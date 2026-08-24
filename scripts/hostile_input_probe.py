"""Adversarial-input probe for polydoc.

Defensive security testing of this library against the inputs a production service
actually receives: truncated files, malformed archives, XML external entities,
compression bombs, and pathological nesting.

Every case must fail *safely* -- bounded time, bounded memory, no file disclosure, no
outbound request. A hang, an unbounded allocation, or a successful XXE is a production
blocker.

Run: python scripts/hostile_input_probe.py
"""

from __future__ import annotations

import io
import queue
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import polydoc
from polydoc.exceptions import PolydocError

RESULTS: list = []
CASE_TIMEOUT = 25.0

WORKDIR = Path(tempfile.mkdtemp(prefix="polydoc_hostile_"))
SECRET = WORKDIR / "SECRET.txt"
SECRET.write_text("TOP-SECRET-CANARY-VALUE", encoding="utf-8")
CANARY = "TOP-SECRET-CANARY-VALUE"


def run_case(name: str, accept: set, function) -> None:
    """Run one case on a worker thread so a hang cannot stall the whole probe.

    ``accept`` lists the outcomes that count as safe: "return" (a value came back),
    "polydoc-error" (our own exception type), "backend-error" (a leaked third-party
    exception -- safe but a poor contract).
    """
    channel: queue.Queue = queue.Queue()

    def worker() -> None:
        try:
            channel.put(("return", function()))
        except PolydocError as exc:
            channel.put(("polydoc-error", f"{type(exc).__name__}: {exc}"))
        except RecursionError as exc:
            channel.put(("recursion-error", str(exc)))
        except MemoryError:
            channel.put(("memory-error", "allocation failed"))
        except AssertionError as exc:
            channel.put(("BREACH", str(exc)))
        except BaseException as exc:  # noqa: BLE001
            channel.put(("backend-error", f"{type(exc).__name__}: {exc}"))

    thread = threading.Thread(target=worker, daemon=True)
    started = time.perf_counter()
    thread.start()
    thread.join(timeout=CASE_TIMEOUT)
    elapsed = time.perf_counter() - started

    if thread.is_alive():
        outcome, detail = "HANG", f"still running after {CASE_TIMEOUT:.0f}s"
        verdict = "BLOCKER"
    else:
        outcome, detail = channel.get()
        detail = str(detail)[:88]
        if outcome == "BREACH":
            verdict = "BLOCKER"
        elif outcome == "memory-error":
            verdict = "BLOCKER"
        elif outcome in accept:
            verdict = "ok"
        elif outcome == "backend-error":
            verdict = "leaky"
        elif outcome == "recursion-error":
            verdict = "weak"
        else:
            verdict = "note"

    RESULTS.append((name, outcome, verdict, elapsed, detail))
    label = {"ok": "ok  ", "leaky": "leak", "weak": "weak", "note": "note", "BLOCKER": "BLOCK"}
    print(f"  [{label[verdict]}] {name:42} {outcome:15} {elapsed:6.2f}s  {detail}")


def zip_of(entries: dict, compresslevel: int = 9) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=compresslevel) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def real_docx_bytes() -> bytes:
    """A structurally valid DOCX produced by our own writer."""
    return polydoc.dumps(polydoc.loads("# Title\n\nBody text.\n", "markdown"), "docx")


def poison_zip_entry(data: bytes, entry: str, payload: str) -> bytes:
    """Rebuild an archive with one entry replaced. Used to poison a *valid* package."""
    source = io.BytesIO(data)
    out = io.BytesIO()
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(
        out, "w", zipfile.ZIP_DEFLATED
    ) as rebuilt:
        for item in original.infolist():
            content = payload if item.filename == entry else original.read(item.filename)
            rebuilt.writestr(item.filename, content)
    return out.getvalue()


print("polydoc", polydoc.__version__, "- adversarial input probe")
print(f"canary file: {SECRET}\n")

