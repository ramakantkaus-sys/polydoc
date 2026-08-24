"""The :class:`Document` root node and its metadata.

:class:`Document` is what every reader returns and every writer consumes. It is a
:class:`~polydoc.model.base.Node` like everything else, so traversal, selectors and
serialisation all work on it uniformly.

The convenience methods (:meth:`Document.find`, :meth:`Document.replace_text`,
:meth:`Document.save`) import their implementations lazily. That keeps this module
free of backend imports, so building a document in memory never touches PyMuPDF.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Sequence, Union

from .base import Node
from .blocks import (
    Block,
    Container,
    Footnote,
    Heading,
    Image,
    Page,
    Section,
    Slide,
    Table,
    blocks_text,
)
from .geometry import PageGeometry

if TYPE_CHECKING:  # pragma: no cover
    from ..edit.selector import Selector

__all__ = ["Document", "Metadata"]


@dataclass
class Metadata:
    """Document-level metadata, mapped onto each format's native properties."""

    title: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    subject: Optional[str] = None
    keywords: List[str] = field(default_factory=list)
    description: Optional[str] = None
    language: Optional[str] = None
    category: Optional[str] = None
    created: Optional[datetime] = None
    modified: Optional[datetime] = None
    producer: Optional[str] = None
    #: Anything format-specific that has no universal equivalent.
    custom: Dict[str, Any] = field(default_factory=dict)

    @property
    def author(self) -> Optional[str]:
        """The primary author, or all of them joined -- most formats expose one field."""
        return ", ".join(self.authors) if self.authors else None

    @author.setter
    def author(self, value: Optional[str]) -> None:
        self.authors = [] if not value else [part.strip() for part in value.split(",") if part.strip()]

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, value in asdict(self).items():
            if value in (None, [], {}):
                continue
            out[key] = value.isoformat() if isinstance(value, datetime) else value
        return out

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Metadata":
        if not data:
            return cls()
        known = {f.name for f in fields(cls)}
        kwargs: Dict[str, Any] = {}
        for key, value in data.items():
            if key not in known:
                continue
            if key in ("created", "modified") and isinstance(value, str):
                try:
                    value = datetime.fromisoformat(value)
                except ValueError:
                    value = None
            kwargs[key] = value
        return cls(**kwargs)


