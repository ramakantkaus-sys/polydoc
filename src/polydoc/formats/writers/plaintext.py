"""Plain-text writer.

Plain text has no markup, so the job is layout: indentation for lists, box-drawn or
padded columns for tables, and blank lines to separate blocks. Headings are optionally
underlined so the structure survives.
"""

from __future__ import annotations

from typing import Any, List, Sequence

from ...model import (
    Block,
    BlockContainer,
    CodeBlock,
    Container,
    Document,
    Heading,
    HorizontalRule,
    Image,
    Inline,
    LineBreak,
    Link,
    ListBlock,
    ListStyle,
    Page,
    PageBreak,
    Paragraph,
    Quote,
    Section,
    Slide,
    Table,
)
from ..base import TextWriter
from ..registry import register_writer

__all__ = ["PlainTextWriter"]

_HEADING_UNDERLINES = {1: "=", 2: "-", 3: "~", 4: ".", 5: ".", 6: "."}


@register_writer
class PlainTextWriter(TextWriter):
    """Renders a document as readable plain text."""

    format = "txt"
    extensions = (".txt", ".text")
    aliases = ("text", "plain")
    mime_types = ("text/plain",)
    description = "Plain text with layout preserved through indentation"

    def render(self, document: Document, **options: Any) -> str:
        renderer = _TextRenderer(
            width=options.get("width", 0),
            underline_headings=options.get("underline_headings", True),
            table_style=options.get("table_style", "grid"),
            show_links=options.get("show_links", True),
        )
        return renderer.render(document)


