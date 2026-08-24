"""Block-level (structural) content.

Design note on field layout: every concrete block declares its own ``style``,
``bbox`` and ``attrs`` fields rather than inheriting them from :class:`Block`.
Dataclass inheritance places base-class fields *first*, which would turn the natural
``Paragraph(plain("hi"))`` into ``Paragraph(style, bbox, attrs, content)``. Declaring
them per class costs three lines each and buys a clean signature, correct ``__eq__``,
and free serialisation.

``attrs`` is the escape hatch: format-specific data that has no home in the universal
model (a DOCX style id, a PPTX placeholder index) rides along there so a round trip
through polydoc does not silently discard it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from .base import Node
from .geometry import BBox, PageGeometry
from .inline import Inline, inline_text, plain
from .style import Alignment, ListStyle, ParagraphStyle, VerticalAlign  # noqa: F401

__all__ = [
    "Block",
    "BlockContainer",
    "CodeBlock",
    "Container",
    "Footnote",
    "HorizontalRule",
    "Image",
    "ListBlock",
    "ListItem",
    "Page",
    "PageBreak",
    "Paragraph",
    "Quote",
    "Section",
    "Slide",
    "Table",
    "TableCell",
    "TableRow",
    "blocks_text",
]

#: What callers may hand to helpers that accept "some text or some inlines".
InlineSource = Union[str, Sequence[Inline], None]


def _as_inlines(value: InlineSource) -> List[Inline]:
    """Normalise a string / inline sequence / ``None`` into an inline list."""
    if value is None:
        return []
    if isinstance(value, str):
        return plain(value)
    return list(value)


class Block(Node):
    """Base class for block-level content.

    Annotations here are for type checkers and documentation only; because ``Block``
    is not a dataclass they do not become inherited fields.
    """

    style: ParagraphStyle
    bbox: Optional[BBox]
    attrs: Dict[str, Any]

    @property
    def is_container(self) -> bool:
        """True when this block holds other blocks (rather than inline content)."""
        return isinstance(self, BlockContainer)


class BlockContainer(Block):
    """Marker for blocks whose payload is a list of child blocks.

    Writers use this to recurse generically instead of enumerating every type.
    """

    content: List[Block]


# ---------------------------------------------------------------------------
# Text blocks
# ---------------------------------------------------------------------------


@dataclass
class Paragraph(Block):
    """A run of body text."""

    content: List[Inline] = field(default_factory=list)
    style: ParagraphStyle = field(default_factory=ParagraphStyle)
    bbox: Optional[BBox] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.adopt(*self.content)

    @classmethod
    def of(cls, text: InlineSource, **style: Any) -> "Paragraph":
        """Build a paragraph from a string.

        >>> Paragraph.of("Hello", alignment=Alignment.CENTER).text
        'Hello'
        """
        return cls(_as_inlines(text), ParagraphStyle(**style))

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass
class Heading(Block):
    """A section heading. ``level`` is 1-6, mirroring HTML and Markdown."""

    content: List[Inline] = field(default_factory=list)
    level: int = 1
    style: ParagraphStyle = field(default_factory=ParagraphStyle)
    bbox: Optional[BBox] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.level = max(1, min(6, int(self.level)))
        self.adopt(*self.content)

    @classmethod
    def of(cls, text: InlineSource, level: int = 1, **style: Any) -> "Heading":
        return cls(_as_inlines(text), level, ParagraphStyle(**style))


@dataclass
class CodeBlock(Block):
    """A preformatted code listing."""

    code: str = ""
    language: Optional[str] = None
    style: ParagraphStyle = field(default_factory=ParagraphStyle)
    bbox: Optional[BBox] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.code

    @property
    def lines(self) -> List[str]:
        return self.code.splitlines()


@dataclass
class Quote(BlockContainer):
    """A block quotation, optionally attributed."""

    content: List[Block] = field(default_factory=list)
    attribution: Optional[str] = None
    style: ParagraphStyle = field(default_factory=ParagraphStyle)
    bbox: Optional[BBox] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.adopt(*self.content)

    @classmethod
    def of(cls, text: str, attribution: Optional[str] = None) -> "Quote":
        return cls([Paragraph.of(text)], attribution)

    @property
    def text(self) -> str:
        return blocks_text(self.content)


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


@dataclass
class ListItem(BlockContainer):
    """One entry in a :class:`ListBlock`.

    Holds *blocks*, not inlines, so an item can contain several paragraphs or a
    nested list -- which is how real documents behave.
    """

    content: List[Block] = field(default_factory=list)
    marker: Optional[str] = None
    checked: Optional[bool] = None
    bbox: Optional[BBox] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.adopt(*self.content)

    @classmethod
    def of(cls, text: InlineSource, checked: Optional[bool] = None) -> "ListItem":
        return cls([Paragraph(_as_inlines(text))], checked=checked)

    @property
    def text(self) -> str:
        return blocks_text(self.content)

    @property
    def sublists(self) -> List["ListBlock"]:
        return [b for b in self.content if isinstance(b, ListBlock)]


@dataclass
class ListBlock(Block):
    """An ordered or unordered list."""

    items: List[ListItem] = field(default_factory=list)
    marker_style: ListStyle = ListStyle.BULLET
    start: int = 1
    tight: bool = True
    level: int = 0
    style: ParagraphStyle = field(default_factory=ParagraphStyle)
    bbox: Optional[BBox] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.marker_style, ListStyle):
            try:
                self.marker_style = ListStyle(str(self.marker_style))
            except ValueError:
                self.marker_style = ListStyle.BULLET
        self.adopt(*self.items)

    @classmethod
    def of(
        cls,
        items: Iterable[InlineSource],
        ordered: bool = False,
        start: int = 1,
    ) -> "ListBlock":
        """Build a flat list from strings.

        >>> ListBlock.of(["a", "b"], ordered=True).marker_style
        <ListStyle.ORDERED: 'ordered'>
        """
        style = ListStyle.ORDERED if ordered else ListStyle.BULLET
        return cls([ListItem.of(item) for item in items], style, start)

    @property
    def ordered(self) -> bool:
        return self.marker_style.is_ordered

    @property
    def text(self) -> str:
        return "\n".join(item.text for item in self.items)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


@dataclass
class TableCell(BlockContainer):
    """A single table cell. Spans default to 1."""

    content: List[Block] = field(default_factory=list)
    colspan: int = 1
    rowspan: int = 1
    valign: Optional[VerticalAlign] = None
    background: Optional[str] = None
    bbox: Optional[BBox] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.colspan = max(1, int(self.colspan))
        self.rowspan = max(1, int(self.rowspan))
        self.valign = VerticalAlign.coerce(self.valign)
        self.adopt(*self.content)

    @classmethod
    def of(cls, value: Any, **kwargs: Any) -> "TableCell":
        """Build a cell from any scalar; ``None`` becomes an empty cell."""
        if isinstance(value, TableCell):
            return value
        if isinstance(value, Block):
            return cls([value], **kwargs)
        text = "" if value is None else str(value)
        return cls([Paragraph(_as_inlines(text))] if text else [], **kwargs)

    @property
    def text(self) -> str:
        return blocks_text(self.content)


@dataclass
class TableRow(Block):
    """A table row."""

    cells: List[TableCell] = field(default_factory=list)
    is_header: bool = False
    height: Optional[float] = None
    bbox: Optional[BBox] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.adopt(*self.cells)

    @classmethod
    def of(cls, values: Iterable[Any], is_header: bool = False) -> "TableRow":
        return cls([TableCell.of(v) for v in values], is_header)

    @property
    def text(self) -> str:
        return "\t".join(cell.text for cell in self.cells)

    @property
    def span_width(self) -> int:
        """Total column count including spans."""
        return sum(cell.colspan for cell in self.cells)


@dataclass
class Table(Block):
    """A table.

    ``header_rows`` records how many leading rows are headers. It is kept alongside
    :attr:`TableRow.is_header` because some formats express it one way and some the
    other; :meth:`normalise` reconciles them.
    """

    rows: List[TableRow] = field(default_factory=list)
    caption: Optional[str] = None
    header_rows: int = 0
    column_widths: Optional[List[float]] = None
    style: ParagraphStyle = field(default_factory=ParagraphStyle)
    bbox: Optional[BBox] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.adopt(*self.rows)
        self.normalise()

    def normalise(self) -> "Table":
        """Reconcile ``header_rows`` with per-row ``is_header`` flags."""
        flagged = sum(1 for row in self.rows if row.is_header)
        if self.header_rows and not flagged:
            for row in self.rows[: self.header_rows]:
                row.is_header = True
        elif flagged and not self.header_rows:
            leading = 0
            for row in self.rows:
                if not row.is_header:
                    break
                leading += 1
            self.header_rows = leading
        return self

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[Iterable[Any]],
        header: bool = True,
        caption: Optional[str] = None,
    ) -> "Table":
        """Build a table from a sequence of sequences.

        >>> t = Table.from_rows([["Name", "Qty"], ["Bolt", 4]])
        >>> t.dimensions
        (2, 2)
        """
        built = [TableRow.of(row) for row in rows]
        if header and built:
            built[0].is_header = True
        return cls(built, caption=caption, header_rows=1 if header and built else 0)

    @property
    def dimensions(self) -> "tuple[int, int]":
        """``(row_count, column_count)`` where columns account for spans."""
        return (len(self.rows), max((row.span_width for row in self.rows), default=0))

    @property
    def header(self) -> List[TableRow]:
        return [row for row in self.rows if row.is_header]

    @property
    def body(self) -> List[TableRow]:
        return [row for row in self.rows if not row.is_header]

    @property
    def text(self) -> str:
        return "\n".join(row.text for row in self.rows)

    def cell(self, row: int, col: int) -> Optional[TableCell]:
        """Cell at a logical position, or ``None`` when out of range."""
        if not 0 <= row < len(self.rows):
            return None
        cells = self.rows[row].cells
        return cells[col] if 0 <= col < len(cells) else None

    def to_matrix(self) -> List[List[str]]:
        """Plain-text view, handy for exporting to CSV or a DataFrame."""
        return [[cell.text for cell in row.cells] for row in self.rows]


# ---------------------------------------------------------------------------
# Media and separators
# ---------------------------------------------------------------------------


@dataclass
class Image(Block):
    """A block-level image.

    Carries either a reference (:attr:`src`) or the bytes themselves (:attr:`data`),
    so a document stays self-contained when read from a zip-based format.
    """

    src: Optional[str] = None
    data: Optional[bytes] = None
    alt: str = ""
    caption: Optional[str] = None
    width: Optional[float] = None
    height: Optional[float] = None
    mime_type: Optional[str] = None
    style: ParagraphStyle = field(default_factory=ParagraphStyle)
    bbox: Optional[BBox] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.caption or self.alt

    @property
    def is_embedded(self) -> bool:
        return self.data is not None


@dataclass
class HorizontalRule(Block):
    """A thematic break."""

    style: ParagraphStyle = field(default_factory=ParagraphStyle)
    bbox: Optional[BBox] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return ""


@dataclass
class PageBreak(Block):
    """An explicit page break."""

    attrs: Dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return ""


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


@dataclass
class Container(BlockContainer):
    """A generic grouping of blocks tagged with a ``role``.

    Used for things the universal model should preserve but not over-specify:
    ``header``, ``footer``, ``textbox``, ``sheet``, ``column``, ``aside``.
    """

    content: List[Block] = field(default_factory=list)
    role: str = "group"
    name: Optional[str] = None
    style: ParagraphStyle = field(default_factory=ParagraphStyle)
    bbox: Optional[BBox] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.adopt(*self.content)

    @property
    def text(self) -> str:
        return blocks_text(self.content)


@dataclass
class Section(BlockContainer):
    """A heading plus everything under it.

    Not produced by readers directly -- :func:`polydoc.intelligence.build_sections`
    derives it from a flat heading sequence, giving you a navigable outline.
    """

    title: List[Inline] = field(default_factory=list)
    content: List[Block] = field(default_factory=list)
    level: int = 1
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.adopt(*self.title, *self.content)

    @property
    def title_text(self) -> str:
        return inline_text(self.title)

    @property
    def text(self) -> str:
        parts = [self.title_text] if self.title else []
        parts.append(blocks_text(self.content))
        return "\n".join(p for p in parts if p)

    @property
    def subsections(self) -> List["Section"]:
        return [b for b in self.content if isinstance(b, Section)]


@dataclass
class Page(BlockContainer):
    """One physical page, as read from a paginated format."""

    content: List[Block] = field(default_factory=list)
    number: Optional[int] = None
    geometry: Optional[PageGeometry] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.adopt(*self.content)

    @property
    def text(self) -> str:
        return blocks_text(self.content)


@dataclass
class Slide(BlockContainer):
    """One presentation slide."""

    content: List[Block] = field(default_factory=list)
    title: Optional[str] = None
    layout: Optional[str] = None
    notes: Optional[str] = None
    index: Optional[int] = None
    geometry: Optional[PageGeometry] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.adopt(*self.content)

    @property
    def text(self) -> str:
        return blocks_text(self.content)

    @property
    def heading(self) -> Optional[str]:
        """The explicit title, else the first heading found in the body."""
        if self.title:
            return self.title
        for block in self.walk(include_self=False):
            if isinstance(block, Heading):
                return block.text
        return None


@dataclass
class Footnote(BlockContainer):
    """A footnote or endnote body, referenced by :class:`~polydoc.model.FootnoteRef`."""

    identifier: str = ""
    content: List[Block] = field(default_factory=list)
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.adopt(*self.content)

    @property
    def text(self) -> str:
        return blocks_text(self.content)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def blocks_text(blocks: Sequence[Block], separator: str = "\n") -> str:
    """Join the plain text of a block sequence, dropping empties."""
    return separator.join(part for part in (block.text for block in blocks) if part)
