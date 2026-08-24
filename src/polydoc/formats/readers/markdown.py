"""Markdown reader, built on markdown-it-py's token stream.

Working from tokens rather than rendered HTML keeps the mapping direct and lossless:
a fenced block with a language becomes a :class:`~polydoc.model.CodeBlock` with
``language`` set, not a ``<pre><code class="language-x">`` we would have to re-parse.

Raw HTML blocks are preserved verbatim in ``attrs["raw_html"]`` so a
Markdown -> polydoc -> Markdown round trip does not quietly drop them.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from ...model import (
    Alignment,
    Block,
    CodeBlock,
    Document,
    Heading,
    HorizontalRule,
    Inline,
    InlineImage,
    LineBreak,
    Link,
    ListBlock,
    ListItem,
    ListStyle,
    Paragraph,
    ParagraphStyle,
    Quote,
    Table,
    TableCell,
    TableRow,
    Text,
    TextStyle,
    merge_runs,
)
from ..base import Reader, require
from ..registry import register_reader
from ..source import Source

__all__ = ["MarkdownReader"]

_CHECKBOX_PREFIXES = (("[ ] ", False), ("[x] ", True), ("[X] ", True))


@register_reader
class MarkdownReader(Reader):
    """Reads CommonMark plus the GitHub extensions (tables, strikethrough)."""

    format = "markdown"
    extensions = (".md", ".markdown", ".mdown", ".mkd")
    aliases = ("md",)
    mime_types = ("text/markdown", "text/x-markdown")
    description = "Markdown (CommonMark + GFM tables, strikethrough, task lists)"

    def read(self, source: Source, **options: Any) -> Document:
        self.enforce_limits(source, **options)
        markdown_it = require("markdown_it", "Reading Markdown", extra="markdown", package="markdown-it-py")
        text = source.text(options.get("encoding"))

        parser = self._make_parser(markdown_it, options.get("preset"))
        tokens = parser.parse(text)

        document = Document(body=_TokenParser(tokens).parse())
        self._extract_front_matter(document, text)
        if document.metadata.title is None:
            for block in document.body:
                if isinstance(block, Heading) and block.level == 1:
                    document.metadata.title = block.text
                    break
        return self.finalise(document, source)

    # -- internals ------------------------------------------------------------
    @staticmethod
    def _make_parser(markdown_it: Any, preset: Optional[str]) -> Any:
        """Build a parser with GFM features on, degrading if the preset is absent.

        The ``gfm-like`` preset also switches on ``linkify``, which raises at parse time
        unless the optional ``linkify-it-py`` package is present. Since bare-URL
        autolinking is a nicety rather than something the model needs, the rule is
        disabled when that package is missing instead of being made a hard dependency.
        """
        import importlib.util

        for candidate in ([preset] if preset else []) + ["gfm-like", "commonmark"]:
            try:
                parser = markdown_it.MarkdownIt(candidate)
            except (KeyError, ValueError):
                continue

            if candidate == "commonmark":
                # Tables and strikethrough are not in CommonMark; enable explicitly.
                try:
                    parser = parser.enable(["table", "strikethrough"])
                except (KeyError, ValueError):  # pragma: no cover
                    pass

            if importlib.util.find_spec("linkify_it") is None:
                try:
                    parser = parser.disable("linkify", ignoreInvalid=True)
                except (KeyError, ValueError, TypeError):  # pragma: no cover
                    parser.options["linkify"] = False
            return parser
        raise RuntimeError("Could not construct a markdown-it parser")  # pragma: no cover

    @staticmethod
    def _extract_front_matter(document: Document, text: str) -> None:
        """Pull ``title:`` / ``author:`` out of a leading YAML front-matter block.

        Parsed with a deliberately small key/value scan rather than a YAML dependency;
        anything more elaborate stays in ``metadata.custom``.
        """
        if not text.startswith("---"):
            return
        end = text.find("\n---", 3)
        if end == -1:
            return
        for line in text[3:end].splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip().strip("\"'")
            if not value:
                continue
            if key == "title":
                document.metadata.title = value
            elif key in ("author", "authors"):
                document.metadata.authors = [p.strip() for p in value.split(",") if p.strip()]
            elif key in ("subject", "description"):
                document.metadata.description = value
            elif key in ("keywords", "tags"):
                document.metadata.keywords = [p.strip() for p in value.split(",") if p.strip()]
            elif key == "lang" or key == "language":
                document.metadata.language = value
            else:
                document.metadata.custom[key] = value


class _TokenParser:
    """Recursive-descent consumer of a markdown-it token stream."""

    def __init__(self, tokens: Sequence[Any]) -> None:
        self.tokens = list(tokens)
        self.index = 0

    # -- cursor ---------------------------------------------------------------
    def _peek(self) -> Optional[Any]:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _advance(self) -> Any:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def _skip_to(self, token_type: str) -> None:
        """Consume tokens up to and including the first match. Defensive resync."""
        while self.index < len(self.tokens):
            if self._advance().type == token_type:
                return

    # -- blocks ---------------------------------------------------------------
    def parse(self, stop: Optional[str] = None) -> List[Block]:
        blocks: List[Block] = []
        while self.index < len(self.tokens):
            token = self._peek()
            assert token is not None
            if stop is not None and token.type == stop:
                self._advance()
                break
            produced = self._block()
            if produced:
                blocks.extend(produced)
        return blocks

    def _block(self) -> List[Block]:
        token = self._advance()
        kind = token.type

        if kind == "heading_open":
            level = int(token.tag[1]) if len(token.tag) > 1 and token.tag[1].isdigit() else 1
            content = self._inline()
            self._skip_to("heading_close")
            return [Heading(content, level)]

        if kind == "paragraph_open":
            content = self._inline()
            self._skip_to("paragraph_close")
            return [Paragraph(content)] if content else []

        if kind in ("fence", "code_block"):
            language = (token.info or "").strip().split()[0] if token.info else None
            return [CodeBlock(token.content.rstrip("\n"), language or None)]

        if kind == "hr":
            return [HorizontalRule()]

        if kind == "blockquote_open":
            return [Quote(self.parse(stop="blockquote_close"))]

        if kind in ("bullet_list_open", "ordered_list_open"):
            return [self._list(token)]

        if kind == "table_open":
            return [self._table()]

        if kind == "html_block":
            # Keep it verbatim so the writer can round-trip it.
            block = Paragraph.of(token.content.strip())
            block.attrs["raw_html"] = True
            return [block]

        if kind == "inline":
            # A stray inline token (defensive; normally wrapped in a paragraph).
            self.index -= 1
            return [Paragraph(self._inline())]

        # Unknown or closing token: ignore.
        return []

    def _list(self, opening: Any) -> ListBlock:
        ordered = opening.type == "ordered_list_open"
        start = 1
        if ordered:
            raw_start = opening.attrGet("start")
            if raw_start is not None:
                try:
                    start = int(raw_start)
                except (TypeError, ValueError):
                    start = 1
        close = "ordered_list_close" if ordered else "bullet_list_close"
        block = ListBlock(
            marker_style=ListStyle.ORDERED if ordered else ListStyle.BULLET,
            start=start,
        )

        while self.index < len(self.tokens):
            token = self._peek()
            assert token is not None
            if token.type == close:
                self._advance()
                break
            if token.type == "list_item_open":
                self._advance()
                item = ListItem(self.parse(stop="list_item_close"))
                self._apply_task_marker(item)
                block.items.append(item)
                block.adopt(item)
            else:
                self._advance()

        block.tight = all(len(item.content) <= 1 for item in block.items)
        return block

    @staticmethod
    def _apply_task_marker(item: ListItem) -> None:
        """Convert a leading ``[ ]`` / ``[x]`` into :attr:`ListItem.checked`."""
        if not item.content or not isinstance(item.content[0], Paragraph):
            return
        paragraph = item.content[0]
        if not paragraph.content or not isinstance(paragraph.content[0], Text):
            return
        first = paragraph.content[0]
        for prefix, checked in _CHECKBOX_PREFIXES:
            if first.text.startswith(prefix):
                first.text = first.text[len(prefix) :]
                item.checked = checked
                return

    def _table(self) -> Table:
        rows: List[TableRow] = []
        in_header = False
        alignments: List[Optional[Alignment]] = []

        while self.index < len(self.tokens):
            token = self._advance()
            kind = token.type
            if kind == "table_close":
                break
            if kind == "thead_open":
                in_header = True
            elif kind == "thead_close":
                in_header = False
            elif kind == "tr_open":
                rows.append(self._table_row(in_header, alignments))
        table = Table(rows, header_rows=sum(1 for r in rows if r.is_header))
        return table

    def _table_row(
        self,
        is_header: bool,
        alignments: List[Optional[Alignment]],
    ) -> TableRow:
        cells: List[TableCell] = []
        while self.index < len(self.tokens):
            token = self._advance()
            kind = token.type
            if kind == "tr_close":
                break
            if kind in ("th_open", "td_open"):
                align = self._alignment_of(token)
                if is_header:
                    alignments.append(align)
                elif align is None and len(cells) < len(alignments):
                    align = alignments[len(cells)]
                content = self._inline() if self._peek_is("inline") else []
                cell = TableCell(
                    [Paragraph(content, ParagraphStyle(alignment=align))] if content else []
                )
                cells.append(cell)
                self._skip_to("th_close" if kind == "th_open" else "td_close")
        return TableRow(cells, is_header=is_header)

    def _peek_is(self, token_type: str) -> bool:
        token = self._peek()
        return token is not None and token.type == token_type

    @staticmethod
    def _alignment_of(token: Any) -> Optional[Alignment]:
        style = token.attrGet("style") or ""
        if "center" in style:
            return Alignment.CENTER
        if "right" in style:
            return Alignment.RIGHT
        if "left" in style:
            return Alignment.LEFT
        return None

    # -- inline ---------------------------------------------------------------
    def _inline(self) -> List[Inline]:
        if not self._peek_is("inline"):
            return []
        token = self._advance()
        return self._inline_children(token.children or [])

    def _inline_children(self, children: Sequence[Any]) -> List[Inline]:
        out: List[Inline] = []
        styles: List[TextStyle] = [TextStyle()]
        # Each open link pushes (link_node, outer_list); content collects into `out`.
        links: List[Any] = []

        toggles = {
            "strong_open": TextStyle(bold=True),
            "em_open": TextStyle(italic=True),
            "s_open": TextStyle(strike=True),
            "sub_open": TextStyle(subscript=True),
            "sup_open": TextStyle(superscript=True),
        }

        for child in children:
            kind = child.type

            if kind == "text":
                if child.content:
                    out.append(Text(child.content, styles[-1]))
            elif kind in toggles:
                styles.append(styles[-1].merge(toggles[kind]))
            elif kind in ("strong_close", "em_close", "s_close", "sub_close", "sup_close"):
                if len(styles) > 1:
                    styles.pop()
            elif kind == "code_inline":
                out.append(Text(child.content, styles[-1].merge(TextStyle(code=True))))
            elif kind == "softbreak":
                out.append(Text(" ", styles[-1]))
            elif kind == "hardbreak":
                out.append(LineBreak())
            elif kind == "link_open":
                link = Link(
                    [],
                    href=child.attrGet("href") or "",
                    title=child.attrGet("title") or None,
                )
                links.append((link, out))
                out = []
            elif kind == "link_close":
                if links:
                    link, outer = links.pop()
                    link.content = merge_runs(out)
                    link.adopt(*link.content)
                    out = outer
                    out.append(link)
            elif kind == "image":
                out.append(
                    InlineImage(
                        src=child.attrGet("src") or None,
                        alt=child.content or "",
                        mime_type=None,
                    )
                )
            elif kind == "html_inline":
                if child.content in ("<br>", "<br/>", "<br />"):
                    out.append(LineBreak())
                elif child.content:
                    out.append(Text(child.content, styles[-1]))

        # Any unclosed link still needs to land in the output.
        while links:
            link, outer = links.pop()
            link.content = merge_runs(out)
            link.adopt(*link.content)
            out = outer
            out.append(link)

        return merge_runs(out)