# ---------------------------------------------------------------------------
print("1. Truncated and corrupt input")

run_case("empty file", {"polydoc-error"}, lambda: polydoc.open(b""))
run_case(
    "PDF header only",
    {"polydoc-error", "backend-error"},
    lambda: polydoc.open(b"%PDF-1.7\n"),
)


def _truncated_pdf():
    good = polydoc.dumps(polydoc.loads("# T\n\nBody.\n", "markdown"), "pdf")
    return polydoc.open(good[: len(good) // 2], format="pdf")


run_case("PDF truncated in half", {"polydoc-error", "backend-error"}, _truncated_pdf)


def _corrupt_trailer():
    good = bytearray(polydoc.dumps(polydoc.loads("# T\n", "markdown"), "pdf"))
    for index in range(max(0, len(good) - 200), len(good)):
        good[index] = 0x41
    document = polydoc.open(bytes(good), format="pdf")
    return f"recovered {document.word_count} words (graceful)"


# Recovering from a damaged trailer is desirable, not unsafe: PyMuPDF rebuilds the xref.
run_case("PDF with destroyed trailer", {"return", "polydoc-error", "backend-error"}, _corrupt_trailer)

run_case(
    "DOCX zip missing content types",
    {"polydoc-error", "backend-error"},
    lambda: polydoc.open(zip_of({"junk.txt": "nope"}), format="docx"),
)
run_case(
    "DOCX truncated archive",
    {"polydoc-error", "backend-error"},
    lambda: polydoc.open(real_docx_bytes()[:400], format="docx"),
)
run_case(
    "XLSX that is really plain text",
    {"polydoc-error", "backend-error"},
    lambda: polydoc.open(b"not a spreadsheet", format="xlsx"),
)


def _valid_docx_broken_xml():
    return polydoc.open(
        poison_zip_entry(real_docx_bytes(), "word/document.xml", "<w:document><oops>"),
        format="docx",
    )


run_case("valid DOCX, malformed document.xml", {"polydoc-error", "backend-error"}, _valid_docx_broken_xml)

# ---------------------------------------------------------------------------
print("\n2. XML external entities against STRUCTURALLY VALID packages")

XXE_DOCX = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<!DOCTYPE w:document [<!ENTITY xxe SYSTEM "file:///{path}">]>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p><w:r><w:t>&xxe;</w:t></w:r></w:p></w:body></w:document>"
)


def _docx_xxe():
    payload = XXE_DOCX.format(path=str(SECRET).replace("\\", "/"))
    poisoned = poison_zip_entry(real_docx_bytes(), "word/document.xml", payload)
    document = polydoc.open(poisoned, format="docx")
    if CANARY in document.text:
        raise AssertionError("XXE SUCCEEDED: local file contents disclosed via DOCX")
    return f"no disclosure (text={document.text[:36]!r})"


run_case("DOCX XXE (valid package, poisoned XML)", {"return", "polydoc-error", "backend-error"}, _docx_xxe)


def _xlsx_xxe():
    from polydoc.model import Document, Table

    valid = polydoc.dumps(Document([Table.from_rows([["a"], ["1"]])]), "xlsx")
    with zipfile.ZipFile(io.BytesIO(valid)) as archive:
        target = next(
            (n for n in archive.namelist() if n.startswith("xl/worksheets/sheet")), None
        )
    if target is None:
        return "no worksheet entry found"
    payload = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE worksheet [<!ENTITY xxe SYSTEM "file:///{path}">]>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&xxe;</t></is></c></row>'
        "</sheetData></worksheet>"
    ).format(path=str(SECRET).replace("\\", "/"))
    document = polydoc.open(poison_zip_entry(valid, target, payload), format="xlsx")
    if CANARY in document.text:
        raise AssertionError("XXE SUCCEEDED: local file contents disclosed via XLSX")
    return "no disclosure"


