"""polydoc's universal document model.

This package is the whole point of the library: one representation that every format
reads into and every format writes out of. Nothing here imports a backend, so the
model stays cheap to construct and easy to test.

Quick map:

* :class:`Document` -- the root, holding ``body`` blocks plus :class:`Metadata`.
* Block content -- :class:`Heading`, :class:`Paragraph`, :class:`Table`,
  :class:`ListBlock`, :class:`Image`, :class:`CodeBlock`, :class:`Page`,
  :class:`Slide`, and friends.
* Inline content -- :class:`Text` runs carrying a :class:`TextStyle`, plus
  :class:`Link`, :class:`LineBreak`, :class:`InlineImage`, :class:`Math`.
* Styling -- sparse :class:`TextStyle` / :class:`ParagraphStyle` where ``None``
  means "inherit".
* Geometry -- :class:`BBox` and :class:`PageGeometry` for page-based formats.
"""

from .base import NODE_REGISTRY, Node, register_node
from .blocks import (
    Block,
    BlockContainer,
    CodeBlock,
    Container,
    Footnote,
    Heading,
    HorizontalRule,
    Image,
    ListBlock,
    ListItem,
    Page,
    PageBreak,
    Paragraph,
    Quote,
    Section,
    Slide,
    Table,
    TableCell,
    TableRow,
    blocks_text,
)
from .document import Document, Metadata
from .geometry import BBox, PageGeometry, Point, Size
from .inline import (
    DynamicField,
    FootnoteRef,
    Inline,
    InlineImage,
    LineBreak,
    Link,
    Math,
    Text,
    inline_text,
    iter_text_nodes,
    merge_runs,
    plain,
)
from .style import Alignment, Color, ListStyle, ParagraphStyle, TextStyle, VerticalAlign

__all__ = [
    "Alignment",
    "BBox",
    "Block",
    "BlockContainer",
    "CodeBlock",
    "Color",
    "Container",
    "Document",
    "DynamicField",
    "Footnote",
    "FootnoteRef",
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
    "Math",
    "Metadata",
    "NODE_REGISTRY",
    "Node",
    "Page",
    "PageBreak",
    "PageGeometry",
    "Paragraph",
    "ParagraphStyle",
    "Point",
    "Quote",
    "Section",
    "Size",
    "Slide",
    "Table",
    "TableCell",
    "TableRow",
    "Text",
    "TextStyle",
    "VerticalAlign",
    "blocks_text",
    "inline_text",
    "iter_text_nodes",
    "merge_runs",
    "plain",
    "register_node",
]
