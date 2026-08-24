"""Inline (character-level) content.

Inline content is a flat-ish sequence of runs. A paragraph's ``content`` is a list of
:class:`Inline` nodes; formatting lives on :class:`Text.style` rather than in nested
markup, which keeps the common case (find/replace, restyle) simple. The one nesting
case worth keeping is :class:`Link`, because a hyperlink genuinely wraps a span.

:func:`merge_runs` is the normaliser that keeps documents tidy: after an edit splits
a run into three pieces, it collapses any neighbours that share a style.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

from .base import Node
from .style import TextStyle

__all__ = [
    "DynamicField",
    "FootnoteRef",
    "Inline",
    "InlineImage",
    "LineBreak",
    "Link",
    "Math",
    "Text",
    "inline_text",
    "iter_text_nodes",
    "merge_runs",
    "plain",
]


class Inline(Node):
    """Base class for inline content. Not instantiable on its own."""


@dataclass
class Text(Inline):
    """A run of text sharing one :class:`TextStyle`.

    Note the ``text`` dataclass field intentionally shadows :attr:`Node.text`; for a
    leaf run the stored string *is* its plain-text rendering.
    """

    text: str = ""
    style: TextStyle = field(default_factory=TextStyle)

    def __len__(self) -> int:
        return len(self.text)

    def __bool__(self) -> bool:
        return bool(self.text)

    def split_at(self, index: int) -> Tuple["Text", "Text"]:
        """Split into two runs at ``index``, both keeping this run's style.

        >>> left, right = Text("hello world").split_at(5)
        >>> left.text, right.text
        ('hello', ' world')
        """
        index = max(0, min(len(self.text), index))
        return (
            Text(self.text[:index], self.style),
            Text(self.text[index:], self.style),
        )

    def slice(self, start: int, end: Optional[int] = None) -> "Text":
        """A new run holding a substring, with the style preserved."""
        return Text(self.text[start:end], self.style)

    def restyle(self, **changes: object) -> "Text":
        """Return a copy with individual style attributes overridden."""
        from dataclasses import replace as _replace

        return Text(self.text, _replace(self.style, **changes))  # type: ignore[arg-type]


@dataclass
class Link(Inline):
    """A hyperlink wrapping inline content."""

    content: List[Inline] = field(default_factory=list)
    href: str = ""
    title: Optional[str] = None

    def __post_init__(self) -> None:
        self.adopt(*self.content)

    @property
    def text(self) -> str:
        return inline_text(self.content)


@dataclass
class LineBreak(Inline):
    """A hard line break inside a block (``<br>``, Shift+Enter in Word)."""

    @property
    def text(self) -> str:
        return "\n"


@dataclass
class InlineImage(Inline):
    """An image positioned within a line of text."""

    src: Optional[str] = None
    alt: str = ""
    data: Optional[bytes] = None
    width: Optional[float] = None
    height: Optional[float] = None
    mime_type: Optional[str] = None

    @property
    def text(self) -> str:
        return self.alt


@dataclass
class Math(Inline):
    """A mathematical expression, stored as LaTeX."""

    latex: str = ""
    display: bool = False

    @property
    def text(self) -> str:
        return self.latex


@dataclass
class FootnoteRef(Inline):
    """A reference marker pointing at a footnote or endnote."""

    identifier: str = ""
    label: Optional[str] = None

    @property
    def text(self) -> str:
        return self.label or f"[{self.identifier}]"


@dataclass
class DynamicField(Inline):
    """A value the renderer computes: page number, date, slide title, and friends."""

    TYPE = "field"

    kind: str = "page-number"
    #: What to show when the target format has no live-field equivalent.
    fallback: str = ""
    style: TextStyle = field(default_factory=TextStyle)

    @property
    def text(self) -> str:
        return self.fallback


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def plain(text: str, **style: object) -> List[Inline]:
    """Build inline content from a plain string.

    >>> plain("hi", bold=True)
    [Text(text='hi', style=TextStyle(bold=True))]
    """
    return [Text(text, TextStyle(**style))] if text else []  # type: ignore[arg-type]


def inline_text(content: Sequence[Inline]) -> str:
    """Concatenate the plain text of an inline sequence."""
    return "".join(node.text for node in content)


def iter_text_nodes(content: Iterable[Inline]) -> List[Text]:
    """Collect every :class:`Text` leaf in document order, descending into links.

    This flat view is what the style-preserving replace engine operates on.
    """
    found: List[Text] = []
    for node in content:
        if isinstance(node, Text):
            found.append(node)
        elif isinstance(node, Link):
            found.extend(iter_text_nodes(node.content))
    return found


def merge_runs(content: Sequence[Inline]) -> List[Inline]:
    """Collapse adjacent same-style text runs and drop empty ones.

    >>> merge_runs([Text("a"), Text("b"), Text("c", TextStyle(bold=True))])
    [Text(text='ab'), Text(text='c', style=TextStyle(bold=True))]
    """
    out: List[Inline] = []
    for node in content:
        if isinstance(node, Text):
            if not node.text:
                continue
            if out and isinstance(out[-1], Text) and out[-1].style == node.style:
                out[-1] = Text(out[-1].text + node.text, node.style)
                continue
        elif isinstance(node, Link):
            node.content = merge_runs(node.content)
            node.adopt(*node.content)
            if not node.content:
                continue
        out.append(node)
    return out