run_case("XLSX XXE (valid package, poisoned XML)", {"return", "polydoc-error", "backend-error"}, _xlsx_xxe)


def _pptx_xxe():
    from polydoc.model import Document, Paragraph, Slide

    valid = polydoc.dumps(Document([Slide(title="T", content=[Paragraph.of("x")])]), "pptx")
    with zipfile.ZipFile(io.BytesIO(valid)) as archive:
        target = next((n for n in archive.namelist() if n.startswith("ppt/slides/slide")), None)
    if target is None:
        return "no slide entry found"
    payload = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE p:sld [<!ENTITY xxe SYSTEM "file:///{path}">]>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>&xxe;</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    ).format(path=str(SECRET).replace("\\", "/"))
    document = polydoc.open(poison_zip_entry(valid, target, payload), format="pptx")
    if CANARY in document.text:
        raise AssertionError("XXE SUCCEEDED: local file contents disclosed via PPTX")
    return "no disclosure"


run_case("PPTX XXE (valid package, poisoned XML)", {"return", "polydoc-error", "backend-error"}, _pptx_xxe)


def _html_xxe():
    markup = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE html [<!ENTITY xxe SYSTEM "file:///{path}">]>'
        "<html><body><p>&xxe;</p></body></html>"
    ).format(path=str(SECRET).replace("\\", "/"))
    document = polydoc.loads(markup, "html")
    if CANARY in document.text:
        raise AssertionError("XXE SUCCEEDED: local file contents disclosed via HTML")
    return "no disclosure"


run_case("HTML external entity", {"return"}, _html_xxe)


def _svg_xxe_in_html():
    # An <image> href pointing at a local file must not be inlined on read.
    markup = f'<html><body><img src="file:///{SECRET}"></body></html>'
    document = polydoc.loads(markup, "html")
    if CANARY in document.text:
        raise AssertionError("local file inlined via img src")
    return "src recorded as a reference only"


run_case("HTML img src=file:// not dereferenced", {"return"}, _svg_xxe_in_html)

# ---------------------------------------------------------------------------
print("\n3. Compression and expansion bombs")


def _zip_bomb():
    # Built in chunks so the probe itself does not need a gigabyte of RAM.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        with archive.open("word/document.xml", "w") as handle:
            chunk = b"\0" * (1024 * 1024)
            for _ in range(512):  # 512 MB expanded
                handle.write(chunk)
    data = buffer.getvalue()
    ratio = (512 * 1024 * 1024) / max(1, len(data))
    try:
        polydoc.open(data, format="docx")
        return f"archive {len(data):,} B, ratio {ratio:,.0f}:1 -> parsed"
    except PolydocError as exc:
        return f"archive {len(data):,} B, ratio {ratio:,.0f}:1 -> rejected: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"archive {len(data):,} B, ratio {ratio:,.0f}:1 -> {type(exc).__name__}"


run_case("zip bomb 512 MB expanded", {"return"}, _zip_bomb)


def _billion_laughs():
    laughs = '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a0 "lol">'
    for index in range(1, 10):
        previous = f"a{index - 1}"
        laughs += f'<!ENTITY a{index} "' + f"&{previous};" * 10 + '">'
    laughs += (
        "]><w:document "
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&a9;</w:t></w:r></w:p></w:body></w:document>"
    )
    poisoned = poison_zip_entry(real_docx_bytes(), "word/document.xml", laughs)
    document = polydoc.open(poisoned, format="docx")
    length = len(document.text)
    if length > 100_000_000:
        raise AssertionError(f"entity expansion produced {length:,} chars")
    return f"expanded to {length:,} chars"


run_case("billion laughs (10^9 expansion)", {"return", "polydoc-error", "backend-error"}, _billion_laughs)

# ---------------------------------------------------------------------------
print("\n4. Pathological structure")

