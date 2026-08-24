"""Markdown writer.

Aims for output that is both valid GFM and pleasant to read: tables get padded
columns, nested lists get correct indentation, and text that would accidentally
create markup gets escaped.

Escaping is context-sensitive on purpose. Escaping every ``.`` would be safe but
produces unreadable output, so we only escape characters where they could actually
start a construct -- a leading ``#``, a ``|`` inside a table cell, and so on.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ...model import (
    Alignment,
    Block,
    BlockContainer,
    CodeBlock,
    Container,
    Document,
    DynamicField,
    Footnote,
    FootnoteRef,
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
    Math,
    Page,
    PageBreak,
    Paragraph,
    Quote,
    Section,
    Slide,
    Table,
    TableRow,
    Text,
)
from ..base import TextWriter
from ..registry import register_writer

__all__ = ["MarkdownWriter"]

#: Characters that begin inline markup and so need escaping mid-text.
_INLINE_ESCAPE = re.compile(r"([\\`*_\[\]<>])")
#: Constructs that only matter at the start of a line.
_LINE_START_ESCAPE = re.compile(r"^(\s*)([#>|]|[-*+](?=\s)|\d+\.(?=\s))")

_ALIGN_MARKERS = {
    Alignment.LEFT: ":---",
    Alignment.CENTER: ":---:",
    Alignment.RIGHT: "---:",
}


@register_writer
class MarkdownWriter(TextWriter):
    """Renders a document as GitHub-Flavoured Markdown."""

    format = "markdown"
    extensions = (".md", ".markdown", ".mdown", ".mkd")
    aliases = ("md",)
    mime_types = ("text/markdown",)
    description = "GitHub-Flavoured Markdown"

    def render(self, document: Document, **options: Any) -> str:
        renderer = _MarkdownRenderer(
            bullet=options.get("bullet", "-"),
            front_matter=options.get("front_matter", False),
            escape=options.get("escape", True),
            table_padding=options.get("table_padding", True),
            page_breaks=options.get("page_breaks", True),
        )
        return renderer.render(document)


class _MarkdownRenderer:
    def __init__(
        self,
        bullet: str = "-",
        front_matter: bool = False,
        escape: bool = True,
        table_padding: bool = True,
        page_breaks: bool = True,
    ) -> None:
        self.bullet = bullet
        self.front_matter = front_matter
        self.escape = escape
        self.table_padding = table_padding
        self.page_breaks = page_breaks
        self._footnotes: List[Footnote] = []
        #: Disabled while rendering headings, where list markers are inert.
        self._line_start_escape = True

    # -- entry point ----------------------------------------------------------
    def render(self, document: Document) -> str:
        self._footnotes = list(document.footnotes)
        parts: List[str] = []
        if self.front_matter:
            header = self._front_matter(document)
            if header:
                parts.append(header)
        body = self._blocks(document.body)
        if body:
            parts.append(body)
        if self._footnotes:
            parts.append(self._footnote_definitions())
        return "\n\n".join(part for part in parts if part).strip() + "\n"

    def _front_matter(self, document: Document) -> str:
        meta = document.metadata
        lines: List[str] = []
        if meta.title:
            lines.append(f"title: {meta.title}")
        if meta.authors:
            lines.append(f"authors: {', '.join(meta.authors)}")
        if meta.description:
            lines.append(f"description: {meta.description}")
        if meta.keywords:
            lines.append(f"keywords: {', '.join(meta.keywords)}")
        if meta.language:
            lines.append(f"language: {meta.language}")
        if not lines:
            return ""
        return "---\n" + "\n".join(lines) + "\n---"

    # -- blocks ---------------------------------------------------------------
    def _blocks(self, blocks: Sequence[Block], indent: str = "") -> str:
        chunks: List[str] = []
        for block in blocks:
            rendered = self._block(block, indent)
            if rendered:
                chunks.append(rendered)
        return "\n\n".join(chunks)

    def _block(self, block: Block, indent: str = "") -> str:
        if isinstance(block, Heading):
            # Inside a heading, "1." or "-" cannot start a list, so line-start
            # escaping would only add noise.
            self._line_start_escape = False
            try:
                text = self._inlines(block.content)
            finally:
                self._line_start_escape = True
            return f"{indent}{'#' * block.level} {text}"

        if isinstance(block, Paragraph):
            if block.attrs.get("raw_html"):
                return self._prefix(block.text, indent)
            text = self._inlines(block.content)
            return self._prefix(text, indent) if text.strip() else ""

        if isinstance(block, CodeBlock):
            fence = self._fence_for(block.code)
            language = block.language or ""
            body = "\n".join(f"{indent}{line}" for line in block.code.split("\n"))
            return f"{indent}{fence}{language}\n{body}\n{indent}{fence}"

        if isinstance(block, ListBlock):
            return self._list(block, indent)

        if isinstance(block, Table):
            return self._table(block, indent)

        if isinstance(block, Quote):
            inner = self._blocks(block.content)
            quoted = "\n".join(
                f"{indent}> {line}" if line else f"{indent}>" for line in inner.split("\n")
            )
            if block.attribution:
                quoted += f"\n{indent}>\n{indent}> -- {self._text(block.attribution)}"
            return quoted

        if isinstance(block, HorizontalRule):
            return f"{indent}---"

        if isinstance(block, PageBreak):
            # Markdown has no page break. An HTML comment is invisible when rendered,
            # unambiguous next to a real "---" rule, and survives a round trip because
            # the reader preserves raw HTML blocks.
            return f"{indent}<!-- page-break -->" if self.page_breaks else ""

        if isinstance(block, Image):
            return self._prefix(self._image(block), indent)

        if isinstance(block, (Page, Slide)):
            return self._surface(block, indent)

        if isinstance(block, Section):
            parts = []
            if block.title:
                level = max(1, block.level or 1)
                parts.append(f"{indent}{'#' * level} {self._inlines(block.title)}")
            inner = self._blocks(block.content, indent)
            if inner:
                parts.append(inner)
            return "\n\n".join(parts)

        if isinstance(block, Footnote):
            return ""  # emitted in the definitions section

        if isinstance(block, Container):
            return self._container(block, indent)

        if isinstance(block, BlockContainer):
            return self._blocks(block.content, indent)

        # Unknown block: fall back to its plain text rather than dropping it.
        return self._prefix(self._text(block.text), indent) if block.text else ""

    def _surface(self, block: Any, indent: str) -> str:
        """Render a Page or Slide, keeping its identity as a heading or rule."""
        parts: List[str] = []
        if isinstance(block, Slide):
            title = block.title
            if title:
                parts.append(f"{indent}## {self._text(title)}")
        inner = self._blocks(block.content, indent)
        if inner:
            parts.append(inner)
        if isinstance(block, Slide) and block.notes:
            parts.append(f"{indent}> **Notes:** {self._text(block.notes)}")
        return "\n\n".join(part for part in parts if part)

    def _container(self, block: Container, indent: str) -> str:
        parts: List[str] = []
        if block.role == "sheet" and block.name:
            parts.append(f"{indent}## {self._text(block.name)}")
        inner = self._blocks(block.content, indent)
        if inner:
            parts.append(inner)
        return "\n\n".join(part for part in parts if part)

    def _image(self, block: Image) -> str:
        """A block image, with its caption on a following italic line."""
        alt = self._text(block.alt or block.caption or "")
        src = block.src or ""
        title = f' "{block.caption}"' if block.caption and block.alt else ""
        image = f"![{alt}]({src}{title})"
        if block.caption and not block.alt:
            return f"{image}\n\n*{self._text(block.caption)}*"
        return image

    @staticmethod
    def _fence_for(code: str) -> str:
        """Pick a fence long enough to contain any backticks in the code."""
        longest = max((len(m) for m in re.findall(r"`+", code)), default=0)
        return "`" * max(3, longest + 1)

    def _prefix(self, text: str, indent: str) -> str:
        if not indent:
            return text
        return "\n".join(f"{indent}{line}" if line else "" for line in text.split("\n"))

    # -- lists ----------------------------------------------------------------
    def _list(self, block: ListBlock, indent: str) -> str:
        lines: List[str] = []
        number = block.start
        for item in block.items:
            marker = self._marker(block, number)
            number += 1
            # Continuation lines align under the marker, per CommonMark.
            hanging = " " * len(marker)
            rendered = self._item_body(item, indent + hanging)

            checkbox = ""
            if item.checked is not None:
                checkbox = "[x] " if item.checked else "[ ] "

            if rendered:
                first, _, rest = rendered.partition("\n")
                first = first[len(indent) + len(hanging) :] if first.startswith(indent) else first
                lines.append(f"{indent}{marker}{checkbox}{first.lstrip()}")
                if rest:
                    lines.append(rest)
            else:
                lines.append(f"{indent}{marker}{checkbox}".rstrip())
        return "\n".join(lines)

    def _item_body(self, item: ListItem, indent: str) -> str:
        if not item.content:
            return ""
        chunks: List[str] = []
        for index, block in enumerate(item.content):
            if isinstance(block, ListBlock):
                # Nested list: no blank line before it, and indented one level.
                chunks.append(self._list(block, indent))
            else:
                chunks.append(self._block(block, indent))
        # Tight lists keep sub-blocks on consecutive lines; loose ones separate.
        joined: List[str] = []
        for index, chunk in enumerate(chunks):
            if not chunk:
                continue
            separator = "\n" if index and isinstance(item.content[index], ListBlock) else "\n\n"
            joined.append((separator if index else "") + chunk)
        return "".join(joined)

    def _marker(self, block: ListBlock, number: int) -> str:
        style = block.marker_style
        if style is ListStyle.NONE:
            return ""
        if style is ListStyle.BULLET:
            return f"{self.bullet} "
        if style is ListStyle.LOWER_ALPHA:
            return f"{chr(ord('a') + max(0, number - 1) % 26)}. "
        if style is ListStyle.UPPER_ALPHA:
            return f"{chr(ord('A') + max(0, number - 1) % 26)}. "
        if style in (ListStyle.LOWER_ROMAN, ListStyle.UPPER_ROMAN):
            numeral = _to_roman(number)
            return f"{numeral if style is ListStyle.UPPER_ROMAN else numeral.lower()}. "
        return f"{number}. "

    # -- tables ---------------------------------------------------------------
    def _table(self, block: Table, indent: str) -> str:
        if not block.rows:
            return ""
        matrix: List[List[str]] = []
        for row in block.rows:
            # Markdown renders header cells bold already; re-emitting the source
            # document's bold would produce "| **Name** |".
            matrix.append([self._cell_text(cell, plain=row.is_header) for cell in row.cells])
        width = max(len(row) for row in matrix)
        matrix = [row + [""] * (width - len(row)) for row in matrix]

        header_count = sum(1 for row in block.rows if row.is_header)
        if header_count:
            header = matrix[0]
            body = matrix[header_count:]
        else:
            # Markdown tables require a header row; synthesise a blank one.
            header = [""] * width
            body = matrix

        alignments = self._column_alignments(block, width)
        widths = [
            max(len(header[i]), *(len(row[i]) for row in body)) if body else len(header[i])
            for i in range(width)
        ]
        if not self.table_padding:
            widths = [0] * width

        lines = [
            self._table_line(header, widths, indent),
            self._separator_line(alignments, widths, indent),
        ]
        lines.extend(self._table_line(row, widths, indent) for row in body)

        if block.caption:
            lines.append("")
            lines.append(f"{indent}*{self._text(block.caption)}*")
        return "\n".join(lines)

    @staticmethod
    def _column_alignments(block: Table, width: int) -> List[Optional[Alignment]]:
        """Take each column's alignment from the first row that specifies one."""
        alignments: List[Optional[Alignment]] = [None] * width
        for row in block.rows:
            for index, cell in enumerate(row.cells[:width]):
                if alignments[index] is not None:
                    continue
                for child in cell.content:
                    align = getattr(getattr(child, "style", None), "alignment", None)
                    if align is not None:
                        alignments[index] = align
                        break
        return alignments

    def _table_line(self, cells: Sequence[str], widths: Sequence[int], indent: str) -> str:
        padded = [cell.ljust(widths[i]) for i, cell in enumerate(cells)]
        return f"{indent}| " + " | ".join(padded) + " |"

    def _separator_line(
        self,
        alignments: Sequence[Optional[Alignment]],
        widths: Sequence[int],
        indent: str,
    ) -> str:
        parts: List[str] = []
        for index, align in enumerate(alignments):
            target = max(3, widths[index])
            if align is None:
                parts.append("-" * target)
            elif align is Alignment.CENTER:
                parts.append(":" + "-" * max(1, target - 2) + ":")
            elif align is Alignment.RIGHT:
                parts.append("-" * max(2, target - 1) + ":")
            else:
                parts.append(":" + "-" * max(2, target - 1))
        return f"{indent}| " + " | ".join(parts) + " |"

    def _cell_text(self, cell: Any, plain: bool = False) -> str:
        """Flatten a cell to one line -- Markdown tables cannot hold blocks."""
        pieces: List[str] = []
        for block in cell.content:
            if isinstance(block, Paragraph):
                content = _unbold(block.content) if plain else block.content
                pieces.append(self._inlines(content))
            elif isinstance(block, ListBlock):
                pieces.append(" ".join(item.text for item in block.items))
            else:
                pieces.append(self._text(block.text))
        joined = " ".join(piece for piece in pieces if piece)
        return joined.replace("\n", " ").replace("|", "\\|")

    # -- inline ---------------------------------------------------------------
    def _inlines(self, content: Sequence[Inline]) -> str:
        return "".join(self._inline(node) for node in content)

    def _inline(self, node: Inline) -> str:
        if isinstance(node, Text):
            return self._styled(node)
        if isinstance(node, Link):
            # Underline is how other formats *render* a link; re-emitting it here
            # would produce [<u>text</u>](url).
            label = self._inlines(_undecorate(node.content)) or node.href
            title = f' "{node.title}"' if node.title else ""
            return f"[{label}]({node.href}{title})"
        if isinstance(node, LineBreak):
            return "  \n"
        if isinstance(node, InlineImage):
            alt = self._text(node.alt)
            return f"![{alt}]({node.src or ''})"
        if isinstance(node, Math):
            return f"$${node.latex}$$" if node.display else f"${node.latex}$"
        if isinstance(node, FootnoteRef):
            return f"[^{node.identifier}]"
        if isinstance(node, DynamicField):
            return self._text(node.fallback)
        return self._text(node.text)

    def _styled(self, node: Text) -> str:
        text = node.text
        if not text:
            return ""
        style = node.style

        if style.is_monospace:
            # Code spans take no other markup, and need a fence long enough.
            ticks = "`" * (max((len(m) for m in re.findall(r"`+", text)), default=0) + 1)
            pad = " " if text.startswith("`") or text.endswith("`") else ""
            return f"{ticks}{pad}{text}{pad}{ticks}"

        rendered = self._text(text)
        # Markdown emphasis cannot span leading/trailing whitespace, so hoist it out.
        stripped = rendered.strip()
        if not stripped:
            return rendered
        lead = rendered[: len(rendered) - len(rendered.lstrip())]
        trail = rendered[len(rendered.rstrip()) :]

        if style.strike:
            stripped = f"~~{stripped}~~"
        if style.bold and style.italic:
            stripped = f"***{stripped}***"
        elif style.bold:
            stripped = f"**{stripped}**"
        elif style.italic:
            stripped = f"*{stripped}*"
        if style.underline and not style.bold and not style.italic:
            # Markdown has no underline; HTML is valid inside Markdown.
            stripped = f"<u>{stripped}</u>"
        return f"{lead}{stripped}{trail}"

    def _text(self, text: str) -> str:
        if not self.escape or not text:
            return text
        escaped = _INLINE_ESCAPE.sub(r"\\\1", text)
        if not self._line_start_escape:
            return escaped
        return _LINE_START_ESCAPE.sub(r"\1\\\2", escaped)

    # -- footnotes ------------------------------------------------------------
    def _footnote_definitions(self) -> str:
        lines: List[str] = []
        for note in self._footnotes:
            body = self._blocks(note.content).replace("\n", "\n    ")
            lines.append(f"[^{note.identifier}]: {body}")
        return "\n\n".join(lines)


def _unbold(content: Sequence[Inline]) -> List[Inline]:
    """Drop bold, for contexts that already render emphatically (table headers)."""
    from dataclasses import replace as _replace

    out: List[Inline] = []
    for node in content:
        if isinstance(node, Text) and node.style.bold:
            node = Text(node.text, _replace(node.style, bold=None))
        out.append(node)
    return out


def _undecorate(content: Sequence[Inline]) -> List[Inline]:
    """Strip underline from link text, which Markdown supplies itself."""
    from dataclasses import replace as _replace

    out: List[Inline] = []
    for node in content:
        if isinstance(node, Text) and node.style.underline:
            node = Text(node.text, _replace(node.style, underline=None))
        out.append(node)
    return out


def _to_roman(number: int) -> str:
    """Integer to upper-case Roman numeral.

    >>> _to_roman(14)
    'XIV'
    """
    if number <= 0:
        return "I"
    table = (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    )
    out: List[str] = []
    for value, numeral in table:
        while number >= value:
            out.append(numeral)
            number -= value
    return "".join(out)
