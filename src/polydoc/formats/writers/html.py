"""HTML writer.

Produces semantic HTML: ``<strong>`` rather than ``<span style="font-weight:bold">``,
``<thead>`` for header rows, ``<figure>``/``<figcaption>`` for captioned images. Style
attributes are emitted only for the properties the model actually carries, so output
stays readable.

``standalone=True`` (the default) wraps the body in a full document with a small
stylesheet, which makes the result openable in a browser. ``standalone=False`` yields a
fragment for embedding.
"""

from __future__ import annotations

import base64
from html import escape
from typing import Any, Dict, List, Optional, Sequence

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
    ParagraphStyle,
    Quote,
    Section,
    Slide,
    Table,
    TableCell,
    TableRow,
    Text,
    TextStyle,
)
from ..base import TextWriter
from ..registry import register_writer

__all__ = ["HTMLWriter"]

_LIST_TYPE_ATTR = {
    ListStyle.LOWER_ALPHA: "a",
    ListStyle.UPPER_ALPHA: "A",
    ListStyle.LOWER_ROMAN: "i",
    ListStyle.UPPER_ROMAN: "I",
}

_STYLE_TAGS = (
    ("bold", "strong"),
    ("italic", "em"),
    ("underline", "u"),
    ("strike", "s"),
    ("code", "code"),
    ("superscript", "sup"),
    ("subscript", "sub"),
    ("highlight", "mark"),
)

_DEFAULT_CSS = """\
:root { color-scheme: light dark; }
body {
  margin: 0 auto; padding: 2rem 1.25rem; max-width: 46rem;
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
h1, h2, h3, h4, h5, h6 { line-height: 1.25; margin: 2em 0 0.6em; }
h1 { font-size: 2em; } h2 { font-size: 1.5em; } h3 { font-size: 1.25em; }
p, ul, ol, table, pre, blockquote, figure { margin: 0 0 1em; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #d0d7de; padding: 0.4em 0.6em; text-align: left; }
thead th { background: #f6f8fa; }
pre {
  background: #f6f8fa; padding: 0.9em 1em; overflow-x: auto; border-radius: 6px;
}
pre code { background: none; padding: 0; }
code { background: #f6f8fa; padding: 0.15em 0.35em; border-radius: 4px; font-size: 0.9em; }
blockquote {
  margin-left: 0; padding: 0.2em 0 0.2em 1em; border-left: 4px solid #d0d7de; color: #57606a;
}
figure { text-align: center; } figcaption { font-size: 0.9em; color: #57606a; }
img { max-width: 100%; height: auto; }
hr { border: none; border-top: 1px solid #d0d7de; margin: 2em 0; }
.polydoc-page { page-break-after: always; }
.polydoc-slide { border-top: 2px solid #d0d7de; padding-top: 1.5em; margin-top: 2em; }
.polydoc-notes { font-size: 0.9em; color: #57606a; }
"""


@register_writer
class HTMLWriter(TextWriter):
    """Renders a document as semantic HTML5."""

    format = "html"
    extensions = (".html", ".htm", ".xhtml")
    aliases = ("htm",)
    mime_types = ("text/html",)
    description = "Semantic HTML5, standalone or as a fragment"

    def render(self, document: Document, **options: Any) -> str:
        renderer = _HTMLRenderer(
            standalone=options.get("standalone", True),
            css=options.get("css", _DEFAULT_CSS),
            indent=options.get("indent", "  "),
            embed_images=options.get("embed_images", True),
            pretty=options.get("pretty", True),
        )
        return renderer.render(document)


