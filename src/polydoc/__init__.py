"""polydoc -- one editable document model for every format.

Most document libraries in Python are one-way: they extract a PDF or a DOCX into text
or Markdown for something else to consume. polydoc keeps a *mutable* model in the
middle, so reading, editing, and writing are the same pipeline::

    import polydoc

    doc = polydoc.open("quarterly.pdf")       # PDF -> universal model
    doc.replace_text("FY2024", "FY2025")       # edit, preserving formatting
    doc.save("quarterly.docx")                 # model -> DOCX

Or in a single call::

    polydoc.convert("slides.pptx", "notes.md")

The pieces:

* :mod:`polydoc.model` -- the universal document model.
* :mod:`polydoc.formats` -- readers and writers, plus the registry you extend.
* :mod:`polydoc.edit` -- selectors and formatting-preserving editing operations.
* :mod:`polydoc.intelligence` -- structure inference (headings, reading order, tables).
"""

from __future__ import annotations

from .api import (
    convert,
    convert_bytes,
    detect,
    dumps,
    formats,
    loads,
    open,
    open_document,
    save,
    supported_formats,
)
from .exceptions import (
    DocumentTooLargeError,
    EditError,
    FormatDetectionError,
    MissingDependencyError,
    ParseError,
    PolydocError,
    SelectorError,
    UnsupportedFormatError,
    WriteError,
)
from .formats.limits import Limits, get_default_limits, set_default_limits
from .model import (
    Alignment,
    BBox,
    Block,
    CodeBlock,
    Color,
    Container,
    Document,
    Heading,
    HorizontalRule,
    Image,
    Inline,
    InlineImage,
    LineBreak,
    Link,
    ListBlock,
    ListItem,
    ListStyle,
    Metadata,
    Page,
    PageBreak,
    PageGeometry,
    Paragraph,
    ParagraphStyle,
    Quote,
    Section,
    Slide,
    Table,
    TableCell,
    TableRow,
    Text,
    TextStyle,
    plain,
)

__version__ = "0.1.2"

__all__ = [
    # API
    "convert",
    "convert_bytes",
    "detect",
    "dumps",
    "formats",
    "loads",
    "open",
    "open_document",
    "save",
    "supported_formats",
    # Model
    "Alignment",
    "BBox",
    "Block",
    "CodeBlock",
    "Color",
    "Container",
    "Document",
    "Heading",
    "HorizontalRule",
    "Image",
    "Inline",
    "InlineImage",
    "LineBreak",
    "Link",
    "ListBlock",
    "ListItem",
    "ListStyle",
    "Metadata",
    "Page",
    "PageBreak",
    "PageGeometry",
    "Paragraph",
    "ParagraphStyle",
    "Quote",
    "Section",
    "Slide",
    "Table",
    "TableCell",
    "TableRow",
    "Text",
    "TextStyle",
    "plain",
    # Resource limits for untrusted input
    "Limits",
    "get_default_limits",
    "set_default_limits",
    # Errors
    "DocumentTooLargeError",
    "EditError",
    "FormatDetectionError",
    "MissingDependencyError",
    "ParseError",
    "PolydocError",
    "SelectorError",
    "UnsupportedFormatError",
    "WriteError",
    "__version__",
]