@dataclass
class Document(Node):
    """A complete document: metadata plus a list of top-level blocks.

    >>> doc = Document()
    >>> doc.append(Heading.of("Title"), Paragraph.of("Body text."))
    >>> doc.text
    'Title\\nBody text.'
    """

    body: List[Block] = field(default_factory=list)
    metadata: Metadata = field(default_factory=Metadata)
    #: Binary assets (images, fonts) keyed by an id referenced from ``src`` fields.
    resources: Dict[str, bytes] = field(default_factory=dict)
    footnotes: List[Footnote] = field(default_factory=list)
    #: Default page setup for writers that need one.
    geometry: Optional[PageGeometry] = None
    #: Format the document was read from, e.g. ``"pdf"``. ``None`` when built in memory.
    source_format: Optional[str] = None
    source_path: Optional[str] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.adopt(*self.body, *self.footnotes)

    # -- traversal ------------------------------------------------------------
    def children(self) -> List[Node]:
        # Metadata is not a Node, so the default field scan already skips it; we
        # override only to guarantee document order of body then footnotes.
        return [*self.body, *self.footnotes]

    def __iter__(self) -> Iterator[Block]:
        return iter(self.body)

    def __len__(self) -> int:
        return len(self.body)

    @property
    def text(self) -> str:
        """Plain-text rendering of the whole document."""
        return blocks_text(self.body)

    def blocks(self, recursive: bool = True) -> Iterator[Block]:
        """Iterate blocks in document order.

        With ``recursive=False`` only top-level blocks are yielded.
        """
        if not recursive:
            yield from self.body
            return
        for node in self.walk(include_self=False):
            if isinstance(node, Block):
                yield node

    # -- structure ------------------------------------------------------------
    @property
    def pages(self) -> List[Page]:
        """Top-level :class:`Page` blocks, when the source was paginated."""
        return [b for b in self.body if isinstance(b, Page)]

    @property
    def slides(self) -> List[Slide]:
        """Top-level :class:`Slide` blocks, when the source was a presentation."""
        return [b for b in self.body if isinstance(b, Slide)]

    @property
    def sheets(self) -> List[Container]:
        """Containers tagged as spreadsheet sheets."""
        return [b for b in self.body if isinstance(b, Container) and b.role == "sheet"]

    @property
    def is_paginated(self) -> bool:
        return bool(self.pages or self.slides)

    @property
    def page_count(self) -> int:
        return len(self.pages) or len(self.slides) or 1

    @property
    def headings(self) -> List[Heading]:
        return [b for b in self.blocks() if isinstance(b, Heading)]

    @property
    def tables(self) -> List[Table]:
        return [b for b in self.blocks() if isinstance(b, Table)]

    @property
    def images(self) -> List[Image]:
        return [b for b in self.blocks() if isinstance(b, Image)]

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    # -- mutation -------------------------------------------------------------
    def append(self, *blocks: Block) -> "Document":
        """Add blocks to the end of the body. Chainable."""
        for block in blocks:
            self.body.append(block)
            block.parent = self
        return self

    def insert(self, index: int, *blocks: Block) -> "Document":
        """Insert blocks at ``index``. Chainable."""
        self.body[index:index] = list(blocks)
        self.adopt(*blocks)
        return self

    def extend(self, blocks: Sequence[Block]) -> "Document":
        return self.append(*blocks)

    def remove(self, block: Block) -> "Document":
        """Remove a block from anywhere in the tree."""
        block.detach()
        return self

    def clear(self) -> "Document":
        self.body.clear()
        return self

    # -- querying and editing (lazy imports keep this module backend-free) -----
    def find(self, selector: Union[str, "Selector"]) -> Optional[Node]:
        """First node matching a selector, or ``None``.

        >>> Document([Heading.of("A")]).find("heading").text
        'A'
        """
        from ..edit.selector import select_one

        return select_one(self, selector)

    def find_all(self, selector: Union[str, "Selector"]) -> List[Node]:
        """Every node matching a selector, in document order.

        >>> len(Document([Heading.of("A"), Paragraph.of("b")]).find_all("heading, paragraph"))
        2
        """
        from ..edit.selector import select

        return select(self, selector)

    def replace_text(
        self,
        pattern: str,
        replacement: str,
        regex: bool = False,
        count: int = 0,
        selector: Optional[str] = None,
        ignore_case: bool = False,
    ) -> int:
        """Replace text while preserving character formatting.

        Returns the number of replacements made. See
        :func:`polydoc.edit.replace_text` for the full description.
        """
        from ..edit.text import replace_text

        return replace_text(
            self,
            pattern,
            replacement,
            regex=regex,
            count=count,
            selector=selector,
            ignore_case=ignore_case,
        )

    def outline(self, max_level: int = 6) -> List[Section]:
        """Derive a nested section tree from the heading sequence."""
        from ..intelligence.structure import build_sections

        return build_sections(self.body, max_level=max_level)

    def apply(self, *transforms: Any) -> "Document":
        """Run one or more callables of the form ``fn(document) -> None | Document``."""
        result = self
        for transform in transforms:
            returned = transform(result)
            if isinstance(returned, Document):
                result = returned
        return result

    # -- I/O (lazy imports avoid a model -> io cycle) --------------------------
    def save(
        self,
        path: Any,
        format: Optional[str] = None,
        **options: Any,
    ) -> Any:
        """Write this document to ``path``.

        The format is inferred from the file extension unless given explicitly.
        """
        from ..api import save

        return save(self, path, format=format, **options)

    def to_bytes(self, format: str, **options: Any) -> bytes:
        """Serialise to bytes in ``format`` without touching the filesystem."""
        from ..api import dumps

        return dumps(self, format, **options)

    def to_text(self, format: str = "markdown", **options: Any) -> str:
        """Serialise to a string in a text-based format."""
        data = self.to_bytes(format, **options)
        return data.decode("utf-8")

    # -- serialisation --------------------------------------------------------
    def to_dict(self, include_ids: bool = False) -> Dict[str, Any]:
        data = super().to_dict(include_ids=include_ids)
        # Metadata is not a Node; encode it explicitly and drop it when empty.
        meta = self.metadata.to_dict()
        if meta:
            data["metadata"] = meta
        else:
            data.pop("metadata", None)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Document":
        node = Node.from_dict({**data, "type": data.get("type", cls.type_name())})
        if not isinstance(node, Document):
            raise TypeError(f"Payload does not describe a Document (got {node.type!r})")
        return node

    def summary(self) -> Dict[str, Any]:
        """A compact profile of the document, useful for logging and CLI output."""
        counts: Dict[str, int] = {}
        for block in self.blocks():
            counts[block.type] = counts.get(block.type, 0) + 1
        return {
            "title": self.metadata.title,
            "source_format": self.source_format,
            "blocks": len(self.body),
            "words": self.word_count,
            "pages": self.page_count,
            "block_counts": counts,
        }

    def __repr__(self) -> str:
        title = self.metadata.title or self.source_path or "untitled"
        return (
            f"Document({title!r}, blocks={len(self.body)}, "
            f"words={self.word_count}, format={self.source_format!r})"
        )
