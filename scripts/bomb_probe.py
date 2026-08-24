"""Isolated decompression-bomb probe.

Runs each bomb in a child process, because the failure mode under test (unbounded
allocation) kills the interpreter and cannot be caught in-process.

Usage:
    python scripts/bomb_probe.py            # parent: runs each case as a child
    python scripts/bomb_probe.py <case>     # child: runs one case
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import time
import zipfile


def build_zip_bomb(expanded_mb: int) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        with archive.open("word/document.xml", "w") as handle:
            chunk = b"\0" * (1024 * 1024)
            for _ in range(expanded_mb):
                handle.write(chunk)
    return buffer.getvalue()


def build_valid_docx() -> bytes:
    import polydoc

    return polydoc.dumps(polydoc.loads("# T\n\nBody.\n", "markdown"), "docx")


def poison(data: bytes, entry: str, payload: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as original, zipfile.ZipFile(
        out, "w", zipfile.ZIP_DEFLATED
    ) as rebuilt:
        for item in original.infolist():
            content = payload if item.filename == entry else original.read(item.filename)
            rebuilt.writestr(item.filename, content)
    return out.getvalue()


def case_zip_bomb(expanded_mb: int) -> None:
    import polydoc

    data = build_zip_bomb(expanded_mb)
    ratio = (expanded_mb * 1024 * 1024) / max(1, len(data))
    print(f"    archive={len(data):,} B  expands to {expanded_mb} MB  ratio={ratio:,.0f}:1")
    sys.stdout.flush()
    try:
        document = polydoc.open(data, format="docx")
        print(f"    RESULT parsed, {document.word_count} words -- NO SIZE GUARD")
    except Exception as exc:  # noqa: BLE001
        print(f"    RESULT rejected: {type(exc).__name__}: {str(exc)[:70]}")


def case_billion_laughs() -> None:
    import polydoc

    laughs = '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a0 "lol">'
    for index in range(1, 10):
        laughs += f'<!ENTITY a{index} "' + f"&a{index - 1};" * 10 + '">'
    laughs += (
        "]><w:document "
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&a9;</w:t></w:r></w:p></w:body></w:document>"
    )
    poisoned = poison(build_valid_docx(), "word/document.xml", laughs)
    print(f"    poisoned archive={len(poisoned):,} B, declares 10^9 expansion")
    sys.stdout.flush()
    try:
        document = polydoc.open(poisoned, format="docx")
        length = len(document.text)
        print(f"    RESULT parsed, text={length:,} chars"
              + ("  -- EXPANSION NOT BLOCKED" if length > 1_000_000 else "  (expansion blocked)"))
    except Exception as exc:  # noqa: BLE001
        print(f"    RESULT rejected: {type(exc).__name__}: {str(exc)[:70]}")


def case_huge_pdf_pagecount() -> None:
    """A PDF declaring an enormous page count."""
    import polydoc

    header = b"%PDF-1.7\n"
    body = b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    body += b"2 0 obj<</Type/Pages/Count 999999999/Kids[]>>endobj\n"
    data = header + body + b"trailer<</Root 1 0 R>>\n%%EOF\n"
    try:
        document = polydoc.open(data, format="pdf")
        print(f"    RESULT parsed, pages={len(document.pages)}")
    except Exception as exc:  # noqa: BLE001
        print(f"    RESULT rejected: {type(exc).__name__}: {str(exc)[:70]}")


def case_valid_docx_bomb(expanded_mb: int) -> None:
    """The real test: a bomb inside a *structurally valid* DOCX.

    The naive bomb above is rejected for missing ``[Content_Types].xml``, which proves
    nothing about decompression limits. Poisoning one entry of a genuine package is what
    a real attacker would do.
    """
    import polydoc

    payload = "<w:t>" + ("A" * (expanded_mb * 1024 * 1024)) + "</w:t>"
    wrapper = (
        '<?xml version="1.0"?><w:document '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r>" + payload + "</w:r></w:p></w:body></w:document>"
    )
    poisoned = poison(build_valid_docx(), "word/document.xml", wrapper)
    ratio = len(wrapper) / max(1, len(poisoned))
    print(f"    archive={len(poisoned):,} B  entry expands to {expanded_mb} MB  ratio={ratio:,.0f}:1")
    sys.stdout.flush()
    try:
        document = polydoc.open(poisoned, format="docx")
        print(f"    RESULT parsed, {len(document.text):,} chars -- NO DECOMPRESSION GUARD")
    except MemoryError:
        print("    RESULT MemoryError (process survived but allocation failed)")
    except Exception as exc:  # noqa: BLE001
        print(f"    RESULT rejected: {type(exc).__name__}: {str(exc)[:70]}")


def case_valid_xlsx_bomb(expanded_mb: int) -> None:
    """Same idea against a valid XLSX worksheet."""
    import zipfile as _zipfile

    import polydoc
    from polydoc.model import Document, Table

    valid = polydoc.dumps(Document([Table.from_rows([["a"], ["1"]])]), "xlsx")
    with _zipfile.ZipFile(io.BytesIO(valid)) as archive:
        target = next(n for n in archive.namelist() if n.startswith("xl/worksheets/sheet"))

    cells = "".join(
        f'<c r="A{i}" t="inlineStr"><is><t>{"A" * 1024}</t></is></c>'
        for i in range(1, expanded_mb * 1024)
    )
    sheet = (
        '<?xml version="1.0"?><worksheet '
        'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData><row r="1">{cells}</row></sheetData></worksheet>'
    )
    poisoned = poison(valid, target, sheet)
    print(f"    archive={len(poisoned):,} B  entry expands to {len(sheet) / 1e6:.0f} MB")
    sys.stdout.flush()
    try:
        document = polydoc.open(poisoned, format="xlsx")
        print(f"    RESULT parsed, {len(document.text):,} chars -- NO DECOMPRESSION GUARD")
    except MemoryError:
        print("    RESULT MemoryError")
    except Exception as exc:  # noqa: BLE001
        print(f"    RESULT rejected: {type(exc).__name__}: {str(exc)[:70]}")


CASES = {
    "zip-bomb-64": lambda: case_zip_bomb(64),
    "zip-bomb-512": lambda: case_zip_bomb(512),
    "valid-docx-bomb-256": lambda: case_valid_docx_bomb(256),
    "valid-xlsx-bomb-64": lambda: case_valid_xlsx_bomb(64),
    "billion-laughs": case_billion_laughs,
    "pdf-pagecount": case_huge_pdf_pagecount,
}


def child(name: str) -> int:
    CASES[name]()
    return 0


def parent() -> int:
    print("Isolated bomb probe (each case in its own process)\n")
    worst = 0
    for name in CASES:
        print(f"  case: {name}")
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [sys.executable, os.path.abspath(__file__), name],
                capture_output=True,
                text=True,
                timeout=180,
            )
            elapsed = time.perf_counter() - started
            print(completed.stdout.rstrip() or "    (no output)")
            if completed.returncode != 0:
                tail = (completed.stderr or "").strip().splitlines()
                reason = tail[-1][:100] if tail else "no stderr"
                print(f"    *** CHILD DIED rc={completed.returncode} after {elapsed:.1f}s: {reason}")
                worst = max(worst, 2)
            else:
                print(f"    survived in {elapsed:.1f}s")
        except subprocess.TimeoutExpired:
            print("    *** CHILD HUNG past 180s -- unbounded work")
            worst = max(worst, 2)
        print()
    return worst


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in CASES:
        sys.exit(child(sys.argv[1]))
    sys.exit(parent())
