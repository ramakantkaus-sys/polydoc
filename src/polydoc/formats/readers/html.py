"""HTML reader, built on BeautifulSoup.

Two details make this more than a tag-to-block lookup:

**Whitespace.** HTML collapses runs of whitespace, so text nodes are normalised on the
way in -- except inside ``<pre>``, where it is significant. Without this, indented
source HTML produces paragraphs full of newlines and padding.

**Mixed content.** A ``<div>`` may hold bare text next to a ``<table>``. Loose inline
content is buffered and flushed as a paragraph whenever a block-level child appears, so
nothing is dropped and nothing ends up nested wrongly.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...model import (
    Alignment,
    Block,
    CodeBlock,
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
    Paragraph,
    ParagraphStyle,
    Quote,
    Table,
    TableCell,
    TableRow,
    Text,
    TextStyle,
    VerticalAlign,
    inline_text,
    merge_runs,
)
from ..base import Reader, require
from ..limits import Limits, check_nesting_depth, get_default_limits
from ..registry import register_reader
from ..source import Source

__all__ = ["HTMLReader"]

#: Tags that start a new block; anything else is treated as inline.
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "canvas", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "li", "main", "nav", "noscript", "ol", "p", "pre",
    "section", "table", "tfoot", "ul", "video",
}

#: Tags whose content is not document text at all.
_SKIP_TAGS = {"script", "style", "head", "meta", "link", "title", "noscript", "template"}

#: Tags that map directly onto character styling.
_STYLE_TAGS: Dict[str, TextStyle] = {
    "b": TextStyle(bold=True),
    "strong": TextStyle(bold=True),
    "i": TextStyle(italic=True),
    "em": TextStyle(italic=True),
    "cite": TextStyle(italic=True),
    "dfn": TextStyle(italic=True),
    "var": TextStyle(italic=True),
    "u": TextStyle(underline=True),
    "ins": TextStyle(underline=True),
    "s": TextStyle(strike=True),
    "strike": TextStyle(strike=True),
    "del": TextStyle(strike=True),
    "code": TextStyle(code=True),
    "kbd": TextStyle(code=True),
    "samp": TextStyle(code=True),
    "tt": TextStyle(code=True),
    "sub": TextStyle(subscript=True),
    "sup": TextStyle(superscript=True),
    "mark": TextStyle(highlight=True),
}

#: Semantic wrappers worth keeping as a labelled container.
_CONTAINER_ROLES = {
    "header": "header",
    "footer": "footer",
    "aside": "aside",
    "nav": "nav",
    "form": "form",
    "address": "address",
}

_WHITESPACE_RE = re.compile(r"\s+")
_CSS_SIZE_RE = re.compile(r"^([\d.]+)\s*(px|pt|em|rem|%)?$")


def _parse_css(declaration: str) -> Tuple[TextStyle, ParagraphStyle]:
    """Extract the properties we model from an inline ``style`` attribute.

    Only the handful of declarations that map onto the universal model are read;
    everything else is intentionally ignored rather than half-supported.
    """
    text = TextStyle()
    paragraph = ParagraphStyle()
    if not declaration:
        return text, paragraph

    updates: Dict[str, Any] = {}
    block_updates: Dict[str, Any] = {}

    for part in declaration.split(";"):
        name, _, value = part.partition(":")
        name = name.strip().lower()
        value = value.strip()
        if not name or not value:
            continue

        if name == "font-weight":
            updates["bold"] = value in ("bold", "bolder") or (
                value.isdigit() and int(value) >= 600
            )
        elif name == "font-style":
            updates["italic"] = value in ("italic", "oblique")
        elif name == "text-decoration" or name == "text-decoration-line":
            if "underline" in value:
                updates["underline"] = True
            if "line-through" in value:
                updates["strike"] = True
        elif name == "color":
            updates["color"] = value
        elif name == "background-color" or name == "background":
            updates["background"] = value
            block_updates["background"] = value
        elif name == "font-family":
            updates["font_family"] = value.split(",")[0].strip().strip("\"'")
        elif name == "font-size":
            size = _css_length(value)
            if size:
                updates["font_size"] = size
        elif name == "text-align":
            block_updates["alignment"] = value
        elif name == "margin-left" or name == "padding-left":
            size = _css_length(value)
            if size:
                block_updates["indent_left"] = size
        elif name == "text-indent":
            size = _css_length(value)
            if size:
                block_updates["first_line_indent"] = size

    if updates:
        text = TextStyle(**{k: v for k, v in updates.items() if v is not None})
    if block_updates:
        paragraph = ParagraphStyle(
            alignment=Alignment.coerce(block_updates.get("alignment")),
            indent_left=block_updates.get("indent_left"),
            first_line_indent=block_updates.get("first_line_indent"),
            background=block_updates.get("background"),
        )
    return text, paragraph


def _css_length(value: str) -> Optional[float]:
    """Convert a CSS length to points, or ``None`` for relative units we cannot resolve."""
    match = _CSS_SIZE_RE.match(value.strip())
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "px").lower()
    if unit == "pt":
        return amount
    if unit == "px":
        return round(amount * 0.75, 2)
    if unit in ("em", "rem"):
        return round(amount * 12.0, 2)
    return None


@register_reader
class HTMLReader(Reader):
    """Reads HTML and XHTML into the universal model."""

    format = "html"
    extensions = (".html", ".htm", ".xhtml")
    aliases = ("htm", "xhtml")
    mime_types = ("text/html", "application/xhtml+xml")
    description = "HTML / XHTML, including inline CSS styling"

    def read(self, source: Source, **options: Any) -> Document:
        self.enforce_limits(source, **options)
        bs4 = require("bs4", "Reading HTML", extra="html", package="beautifulsoup4")
        markup = source.text(options.get("encoding"))

        parser = options.get("parser")
        soup = self._make_soup(bs4, markup, parser)

        document = Document()
        # Read metadata first: the tags it needs live in <head>, which the cleanup
        # pass below removes.
        self._read_metadata(soup, document)

        for element in soup.find_all(list(_SKIP_TAGS)):
            element.decompose()

        root = soup.body or soup
        limits = (options.get("limits") or get_default_limits()).with_overrides(**options)
        builder = _HTMLBuilder(bs4, limits)
        document.body.extend(builder.blocks(root))
        document.adopt(*document.body)

        if document.metadata.title is None:
            for block in document.body:
                if isinstance(block, Heading):
                    document.metadata.title = block.text
                    break

        return self.finalise(document, source)

    # -- internals ------------------------------------------------------------
    @staticmethod
    def _make_soup(bs4: Any, markup: str, parser: Optional[str]) -> Any:
        """Prefer lxml for speed and resilience, then fall back to the stdlib parser."""
        candidates = [parser] if parser else ["lxml", "html.parser"]
        last_error: Optional[Exception] = None
        for candidate in candidates:
            try:
                return bs4.BeautifulSoup(markup, candidate)
            except Exception as exc:  # pragma: no cover - depends on installed parsers
                last_error = exc
        raise RuntimeError(f"No usable HTML parser: {last_error}")  # pragma: no cover

    @staticmethod
    def _read_metadata(soup: Any, document: Document) -> None:
        meta = document.metadata
        if soup.title and soup.title.string:
            meta.title = soup.title.string.strip()

        html_tag = soup.find("html")
        if html_tag and html_tag.get("lang"):
            meta.language = html_tag["lang"]

        for tag in soup.find_all("meta"):
            name = (tag.get("name") or tag.get("property") or "").lower()
            content = tag.get("content")
            if not name or not content:
                continue
            if name in ("author", "article:author"):
                meta.authors = [p.strip() for p in content.split(",") if p.strip()]
            elif name in ("description", "og:description"):
                meta.description = content
            elif name == "keywords":
                meta.keywords = [p.strip() for p in content.split(",") if p.strip()]
            elif name in ("og:title",) and not meta.title:
                meta.title = content
            elif name == "generator":
                meta.producer = content
            else:
                meta.custom[name] = content


class _HTMLBuilder:
    """Walks a BeautifulSoup tree producing model blocks."""

    def __init__(self, bs4: Any, limits: Optional[Limits] = None) -> None:
        self._NavigableString = bs4.NavigableString
        self._Tag = bs4.Tag
        self._Comment = bs4.Comment
        self._limits = limits or get_default_limits()
        #: Current recursion depth, checked so pathological nesting raises a clear
        #: error instead of a bare RecursionError from deep inside this walk.
        self._depth = 0

    def _descend(self) -> None:
        self._depth += 1
        check_nesting_depth(self._depth, self._limits, "HTML")

    # -- blocks ---------------------------------------------------------------
    def blocks(self, element: Any, preserve: bool = False) -> List[Block]:
        out: List[Block] = []
        pending: List[Inline] = []

        def _flush() -> None:
            """Emit buffered inline content as a paragraph (or a block image)."""
            nonlocal pending
            if not pending:
                return
            trimmed = _trim_inline(merge_runs(pending))
            pending = []
            if not trimmed:
                return
            # A paragraph holding nothing but an image is really a block image.
            if len(trimmed) == 1 and isinstance(trimmed[0], InlineImage):
                image = trimmed[0]
                out.append(
                    Image(
                        src=image.src,
                        alt=image.alt,
                        width=image.width,
                        height=image.height,
                        mime_type=image.mime_type,
                    )
                )
                return
            if inline_text(trimmed).strip():
                out.append(Paragraph(trimmed))

        for child in element.children:
            if isinstance(child, self._Comment):
                continue
            if isinstance(child, self._NavigableString):
                text = str(child)
                if not preserve:
                    text = _WHITESPACE_RE.sub(" ", text)
                if text.strip() or (pending and text):
                    pending.append(Text(text))
                continue
            name = (child.name or "").lower()
            if name in _SKIP_TAGS:
                continue
            if name in _BLOCK_TAGS or name in ("figure", "figcaption"):
                _flush()
                self._descend()
                try:
                    out.extend(self.block(child))
                finally:
                    self._depth -= 1
            else:
                self._descend()
                try:
                    pending.extend(self.inline(child, TextStyle(), preserve))
                finally:
                    self._depth -= 1

        _flush()
        return out

    def block(self, element: Any) -> List[Block]:
        name = (element.name or "").lower()
        text_style, para_style = _parse_css(element.get("style", ""))
        align = _attr_alignment(element) or para_style.alignment
        if align is not None:
            para_style = ParagraphStyle(
                alignment=align,
                indent_left=para_style.indent_left,
                first_line_indent=para_style.first_line_indent,
                background=para_style.background,
            )

        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            content = self.inline_children(element, text_style)
            return [Heading(_trim_inline(content), int(name[1]), para_style)]

        if name == "p" or name == "dt" or name == "dd":
            content = _trim_inline(self.inline_children(element, text_style))
            if not content:
                return []
            style = para_style
            if name == "dd":
                style = ParagraphStyle(
                    alignment=para_style.alignment,
                    indent_left=(para_style.indent_left or 0) + 36,
                )
            return [Paragraph(content, style)]

        if name == "pre":
            code_child = element.find("code")
            language = None
            if code_child is not None:
                language = _language_from_classes(code_child.get("class"))
                text = code_child.get_text()
            else:
                language = _language_from_classes(element.get("class"))
                text = element.get_text()
            return [CodeBlock(text.strip("\n"), language)]

        if name in ("ul", "ol", "dl"):
            return [self.list_block(element)]

        if name == "table":
            return [self.table(element)]

        if name == "blockquote":
            inner = self.blocks(element)
            attribution = None
            cite = element.get("cite")
            footer = element.find(["footer", "cite"])
            if footer is not None:
                attribution = " ".join(footer.get_text().split())
                # Remove it from the quoted body so it is not duplicated.
                inner = [b for b in inner if b.text.strip() != attribution]
            return [Quote(inner, attribution or cite)]

        if name == "hr":
            return [HorizontalRule()]

        if name == "figure":
            return self.figure(element)

        if name == "figcaption":
            content = _trim_inline(self.inline_children(element, text_style))
            if not content:
                return []
            block = Paragraph(content, para_style)
            block.attrs["role"] = "caption"
            return [block]

        if name == "li":
            # Reached only for a stray <li>; wrap it so nothing is lost.
            return [ListBlock([ListItem(self.blocks(element))])]

        if name in _CONTAINER_ROLES:
            inner = self.blocks(element)
            if not inner:
                return []
            return [Container(inner, role=_CONTAINER_ROLES[name])]

        # div, section, article, main, and anything else structural: flatten.
        return self.blocks(element)

    def figure(self, element: Any) -> List[Block]:
        """A ``<figure>`` becomes an :class:`Image` carrying its ``<figcaption>``."""
        caption_tag = element.find("figcaption")
        caption = " ".join(caption_tag.get_text().split()) if caption_tag else None
        img = element.find("img")
        if img is not None:
            return [
                Image(
                    src=img.get("src"),
                    alt=img.get("alt", ""),
                    caption=caption,
                    width=_dimension(img.get("width")),
                    height=_dimension(img.get("height")),
                )
            ]
        # A figure can wrap a table or code sample instead of an image.
        inner = self.blocks(element)
        if caption:
            for block in inner:
                if isinstance(block, Table) and not block.caption:
                    block.caption = caption
                    break
        return inner

    def list_block(self, element: Any) -> ListBlock:
        name = (element.name or "").lower()
        if name == "ol":
            marker_style = _ordered_style(element.get("type"))
            start = _int_or(element.get("start"), 1)
        else:
            marker_style = ListStyle.BULLET
            start = 1

        block = ListBlock(marker_style=marker_style, start=start)
        for child in element.find_all(["li", "dt", "dd"], recursive=False):
            item = ListItem(self.blocks(child))
            checked = self._task_state(child)
            if checked is not None:
                item.checked = checked
            block.items.append(item)
            block.adopt(item)
        block.tight = all(len(item.content) <= 1 for item in block.items)
        return block

    @staticmethod
    def _task_state(element: Any) -> Optional[bool]:
        """Detect a GitHub-style task list item."""
        box = element.find("input", attrs={"type": "checkbox"})
        if box is None:
            return None
        return box.has_attr("checked")

    def table(self, element: Any) -> Table:
        caption_tag = element.find("caption")
        caption = " ".join(caption_tag.get_text().split()) if caption_tag else None

        rows: List[TableRow] = []
        for tr in element.find_all("tr"):
            in_head = any(
                (parent.name or "").lower() == "thead"
                for parent in tr.parents
                if parent is not element and getattr(parent, "name", None)
            )
            cells: List[TableCell] = []
            header_cells = 0
            for td in tr.find_all(["td", "th"], recursive=False):
                is_th = (td.name or "").lower() == "th"
                header_cells += 1 if is_th else 0
                cell_text_style, cell_para_style = _parse_css(td.get("style", ""))
                align = _attr_alignment(td) or cell_para_style.alignment
                content = self.blocks(td)
                if not content:
                    inline = _trim_inline(self.inline_children(td, cell_text_style))
                    if inline:
                        content = [Paragraph(inline, ParagraphStyle(alignment=align))]
                elif align is not None:
                    for block in content:
                        if isinstance(block, Paragraph) and block.style.alignment is None:
                            block.style = ParagraphStyle(
                                alignment=align,
                                indent_left=block.style.indent_left,
                                background=block.style.background,
                            )
                if is_th and cell_text_style.bold is None:
                    cell_text_style = cell_text_style.merge(TextStyle(bold=True))
                cells.append(
                    TableCell(
                        content,
                        colspan=_int_or(td.get("colspan"), 1),
                        rowspan=_int_or(td.get("rowspan"), 1),
                        valign=VerticalAlign.coerce(td.get("valign")),
                        background=cell_para_style.background,
                    )
                )
            if cells:
                is_header = in_head or (header_cells == len(cells))
                rows.append(TableRow(cells, is_header=is_header))

        table = Table(rows, caption=caption)
        widths = _column_widths(element)
        if widths:
            table.column_widths = widths
        return table

    # -- inline ---------------------------------------------------------------
    def inline_children(
        self,
        element: Any,
        style: TextStyle,
        preserve: bool = False,
    ) -> List[Inline]:
        out: List[Inline] = []
        for child in element.children:
            if isinstance(child, self._Comment):
                continue
            if isinstance(child, self._NavigableString):
                text = str(child)
                if not preserve:
                    text = _WHITESPACE_RE.sub(" ", text)
                if text:
                    out.append(Text(text, style))
            else:
                self._descend()
                try:
                    out.extend(self.inline(child, style, preserve))
                finally:
                    self._depth -= 1
        return merge_runs(out)

    def inline(self, element: Any, style: TextStyle, preserve: bool = False) -> List[Inline]:
        name = (element.name or "").lower()
        if name in _SKIP_TAGS:
            return []
        if name == "br":
            return [LineBreak()]
        if name == "wbr":
            return []

        css_text, _ = _parse_css(element.get("style", ""))
        merged = style.merge(_STYLE_TAGS.get(name, TextStyle())).merge(css_text)

        if name == "img":
            return [
                InlineImage(
                    src=element.get("src"),
                    alt=element.get("alt", ""),
                    width=_dimension(element.get("width")),
                    height=_dimension(element.get("height")),
                )
            ]

        if name == "a":
            href = element.get("href") or ""
            content = self.inline_children(element, merged, preserve)
            if not href:
                return content
            return [Link(content, href=href, title=element.get("title") or None)]

        if name == "input":
            # Task-list checkboxes are handled at the list level; drop the widget.
            return []

        if name in _BLOCK_TAGS:
            # A block tag encountered in inline position (invalid but common):
            # keep its text so nothing disappears.
            return self.inline_children(element, merged, preserve)

        return self.inline_children(element, merged, preserve or name == "pre")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trim_inline(content: Sequence[Inline]) -> List[Inline]:
    """Strip whitespace at the edges of an inline sequence.

    HTML source indentation otherwise shows up as leading spaces in every paragraph.
    """
    items = list(content)
    while items:
        first = items[0]
        if isinstance(first, Text):
            stripped = first.text.lstrip()
            if stripped:
                items[0] = Text(stripped, first.style)
                break
            items.pop(0)
        elif isinstance(first, LineBreak):
            items.pop(0)
        else:
            break
    while items:
        last = items[-1]
        if isinstance(last, Text):
            stripped = last.text.rstrip()
            if stripped:
                items[-1] = Text(stripped, last.style)
                break
            items.pop()
        elif isinstance(last, LineBreak):
            items.pop()
        else:
            break
    return items


def _attr_alignment(element: Any) -> Optional[Alignment]:
    return Alignment.coerce(element.get("align"))


def _int_or(value: Any, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _dimension(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace("px", "").strip())
    except ValueError:
        return None


def _ordered_style(type_attr: Optional[str]) -> ListStyle:
    return {
        "a": ListStyle.LOWER_ALPHA,
        "A": ListStyle.UPPER_ALPHA,
        "i": ListStyle.LOWER_ROMAN,
        "I": ListStyle.UPPER_ROMAN,
    }.get(type_attr or "", ListStyle.ORDERED)


def _language_from_classes(classes: Any) -> Optional[str]:
    """Read a highlight language out of ``class="language-python"`` and friends."""
    if not classes:
        return None
    if isinstance(classes, str):
        classes = classes.split()
    for name in classes:
        lowered = name.lower()
        for prefix in ("language-", "lang-", "highlight-", "brush:"):
            if lowered.startswith(prefix):
                return lowered[len(prefix) :].strip() or None
    # A bare single class is often the language itself.
    return classes[0] if len(classes) == 1 and classes[0].isalnum() else None


def _column_widths(table_element: Any) -> Optional[List[float]]:
    """Read relative column widths from ``<col>`` elements, if present."""
    cols = table_element.find_all("col")
    if not cols:
        return None
    widths: List[float] = []
    for col in cols:
        raw = col.get("width") or ""
        style_width = ""
        style = col.get("style") or ""
        for part in style.split(";"):
            key, _, value = part.partition(":")
            if key.strip().lower() == "width":
                style_width = value.strip()
        candidate = raw or style_width
        parsed = _dimension(candidate.replace("%", "")) if candidate else None
        widths.append(parsed if parsed is not None else 0.0)
    return widths if any(widths) else None