class _HTMLRenderer:
    def __init__(
        self,
        standalone: bool = True,
        css: Optional[str] = _DEFAULT_CSS,
        indent: str = "  ",
        embed_images: bool = True,
        pretty: bool = True,
    ) -> None:
        self.standalone = standalone
        self.css = css
        self.indent_unit = indent if pretty else ""
        self.embed_images = embed_images
        self.pretty = pretty
        self._newline = "\n" if pretty else ""

    # -- entry point ----------------------------------------------------------
    def render(self, document: Document) -> str:
        depth = 2 if self.standalone else 0
        body = self._blocks(document.body, depth)
        if document.footnotes:
            body += self._newline + self._footnotes(document.footnotes, depth)
        if not self.standalone:
            return body.strip() + "\n"

        meta = self._head(document)
        title = escape(document.metadata.title or "Document")
        lang = f' lang="{escape(document.metadata.language)}"' if document.metadata.language else ""
        style = f"<style>\n{self.css}</style>\n" if self.css else ""
        return (
            "<!DOCTYPE html>\n"
            f"<html{lang}>\n<head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{title}</title>\n"
            f"{meta}{style}"
            "</head>\n<body>\n"
            f"{body}\n"
            "</body>\n</html>\n"
        )

    def _head(self, document: Document) -> str:
        meta = document.metadata
        lines: List[str] = []
        if meta.authors:
            lines.append(f'<meta name="author" content="{escape(meta.author or "")}">')
        if meta.description:
            lines.append(f'<meta name="description" content="{escape(meta.description)}">')
        if meta.keywords:
            lines.append(f'<meta name="keywords" content="{escape(", ".join(meta.keywords))}">')
        lines.append('<meta name="generator" content="polydoc">')
        return "\n".join(lines) + "\n" if lines else ""

    # -- blocks ---------------------------------------------------------------
    def _blocks(self, blocks: Sequence[Block], depth: int) -> str:
        rendered = [self._block(block, depth) for block in blocks]
        return self._newline.join(chunk for chunk in rendered if chunk)

    def _pad(self, depth: int) -> str:
        return self.indent_unit * depth

    def _block(self, block: Block, depth: int) -> str:
        pad = self._pad(depth)

        if isinstance(block, Heading):
            attrs = self._block_attrs(block.style)
            return f"{pad}<h{block.level}{attrs}>{self._inlines(block.content)}</h{block.level}>"

        if isinstance(block, Paragraph):
            if block.attrs.get("raw_html"):
                return f"{pad}{block.text}"
            inner = self._inlines(block.content)
            if not inner.strip():
                return ""
            tag = "figcaption" if block.attrs.get("role") == "caption" else "p"
            attrs = self._block_attrs(block.style)
            return f"{pad}<{tag}{attrs}>{inner}</{tag}>"

        if isinstance(block, CodeBlock):
            language = f' class="language-{escape(block.language)}"' if block.language else ""
            return f"{pad}<pre><code{language}>{escape(block.code)}</code></pre>"

        if isinstance(block, ListBlock):
            return self._list(block, depth)

        if isinstance(block, Table):
            return self._table(block, depth)

        if isinstance(block, Quote):
            inner = self._blocks(block.content, depth + 1)
            footer = ""
            if block.attribution:
                footer = (
                    f"{self._newline}{self._pad(depth + 1)}"
                    f"<footer>{escape(block.attribution)}</footer>"
                )
            return f"{pad}<blockquote>{self._newline}{inner}{footer}{self._newline}{pad}</blockquote>"

        if isinstance(block, HorizontalRule):
            return f"{pad}<hr>"

        if isinstance(block, PageBreak):
            return f'{pad}<div class="polydoc-page-break" style="page-break-after:always"></div>'

        if isinstance(block, Image):
            return self._image(block, depth)

        if isinstance(block, Page):
            inner = self._blocks(block.content, depth + 1)
            number = f' data-page="{block.number}"' if block.number is not None else ""
            return (
                f'{pad}<section class="polydoc-page"{number}>{self._newline}'
                f"{inner}{self._newline}{pad}</section>"
            )

        if isinstance(block, Slide):
            return self._slide(block, depth)

        if isinstance(block, Section):
            parts = []
            if block.title:
                level = min(6, max(1, block.level or 1))
                parts.append(
                    f"{self._pad(depth + 1)}<h{level}>{self._inlines(block.title)}</h{level}>"
                )
            inner = self._blocks(block.content, depth + 1)
            if inner:
                parts.append(inner)
            joined = self._newline.join(parts)
            return f"{pad}<section>{self._newline}{joined}{self._newline}{pad}</section>"

        if isinstance(block, Container):
            return self._container(block, depth)

        if isinstance(block, Footnote):
            return ""  # rendered in the footnotes section

        if isinstance(block, BlockContainer):
            return self._blocks(block.content, depth)

        text = block.text
        return f"{pad}<p>{escape(text)}</p>" if text else ""

    def _container(self, block: Container, depth: int) -> str:
        pad = self._pad(depth)
        tag = {"header": "header", "footer": "footer", "aside": "aside", "nav": "nav"}.get(
            block.role, "section"
        )
        label = f' aria-label="{escape(block.name)}"' if block.name else ""
        parts: List[str] = []
        if block.role == "sheet" and block.name:
            parts.append(f"{self._pad(depth + 1)}<h2>{escape(block.name)}</h2>")
        inner = self._blocks(block.content, depth + 1)
        if inner:
            parts.append(inner)
        joined = self._newline.join(p for p in parts if p)
        role = f' class="polydoc-{escape(block.role)}"' if block.role != "group" else ""
        return f"{pad}<{tag}{role}{label}>{self._newline}{joined}{self._newline}{pad}</{tag}>"

    def _slide(self, block: Slide, depth: int) -> str:
        pad = self._pad(depth)
        parts: List[str] = []
        if block.title:
            parts.append(f"{self._pad(depth + 1)}<h2>{escape(block.title)}</h2>")
        inner = self._blocks(block.content, depth + 1)
        if inner:
            parts.append(inner)
        if block.notes:
            parts.append(
                f'{self._pad(depth + 1)}<aside class="polydoc-notes">'
                f"{escape(block.notes)}</aside>"
            )
        joined = self._newline.join(p for p in parts if p)
        index = f' data-slide="{block.index}"' if block.index is not None else ""
        return (
            f'{pad}<section class="polydoc-slide"{index}>{self._newline}'
            f"{joined}{self._newline}{pad}</section>"
        )

    def _image(self, block: Image, depth: int) -> str:
        pad = self._pad(depth)
        src = self._image_src(block)
        attrs = [f'src="{escape(src)}"', f'alt="{escape(block.alt)}"']
        if block.width:
            attrs.append(f'width="{int(block.width)}"')
        if block.height:
            attrs.append(f'height="{int(block.height)}"')
        tag = f"<img {' '.join(attrs)}>"
        if block.caption:
            return (
                f"{pad}<figure>{self._newline}{self._pad(depth + 1)}{tag}{self._newline}"
                f"{self._pad(depth + 1)}<figcaption>{escape(block.caption)}</figcaption>"
                f"{self._newline}{pad}</figure>"
            )
        return f"{pad}{tag}"

    def _image_src(self, block: Any) -> str:
        """Inline embedded bytes as a data URI so the output is self-contained."""
        if self.embed_images and getattr(block, "data", None):
            mime = block.mime_type or "image/png"
            encoded = base64.b64encode(block.data).decode("ascii")
            return f"data:{mime};base64,{encoded}"
        return block.src or ""

    # -- lists ----------------------------------------------------------------
    def _list(self, block: ListBlock, depth: int) -> str:
        pad = self._pad(depth)
        ordered = block.marker_style.is_ordered
        tag = "ol" if ordered else "ul"
        attrs = ""
        if ordered:
            if block.start != 1:
                attrs += f' start="{block.start}"'
            type_attr = _LIST_TYPE_ATTR.get(block.marker_style)
            if type_attr:
                attrs += f' type="{type_attr}"'
        if block.marker_style is ListStyle.NONE:
            attrs += ' style="list-style:none"'

        items = [self._list_item(item, depth + 1) for item in block.items]
        joined = self._newline.join(item for item in items if item)
        return f"{pad}<{tag}{attrs}>{self._newline}{joined}{self._newline}{pad}</{tag}>"

    def _list_item(self, item: ListItem, depth: int) -> str:
        pad = self._pad(depth)
        checkbox = ""
        if item.checked is not None:
            checked = " checked" if item.checked else ""
            checkbox = f'<input type="checkbox" disabled{checked}> '

        # A single paragraph collapses onto one line, which is how hand-written
        # HTML looks; anything richer gets block layout.
        if len(item.content) == 1 and isinstance(item.content[0], Paragraph):
            inner = self._inlines(item.content[0].content)
            return f"{pad}<li>{checkbox}{inner}</li>"
        inner = self._blocks(item.content, depth + 1)
        prefix = f"{self._pad(depth + 1)}{checkbox}" if checkbox else ""
        return f"{pad}<li>{self._newline}{prefix}{inner}{self._newline}{pad}</li>"

    # -- tables ---------------------------------------------------------------
    def _table(self, block: Table, depth: int) -> str:
        pad = self._pad(depth)
        parts: List[str] = []
        if block.caption:
            parts.append(f"{self._pad(depth + 1)}<caption>{escape(block.caption)}</caption>")

        if block.column_widths:
            total = sum(w for w in block.column_widths if w) or 1
            cols = [
                f'{self._pad(depth + 2)}<col style="width:{max(0.0, w) / total:.1%}">'
                for w in block.column_widths
            ]
            parts.append(
                f"{self._pad(depth + 1)}<colgroup>{self._newline}"
                + self._newline.join(cols)
                + f"{self._newline}{self._pad(depth + 1)}</colgroup>"
            )

        header_rows = [row for row in block.rows if row.is_header]
        body_rows = [row for row in block.rows if not row.is_header]

        if header_rows:
            rows = self._newline.join(self._row(r, depth + 2, True) for r in header_rows)
            parts.append(
                f"{self._pad(depth + 1)}<thead>{self._newline}{rows}"
                f"{self._newline}{self._pad(depth + 1)}</thead>"
            )
        if body_rows:
            rows = self._newline.join(self._row(r, depth + 2, False) for r in body_rows)
            parts.append(
                f"{self._pad(depth + 1)}<tbody>{self._newline}{rows}"
                f"{self._newline}{self._pad(depth + 1)}</tbody>"
            )

        joined = self._newline.join(parts)
        return f"{pad}<table>{self._newline}{joined}{self._newline}{pad}</table>"

    def _row(self, row: TableRow, depth: int, header: bool) -> str:
        pad = self._pad(depth)
        cells = self._newline.join(
            self._cell(cell, depth + 1, header) for cell in row.cells
        )
        return f"{pad}<tr>{self._newline}{cells}{self._newline}{pad}</tr>"

    def _cell(self, cell: TableCell, depth: int, header: bool) -> str:
        pad = self._pad(depth)
        tag = "th" if header else "td"
        attrs = ""
        if cell.colspan > 1:
            attrs += f' colspan="{cell.colspan}"'
        if cell.rowspan > 1:
            attrs += f' rowspan="{cell.rowspan}"'
        styles: List[str] = []
        if cell.valign:
            styles.append(f"vertical-align:{cell.valign.value}")
        if cell.background:
            styles.append(f"background:{cell.background}")

        align = self._cell_alignment(cell)
        if align is not None:
            styles.append(f"text-align:{align.value}")
        if styles:
            attrs += f' style="{";".join(styles)}"'

        # A lone paragraph renders inline for compact, readable markup.
        if len(cell.content) == 1 and isinstance(cell.content[0], Paragraph):
            inner = self._inlines(cell.content[0].content)
            return f"{pad}<{tag}{attrs}>{inner}</{tag}>"
        if not cell.content:
            return f"{pad}<{tag}{attrs}></{tag}>"
        inner = self._blocks(cell.content, depth + 1)
        return f"{pad}<{tag}{attrs}>{self._newline}{inner}{self._newline}{pad}</{tag}>"

    @staticmethod
    def _cell_alignment(cell: TableCell) -> Optional[Alignment]:
        for block in cell.content:
            align = getattr(getattr(block, "style", None), "alignment", None)
            if align is not None:
                return align
        return None

    # -- inline ---------------------------------------------------------------
    def _inlines(self, content: Sequence[Inline]) -> str:
        return "".join(self._inline(node) for node in content)

    def _inline(self, node: Inline) -> str:
        if isinstance(node, Text):
            return self._styled(node)
        if isinstance(node, Link):
            inner = self._inlines(node.content) or escape(node.href)
            title = f' title="{escape(node.title)}"' if node.title else ""
            return f'<a href="{escape(node.href, quote=True)}"{title}>{inner}</a>'
        if isinstance(node, LineBreak):
            return "<br>"
        if isinstance(node, InlineImage):
            src = self._image_src(node)
            return f'<img src="{escape(src)}" alt="{escape(node.alt)}">'
        if isinstance(node, Math):
            wrapper = "div" if node.display else "span"
            return f'<{wrapper} class="math">{escape(node.latex)}</{wrapper}>'
        if isinstance(node, FootnoteRef):
            ident = escape(node.identifier)
            return (
                f'<sup id="fnref-{ident}"><a href="#fn-{ident}">'
                f"{escape(node.label or node.identifier)}</a></sup>"
            )
        if isinstance(node, DynamicField):
            return escape(node.fallback)
        return escape(node.text)

    def _styled(self, node: Text) -> str:
        text = escape(node.text)
        if not text:
            return ""
        style = node.style

        # Semantic tags first, innermost last so nesting reads naturally.
        for attribute, tag in _STYLE_TAGS:
            if getattr(style, attribute, None):
                text = f"<{tag}>{text}</{tag}>"

        css = self._text_css(style)
        if css:
            text = f'<span style="{css}">{text}</span>'
        return text

    @staticmethod
    def _text_css(style: TextStyle) -> str:
        parts: List[str] = []
        if style.color:
            parts.append(f"color:{style.color}")
        if style.background and not style.highlight:
            parts.append(f"background:{style.background}")
        if style.font_family:
            parts.append(f"font-family:{style.font_family}")
        if style.font_size:
            parts.append(f"font-size:{style.font_size:g}pt")
        if style.small_caps:
            parts.append("font-variant:small-caps")
        return ";".join(parts)

    @staticmethod
    def _block_attrs(style: ParagraphStyle) -> str:
        parts: List[str] = []
        if style.alignment:
            parts.append(f"text-align:{style.alignment.value}")
        if style.indent_left:
            parts.append(f"margin-left:{style.indent_left:g}pt")
        if style.indent_right:
            parts.append(f"margin-right:{style.indent_right:g}pt")
        if style.first_line_indent:
            parts.append(f"text-indent:{style.first_line_indent:g}pt")
        if style.line_spacing:
            parts.append(f"line-height:{style.line_spacing:g}")
        if style.background:
            parts.append(f"background:{style.background}")
        return f' style="{";".join(parts)}"' if parts else ""

    # -- footnotes ------------------------------------------------------------
    def _footnotes(self, notes: Sequence[Footnote], depth: int) -> str:
        pad = self._pad(depth)
        items: List[str] = []
        for note in notes:
            ident = escape(note.identifier)
            inner = self._blocks(note.content, depth + 2)
            items.append(
                f'{self._pad(depth + 1)}<li id="fn-{ident}">{self._newline}{inner}'
                f"{self._newline}{self._pad(depth + 1)}</li>"
            )
        joined = self._newline.join(items)
        return (
            f'{pad}<section class="polydoc-footnotes">{self._newline}'
            f"{self._pad(depth + 1)}<hr>{self._newline}"
            f"{self._pad(depth + 1)}<ol>{self._newline}{joined}{self._newline}"
            f"{self._pad(depth + 1)}</ol>{self._newline}{pad}</section>"
        )