class _TextRenderer:
    def __init__(
        self,
        width: int = 0,
        underline_headings: bool = True,
        table_style: str = "grid",
        show_links: bool = True,
    ) -> None:
        self.width = width
        self.underline_headings = underline_headings
        self.table_style = table_style
        self.show_links = show_links

    def render(self, document: Document) -> str:
        parts: List[str] = []
        title = document.metadata.title
        # Only add the title when the body does not already open with it.
        if title and not self._starts_with_title(document, title):
            parts.append(self._underlined(title, 1))
        body = self._blocks(document.body)
        if body:
            parts.append(body)
        if document.footnotes:
            notes = "\n".join(
                f"[{note.identifier}] {self._blocks(note.content)}" for note in document.footnotes
            )
            parts.append(f"---\n{notes}")
        return "\n\n".join(part for part in parts if part).strip() + "\n"

    @staticmethod
    def _starts_with_title(document: Document, title: str) -> bool:
        for block in document.body:
            if isinstance(block, Heading):
                return block.text.strip() == title.strip()
            if isinstance(block, (Page, Slide, Container, Section)):
                inner = getattr(block, "content", [])
                return bool(inner) and getattr(inner[0], "text", "").strip() == title.strip()
            return False
        return False

    # -- blocks ---------------------------------------------------------------
    def _blocks(self, blocks: Sequence[Block], indent: str = "") -> str:
        chunks = [self._block(block, indent) for block in blocks]
        return "\n\n".join(chunk for chunk in chunks if chunk)

    def _block(self, block: Block, indent: str = "") -> str:
        if isinstance(block, Heading):
            text = self._inlines(block.content)
            if self.underline_headings and block.level <= 2:
                return self._indent(self._underlined(text, block.level), indent)
            prefix = "  " * (block.level - 1)
            return self._indent(f"{prefix}{text.upper() if block.level == 1 else text}", indent)

        if isinstance(block, Paragraph):
            return self._indent(self._wrap(self._inlines(block.content)), indent)

        if isinstance(block, CodeBlock):
            return self._indent(
                "\n".join(f"    {line}" for line in block.code.split("\n")), indent
            )

        if isinstance(block, ListBlock):
            return self._list(block, indent)

        if isinstance(block, Table):
            return self._indent(self._table(block), indent)

        if isinstance(block, Quote):
            inner = self._blocks(block.content)
            quoted = "\n".join(f"  | {line}" for line in inner.split("\n"))
            if block.attribution:
                quoted += f"\n  |   -- {block.attribution}"
            return self._indent(quoted, indent)

        if isinstance(block, HorizontalRule):
            return self._indent("-" * (self.width or 60), indent)

        if isinstance(block, PageBreak):
            return self._indent("\f", indent)

        if isinstance(block, Image):
            label = block.caption or block.alt or block.src or "image"
            return self._indent(f"[Image: {label}]", indent)

        if isinstance(block, Slide):
            parts = []
            if block.title:
                parts.append(self._underlined(block.title, 2))
            inner = self._blocks(block.content, indent)
            if inner:
                parts.append(inner)
            if block.notes:
                parts.append(f"Notes: {block.notes}")
            return "\n\n".join(p for p in parts if p)

        if isinstance(block, Container):
            parts = []
            if block.name:
                parts.append(self._underlined(block.name, 2))
            inner = self._blocks(block.content, indent)
            if inner:
                parts.append(inner)
            return "\n\n".join(p for p in parts if p)

        if isinstance(block, Section):
            parts = []
            if block.title:
                parts.append(self._underlined(self._inlines(block.title), max(1, block.level)))
            inner = self._blocks(block.content, indent)
            if inner:
                parts.append(inner)
            return "\n\n".join(p for p in parts if p)

        if isinstance(block, BlockContainer):
            return self._blocks(block.content, indent)

        return self._indent(block.text, indent) if block.text else ""

    def _underlined(self, text: str, level: int) -> str:
        char = _HEADING_UNDERLINES.get(level, "-")
        return f"{text}\n{char * max(3, len(text))}"

    @staticmethod
    def _indent(text: str, indent: str) -> str:
        if not indent or not text:
            return text
        return "\n".join(f"{indent}{line}" if line else "" for line in text.split("\n"))

    def _wrap(self, text: str) -> str:
        if not self.width or len(text) <= self.width:
            return text
        import textwrap

        return "\n".join(
            textwrap.fill(line, self.width) if line else "" for line in text.split("\n")
        )

    # -- lists ----------------------------------------------------------------
    def _list(self, block: ListBlock, indent: str) -> str:
        lines: List[str] = []
        number = block.start
        for item in block.items:
            marker = self._marker(block, number)
            number += 1
            if item.checked is not None:
                marker += "[x] " if item.checked else "[ ] "
            hanging = indent + " " * len(marker)

            body_parts: List[str] = []
            for child in item.content:
                if isinstance(child, ListBlock):
                    body_parts.append(self._list(child, hanging))
                else:
                    body_parts.append(self._block(child, hanging))
            body = "\n".join(part for part in body_parts if part)

            if body:
                stripped = body[len(hanging) :] if body.startswith(hanging) else body.lstrip()
                first, _, rest = stripped.partition("\n")
                lines.append(f"{indent}{marker}{first}")
                if rest:
                    lines.append(rest)
            else:
                lines.append(f"{indent}{marker}".rstrip())
        return "\n".join(lines)

    @staticmethod
    def _marker(block: ListBlock, number: int) -> str:
        style = block.marker_style
        if style is ListStyle.NONE:
            return "  "
        if style is ListStyle.BULLET:
            return "* "
        if style is ListStyle.LOWER_ALPHA:
            return f"{chr(ord('a') + max(0, number - 1) % 26)}. "
        if style is ListStyle.UPPER_ALPHA:
            return f"{chr(ord('A') + max(0, number - 1) % 26)}. "
        return f"{number}. "

    # -- tables ---------------------------------------------------------------
    def _table(self, block: Table) -> str:
        matrix = [[self._cell(cell) for cell in row.cells] for row in block.rows]
        if not matrix:
            return ""
        width = max(len(row) for row in matrix)
        matrix = [row + [""] * (width - len(row)) for row in matrix]
        widths = [max(len(row[i]) for row in matrix) for i in range(width)]

        header_count = sum(1 for row in block.rows if row.is_header)

        def line(cells: Sequence[str]) -> str:
            return "| " + " | ".join(cells[i].ljust(widths[i]) for i in range(width)) + " |"

        def rule(char: str = "-") -> str:
            return "+" + "+".join(char * (w + 2) for w in widths) + "+"

        out: List[str] = []
        if self.table_style == "grid":
            out.append(rule())
            for index, row in enumerate(matrix):
                out.append(line(row))
                out.append(rule("=" if index + 1 == header_count and header_count else "-"))
        else:
            for index, row in enumerate(matrix):
                out.append(line(row))
                if index + 1 == header_count and header_count:
                    out.append(rule("-"))
        if block.caption:
            out.append(f"({block.caption})")
        return "\n".join(out)

    def _cell(self, cell: Any) -> str:
        return " ".join(cell.text.split())

    # -- inline ---------------------------------------------------------------
    def _inlines(self, content: Sequence[Inline]) -> str:
        parts: List[str] = []
        for node in content:
            if isinstance(node, LineBreak):
                parts.append("\n")
            elif isinstance(node, Link):
                label = self._inlines(node.content)
                if self.show_links and node.href and node.href != label:
                    parts.append(f"{label} <{node.href}>")
                else:
                    parts.append(label)
            else:
                parts.append(node.text)
        return "".join(parts)
