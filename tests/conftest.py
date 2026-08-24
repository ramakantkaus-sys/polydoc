"""Shared fixtures.

Backend-dependent tests are skipped rather than failed when an optional package is
absent, so the suite is meaningful on a minimal install (``pip install polydoc``) as
well as a full one (``pip install polydoc[all]``).
"""

from __future__ import annotations

import importlib.util
from typing import List

import pytest

from polydoc.model import (
    Alignment,
    CodeBlock,
    Document,
    Heading,
    HorizontalRule,
    Image,
    Link,
    ListBlock,
    ListItem,
    ListStyle,
    Metadata,
    PageBreak,
    Paragraph,
    Quote,
    Table,
    Text,
    TextStyle,
    plain,
)


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


#: Skip markers for each optional backend.
needs_docx = pytest.mark.skipif(not _installed("docx"), reason="python-docx not installed")
needs_pptx = pytest.mark.skipif(not _installed("pptx"), reason="python-pptx not installed")
needs_xlsx = pytest.mark.skipif(not _installed("openpyxl"), reason="openpyxl not installed")
needs_html = pytest.mark.skipif(not _installed("bs4"), reason="beautifulsoup4 not installed")
needs_markdown = pytest.mark.skipif(
    not _installed("markdown_it"), reason="markdown-it-py not installed"
)
needs_pdf_read = pytest.mark.skipif(
    not (_installed("pymupdf") or _installed("fitz")), reason="PyMuPDF not installed"
)
needs_pdf_write = pytest.mark.skipif(
    not _installed("reportlab"), reason="reportlab not installed"
)


@pytest.fixture
def sample_document() -> Document:
    """A document exercising most of the model, used across format tests."""
    return Document(
        metadata=Metadata(
            title="Quarterly Report",
            authors=["Ada Lovelace"],
            subject="Engineering",
            keywords=["alpha", "beta"],
            language="en",
        ),
        body=[
            Heading.of("Quarterly Report", 1),
            Paragraph(
                [
                    Text("Plain, "),
                    Text("bold", TextStyle(bold=True)),
                    Text(", "),
                    Text("italic", TextStyle(italic=True)),
                    Text(", "),
                    Text("mono", TextStyle(code=True)),
                    Text(", and a "),
                    Link(plain("link"), href="https://example.com"),
                    Text("."),
                ]
            ),
            Paragraph.of("Centred text.", alignment=Alignment.CENTER),
            Heading.of("Findings", 2),
            ListBlock(
                [
                    ListItem.of("first"),
                    ListItem(
                        [
                            Paragraph.of("second"),
                            ListBlock(
                                [ListItem.of("nested a"), ListItem.of("nested b")],
                                ListStyle.BULLET,
                            ),
                        ]
                    ),
                    ListItem.of("third"),
                ],
                ListStyle.ORDERED,
            ),
            Heading.of("Data", 2),
            Table.from_rows(
                [
                    ["Component", "Trials", "Rate"],
                    ["Reader", "1200", "99.2%"],
                    ["Writer", "1150", "98.7%"],
                ],
                caption="Measured rates",
            ),
            Quote.of("Correctness first.", "Team motto"),
            CodeBlock("def f(x):\n    return x + 1", "python"),
            HorizontalRule(),
            PageBreak(),
            Heading.of("Appendix", 2),
            Paragraph.of("After the break."),
        ],
    )


@pytest.fixture
def simple_document() -> Document:
    """A minimal document, for tests that only need something valid."""
    return Document(
        metadata=Metadata(title="Simple"),
        body=[Heading.of("Title", 1), Paragraph.of("One paragraph.")],
    )


@pytest.fixture
def template_document() -> Document:
    """A document whose key phrase is deliberately split across styled runs.

    This is the shape that defeats naive find/replace, so several tests depend on the
    exact run boundaries here.
    """
    return Document(
        body=[
            Heading.of("Invoice for {{client}}", 1),
            Paragraph(
                [
                    Text("Period: "),
                    Text("FY", TextStyle(bold=True)),
                    Text("2024", TextStyle(bold=True, italic=True)),
                    Text(" Q3"),
                    Text(" for {{client}}."),
                ]
            ),
            Paragraph(
                [
                    Text("See "),
                    Link(
                        [Text("the FY2024 terms", TextStyle(underline=True))],
                        href="https://example.com/fy2024",
                    ),
                    Text(" for detail."),
                ]
            ),
            Table.from_rows([["Item", "Amount"], ["Consulting", "1000"]]),
        ]
    )


#: Formats whose writer output can be read back by polydoc.
ROUND_TRIP_FORMATS: List[str] = ["markdown", "html", "json", "docx", "pptx", "xlsx", "pdf"]
