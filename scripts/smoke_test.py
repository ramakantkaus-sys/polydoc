#!/usr/bin/env python3
"""Smoke test for an *installed* polydoc.

The pytest suite runs against the source tree. This script deliberately does not, so it
catches the class of bug that only appears in a real installation:

* a module missing from the wheel,
* a data file (``py.typed``) not packaged,
* an undeclared dependency that happens to be present in the development environment.

That last one is not hypothetical -- it is how the ``linkify-it-py`` dependency bug in
the Markdown reader was found.

Run it from a directory that is *not* the source tree::

    python -m venv /tmp/verify
    /tmp/verify/bin/pip install "dist/polydoc-0.1.0-py3-none-any.whl[all]"
    cd /tmp && /tmp/verify/bin/python path/to/scripts/smoke_test.py

Exits non-zero if any check fails.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import polydoc
from polydoc.edit import Pipeline, find_text, replace_text, style_text
from polydoc.model import (
    Document,
    Heading,
    ListBlock,
    ListItem,
    ListStyle,
    Metadata,
    Paragraph,
    Table,
    Text,
    TextStyle,
)

failures: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'ok  ' if condition else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    if not condition:
        failures.append(label)


def available(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


def build_document() -> Document:
    doc = Document(
        metadata=Metadata(title="Report for {{client}}", authors=["Ada Lovelace"])
    )
    doc.append(
        Heading.of("Report for {{client}}", 1),
        # The phrase below straddles three differently-styled runs on purpose.
        Paragraph([
            Text("Period: "),
            Text("FY", TextStyle(bold=True)),
            Text("2024", TextStyle(bold=True, italic=True)),
            Text(" Q3."),
        ]),
        Heading.of("Checks", 2),
        ListBlock(
            [
                ListItem.of("read every format"),
                ListItem([
                    Paragraph.of("write every format"),
                    ListBlock([ListItem.of("binary"), ListItem.of("text")], ListStyle.BULLET),
                ]),
            ],
            ListStyle.ORDERED,
        ),
        Table.from_rows(
            [["Format", "Read", "Write"], ["PDF", "yes", "yes"], ["DOCX", "yes", "yes"]],
            caption="Support matrix",
        ),
    )
    return doc


def main() -> int:
    location = Path(polydoc.__file__).parent
    print(f"polydoc {polydoc.__version__}")
    print(f"  installed at {location}")
    print(f"  running from {Path.cwd()}\n")

    if (Path.cwd() / "src" / "polydoc").exists():
        print("WARNING: the source tree is importable from here, which weakens this test.\n")

    print("1. Registry")
    check("readable formats registered", len(polydoc.formats("read")) >= 5,
          ", ".join(polydoc.formats("read")))
    check("writable formats registered", len(polydoc.formats("write")) >= 5,
          ", ".join(polydoc.formats("write")))

    print("\n2. Model")
    doc = build_document()
    check("document built", doc.word_count > 10, f"{doc.word_count} words")
    check("outline derived", len(doc.outline()) >= 1)

    print("\n3. Formatting-preserving replace across run boundaries")
    paragraph = doc.body[1]
    count = replace_text(doc, "FY2024 Q3", "FY2025 Q1")
    check("replacement applied", count == 1)
    check("text updated", "FY2025 Q1" in paragraph.text, paragraph.text)
    check("bold preserved", any(r.style.bold and "FY2025" in r.text for r in paragraph.content))
    check("metadata updated too", replace_text(doc, "{{client}}", "Acme Ltd") >= 2)
    check("title filled", doc.metadata.title == "Report for Acme Ltd", doc.metadata.title or "")
    check("style_text", style_text(doc, "Acme Ltd", color="#cc0000") >= 1)
    check("find_text", len(find_text(doc, "FY2025")) == 1)

    print("\n4. Selectors")
    check("by type", len(doc.find_all("heading")) == 2)
    check("by attribute", doc.find("heading[level=2]").text == "Checks")
    check("descendant", len(doc.find_all("table table_cell")) == 9)
    check("direct child", len(doc.find_all("table > table_row")) == 3)
    check("nested list", len(doc.find_all("list_block list_block")) == 1)

    backends = {
        "markdown": "markdown_it",
        "html": "bs4",
        "docx": "docx",
        "pptx": "pptx",
        "xlsx": "openpyxl",
        "pdf": "reportlab",
    }
    formats = [
        ("markdown", ".md"), ("html", ".html"), ("txt", ".txt"), ("json", ".json"),
        ("csv", ".csv"), ("docx", ".docx"), ("pptx", ".pptx"), ("xlsx", ".xlsx"),
        ("pdf", ".pdf"),
    ]

    with tempfile.TemporaryDirectory() as workdir:
        out = Path(workdir)

        print("\n5. Write and read back every available format")
        written = {}
        for fmt, extension in formats:
            module = backends.get(fmt)
            if module and not available(module):
                print(f"  [skip] {fmt} ({module} not installed)")
                continue
            try:
                path = doc.save(out / f"report{extension}", format=fmt)
                written[fmt] = path
                check(f"write {fmt}", path.stat().st_size > 0, f"{path.stat().st_size:,} bytes")
            except Exception as exc:
                check(f"write {fmt}", False, f"{type(exc).__name__}: {exc}")

        for fmt, path in written.items():
            try:
                check(f"read back {fmt}", polydoc.open(path).word_count > 0)
            except Exception as exc:
                check(f"read back {fmt}", False, f"{type(exc).__name__}: {exc}")

        print("\n6. Fidelity through the binary formats")
        if "docx" in written:
            rt = polydoc.open(written["docx"])
            check("docx headings", [h.text for h in rt.headings][:1] == ["Report for Acme Ltd"])
            check("docx edit survived", "{{client}}" not in rt.text)
            check("docx table", rt.tables[0].to_matrix()[0] == ["Format", "Read", "Write"])
            check("docx nested list", len(rt.find_all("list_block list_block")) == 1)
            check("docx template metadata not leaked", rt.metadata.author != "python-docx")
        if "pdf" in written and available("pymupdf"):
            rt = polydoc.open(written["pdf"])
            check("pdf headings inferred", bool(rt.headings))
            check("pdf table recovered", bool(rt.tables))
        if "xlsx" in written:
            check("xlsx sheets", bool(polydoc.open(written["xlsx"]).sheets))

        print("\n7. Lossless JSON round trip")
        if "json" in written:
            check("json identical", polydoc.open(written["json"]).body == doc.body)

        print("\n8. Conversion matrix")
        sources = {"csv": "name,qty\nBolt,4\n", "txt": "TITLE\n\nBody text.\n"}
        if available("markdown_it"):
            sources["markdown"] = "# T\n\nBody **text**.\n\n| a | b |\n| - | - |\n| 1 | 2 |\n"
        if available("bs4"):
            sources["html"] = "<h1>T</h1><p>Body <b>text</b>.</p>"

        pairs = ok = 0
        for source_format, payload in sources.items():
            loaded = polydoc.loads(payload, source_format)
            for target_format, _ in formats:
                module = backends.get(target_format)
                if module and not available(module):
                    continue
                pairs += 1
                try:
                    if polydoc.dumps(loaded, target_format):
                        ok += 1
                except Exception as exc:
                    print(f"      {source_format} -> {target_format}: {exc}")
        check("all conversion pairs succeed", ok == pairs, f"{ok}/{pairs}")

        print("\n9. Pipeline through convert()")
        template = out / "template.md"
        template.write_text("# Offer for {{name}}\n\nDear {{name}}.\n", encoding="utf-8")
        target_format = "docx" if available("docx") else "txt"
        extension = ".docx" if target_format == "docx" else ".txt"
        if available("markdown_it"):
            polydoc.convert(
                template,
                out / f"offer{extension}",
                transform=Pipeline().replace("{{name}}", "Grace Hopper"),
            )
            result = polydoc.open(out / f"offer{extension}")
            check("transform applied", "Grace Hopper" in result.text)
        else:
            print("  [skip] needs the markdown reader")

        print("\n10. Format detection")
        for fmt, path in written.items():
            if fmt in ("txt", "csv"):
                continue  # ambiguous by design
            detected = polydoc.detect(path)
            check(f"detect {fmt}", detected == fmt, f"got {detected}")

    print("\n11. Packaging")
    check("py.typed shipped", (location / "py.typed").exists())
    check("all subpackages present", all(
        (location / name).is_dir() for name in ("model", "formats", "edit", "intelligence")
    ))

    print("\n" + "=" * 62)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