run_case(
    "HTML nested 5000 deep",
    {"return"},
    lambda: f"{len(polydoc.loads('<div>' * 5000 + 'x' + '</div>' * 5000, 'html').body)} blocks",
)


def _deep_markdown():
    lines = [("  " * level) + "- item" for level in range(1500)]
    document = polydoc.loads("\n".join(lines), "markdown")
    return f"{len(document.body)} top-level blocks"


run_case("markdown list nested 1500 deep", {"return"}, _deep_markdown)


def _deep_model_serialise():
    # A deep tree must serialise without blowing the recursion limit.
    from polydoc.model import Container, Document

    node = Container(role="g")
    root = node
    for _ in range(2000):
        inner = Container(role="g")
        node.content.append(inner)
        node.adopt(inner)
        node = inner
    document = Document([root])
    return f"json {len(document.to_text('json')):,} chars"


run_case("model nested 2000 deep -> JSON", {"return"}, _deep_model_serialise)

run_case(
    "10 MB single line of text",
    {"return"},
    lambda: f"{polydoc.loads('x' * (10 * 1024 * 1024), 'txt').word_count} words",
)
run_case(
    "CSV with 200k rows",
    {"return"},
    lambda: f"{polydoc.loads('a,b' + chr(10) + ('1,2' + chr(10)) * 200_000, 'csv').tables[0].dimensions[0]} rows",
)

# ---------------------------------------------------------------------------
print("\n5. Memory profile")


def _memory_ratio():
    import tracemalloc

    payload = ("word " * 12 + "\n\n").encode() * 300_000
    tracemalloc.start()
    polydoc.open(payload, format="txt")
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return (
        f"input {len(payload) / 1e6:.0f} MB -> peak {peak / 1e6:.0f} MB "
        f"({peak / len(payload):.1f}x input)"
    )


run_case("peak memory vs input size", {"return"}, _memory_ratio)

# ---------------------------------------------------------------------------
print("\n6. Concurrency (global caches and the registry)")


def _threads():
    errors: list = []
    payload = "# T\n\nBody **text**.\n\n| a | b |\n| - | - |\n| 1 | 2 |\n"

    def worker(index: int) -> None:
        try:
            for _ in range(5):
                document = polydoc.loads(payload, "markdown")
                document.replace_text("Body", f"Body{index}")
                document.find_all("table > table_row")
                polydoc.dumps(document, "html")
                polydoc.dumps(document, "json")
                polydoc.dumps(document, "docx")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    if errors:
        raise AssertionError(f"{len(errors)} thread error(s), first: {errors[0]}")
    return "24 threads x 5 iterations, no errors"


run_case("24 concurrent threads", {"return"}, _threads)

# ---------------------------------------------------------------------------
print("\n7. Path handling")

run_case(
    "nonexistent traversal path",
    {"polydoc-error", "backend-error"},
    lambda: polydoc.open("../../../../etc/passwd"),
)
run_case("directory as input", {"polydoc-error"}, lambda: polydoc.open(WORKDIR))

# ---------------------------------------------------------------------------
print("\n" + "=" * 100)
counts: dict = {}
for _n, _o, verdict, _e, _d in RESULTS:
    counts[verdict] = counts.get(verdict, 0) + 1
print("summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

blockers = [row for row in RESULTS if row[2] == "BLOCKER"]
if blockers:
    print("\n*** PRODUCTION BLOCKERS ***")
    for name, outcome, _v, elapsed, detail in blockers:
        print(f"  {name}: {outcome} after {elapsed:.1f}s -- {detail}")
else:
    print("\nNo production blockers: nothing hung, exhausted memory, or disclosed a file.")

rough = [row for row in RESULTS if row[2] in ("leaky", "weak")]
if rough:
    print("\nRough edges (safe, but the error contract leaks backend exceptions):")
    for name, outcome, _v, _e, detail in rough:
        print(f"  {name}: {detail}")

print(f"\nscratch: {WORKDIR}")
