"""PDF writer, built on ReportLab's Platypus layout engine.

Platypus handles pagination, so the work here is translation: model blocks become
flowables, and inline runs become ReportLab's small markup dialect (``<b>``, ``<font
color=...>``, ``<a href=...>``).

Two things worth calling out:

**A real outline.** Headings register PDF bookmarks through an ``afterFlowable`` hook, so
the output has a navigable sidebar rather than being a flat wall of pages. ReportLab
rejects outline levels that skip a step, so levels are normalised as they are emitted.

**Table widths.** Platypus needs explicit column widths or wide tables overflow the
page. Widths come from the model when present, otherwise from a measurement of the
content, always normalised to the printable width.
"""

from __future__ import annotations

import html as _html
from typing import Any, BinaryIO, Dict, List, Optional, Sequence, Tuple

from ...exceptions import WriteError
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
    ListStyle,
    Math,
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
    Text,
    TextStyle,
    VerticalAlign,
)
from ..base import Writer, require, unwrap_pages
from ..registry import register_writer

__all__ = ["PDFWriter"]

_ALIGNMENT_CODES = {
    Alignment.LEFT: 0,
    Alignment.CENTER: 1,
    Alignment.RIGHT: 2,
    Alignment.JUSTIFY: 4,
}

#: ReportLab bullet/numbering types per list style.
_BULLET_TYPES = {
    ListStyle.BULLET: "bullet",
    ListStyle.ORDERED: "1",
    ListStyle.LOWER_ALPHA: "a",
    ListStyle.UPPER_ALPHA: "A",
    ListStyle.LOWER_ROMAN: "i",
    ListStyle.UPPER_ROMAN: "I",
}

_VALIGN_CODES = {
    VerticalAlign.TOP: "TOP",
    VerticalAlign.MIDDLE: "MIDDLE",
    VerticalAlign.BOTTOM: "BOTTOM",
}

#: File suffix per MIME type, so ReportLab can pick the right decoder from the path.
_IMAGE_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/webp": ".webp",
}

#: Horizontal placement for image flowables. ReportLab's own default is CENTER, which
#: silently recentres a left-aligned figure, so ``None`` maps to LEFT here.
_IMAGE_ALIGN = {
    Alignment.LEFT: "LEFT",
    Alignment.CENTER: "CENTER",
    Alignment.RIGHT: "RIGHT",
    Alignment.JUSTIFY: "LEFT",
    None: "LEFT",
}

#: Point sizes for heading levels 1-6.
_HEADING_SIZES = (22.0, 17.0, 14.0, 12.5, 11.5, 11.0)

_MONO_FONT = "Courier"
#: Bullet glyphs per depth. Two constraints: the character must exist in the standard
#: WinAnsi fonts (U+25E6 WHITE BULLET does not, and ReportLab silently substitutes a
#: glyph that extracts as garbage), and it must be one a reader recognises as a bullet.
#: Word's choice of a literal "o" for level two satisfies the first but not the second.
_BULLET_GLYPHS = ("\u2022", "\u00b7", "-")


@register_writer
class PDFWriter(Writer):
    """Writes PDF via ReportLab, with automatic pagination and a bookmark outline."""

    format = "pdf"
    extensions = (".pdf",)
    mime_types = ("application/pdf",)
    extra = "pdf"
    description = "PDF via ReportLab, with a bookmark outline"

    def write(self, document: Document, stream: BinaryIO, **options: Any) -> None:
        require("reportlab", "Writing PDF", extra="pdf")
        builder = _PDFBuilder(document, options)
        builder.build(stream)


class _PDFBuilder:
    def __init__(self, document: Document, options: Dict[str, Any]) -> None:
        self.document = document
        self.options = options
        self.embed_images: bool = options.get("embed_images", True)
        self.base_font: str = options.get("font", "Helvetica")
        self.base_size: float = float(options.get("font_size", 10.5))
        self.leading_ratio: float = float(options.get("leading", 1.42))
        self.link_colour: str = options.get("link_color", "#0563c1")
        self.outline: bool = options.get("outline", True)
        self.page_numbers: bool = options.get("page_numbers", True)
        self._styles = self._build_styles()
        #: sha1 -> temp file path, for inline images spilled during layout.
        self._spilled: Dict[str, str] = {}

    # -- setup ----------------------------------------------------------------
    def _geometry(self) -> Tuple[float, float, float, float, float, float]:
        from reportlab.lib.pagesizes import A4

        geometry = self.document.geometry
        page_size = self.options.get("page_size")
        if page_size is not None and not isinstance(page_size, PageGeometry):
            width, height = page_size
        elif geometry is not None:
            width, height = geometry.size.width, geometry.size.height
        else:
            width, height = A4

        margins = self.options.get("margins")
        if margins is not None:
            left = right = top = bottom = float(margins)
        elif geometry is not None:
            left, right = geometry.margin_left, geometry.margin_right
            top, bottom = geometry.margin_top, geometry.margin_bottom
        else:
            left = right = top = bottom = 56.0

        # A page-based source (a slide deck) can report zero margins; PDF prose needs
        # some, or text runs to the trim edge.
        if max(left, right, top, bottom) < 8.0:
            left = right = top = bottom = 42.0
        return (width, height, left, right, top, bottom)

    def _build_styles(self) -> Dict[str, Any]:
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.styles import ParagraphStyle as RLStyle
        from reportlab.lib.styles import getSampleStyleSheet

        sheet = getSampleStyleSheet()
        body = RLStyle(
            "PDBody",
            parent=sheet["BodyText"],
            fontName=self.base_font,
            fontSize=self.base_size,
            leading=self.base_size * self.leading_ratio,
            spaceBefore=0,
            spaceAfter=self.base_size * 0.62,
            alignment=TA_LEFT,
        )
        styles: Dict[str, Any] = {"body": body}

        for level in range(1, 7):
            size = _HEADING_SIZES[level - 1]
            styles[f"h{level}"] = RLStyle(
                f"PDHeading{level}",
                parent=body,
                fontName=f"{self.base_font}-Bold",
                fontSize=size,
                leading=size * 1.22,
                spaceBefore=size * (0.9 if level > 1 else 0.3),
                spaceAfter=size * 0.34,
                keepWithNext=True,
            )

        styles["code"] = RLStyle(
            "PDCode",
            parent=body,
            fontName=_MONO_FONT,
            fontSize=self.base_size * 0.9,
            leading=self.base_size * 1.18,
            leftIndent=10,
            spaceBefore=2,
            spaceAfter=2,
            backColor="#f6f8fa",
            borderPadding=(5, 6, 5, 6),
        )
        styles["quote"] = RLStyle(
            "PDQuote",
            parent=body,
            leftIndent=20,
            rightIndent=10,
            textColor="#444444",
            borderColor="#cccccc",
            spaceBefore=4,
            spaceAfter=6,
        )
        styles["caption"] = RLStyle(
            "PDCaption",
            parent=body,
            fontSize=self.base_size * 0.86,
            textColor="#555555",
            alignment=1,
            spaceBefore=2,
            spaceAfter=8,
        )
        styles["cell"] = RLStyle(
            "PDCell",
            parent=body,
            fontSize=self.base_size * 0.92,
            leading=self.base_size * 1.16,
            spaceAfter=0,
        )
        styles["cellhead"] = RLStyle(
            "PDCellHead",
            parent=styles["cell"],
            fontName=f"{self.base_font}-Bold",
        )
        styles["footnote"] = RLStyle(
            "PDFootnote",
            parent=body,
            fontSize=self.base_size * 0.84,
            leading=self.base_size * 1.05,
            spaceAfter=2,
        )
        return styles

    # -- entry point ----------------------------------------------------------
    def build(self, stream: BinaryIO) -> None:

        width, height, left, right, top, bottom = self._geometry()
        self.frame_width = width - left - right

        meta = self.document.metadata
        template = _OutlineTemplate(
            stream,
            pagesize=(width, height),
            leftMargin=left,
            rightMargin=right,
            topMargin=top,
            bottomMargin=bottom,
            title=meta.title or "",
            author=meta.author or "",
            subject=meta.subject or "",
            creator="polydoc",
            keywords=", ".join(meta.keywords),
        )
        template.enable_outline = self.outline

        # Inline images spill to temp files while the story is assembled; they must
        # survive until build() has read them, hence the try/finally around both.
        try:
            story = self._story()
            if not story:
                # ReportLab refuses to emit a zero-flowable document.
                from reportlab.platypus import Spacer

                story = [Spacer(1, 1)]

            decorator = _PageDecorator(self.document, self.page_numbers, self.base_font)
            try:
                template.build(story, onFirstPage=decorator, onLaterPages=decorator)
            except Exception as exc:  # pragma: no cover - layout overflow
                raise WriteError(
                    f"ReportLab could not lay out this document: {exc}"
                ) from exc
        finally:
            self._cleanup_spilled()

    def _story(self) -> List[Any]:
        blocks = unwrap_pages(self.document)
        story: List[Any] = []
        for block in blocks:
            story.extend(self._flowables(block))
        if self.document.footnotes:
            story.extend(self._footnotes())
        return story

    # -- blocks ---------------------------------------------------------------
    def _flowables(self, block: Block, depth: int = 0) -> List[Any]:
        from reportlab.platypus import KeepTogether, Preformatted, Spacer

        if isinstance(block, Heading):
            return [self._paragraph(block.content, self._heading_style(block), block.style)]

        if isinstance(block, Paragraph):
            if not block.text.strip():
                return []
            return [self._paragraph(block.content, self._styles["body"], block.style)]

        if isinstance(block, CodeBlock):
            style = self._styles["code"]
            # Preformatted keeps whitespace and never re-wraps, which is what code needs.
            return [Preformatted(block.code or " ", style), Spacer(1, 4)]

        if isinstance(block, ListBlock):
            return [self._list(block, depth)]

        if isinstance(block, Table):
            return self._table(block)

        if isinstance(block, Quote):
            inner: List[Any] = []
            for child in block.content:
                if isinstance(child, Paragraph):
                    inner.append(
                        self._paragraph(child.content, self._styles["quote"], child.style)
                    )
                else:
                    inner.extend(self._flowables(child, depth))
            if block.attribution:
                inner.append(
                    self._paragraph(
                        [Text(f"\u2014 {block.attribution}", TextStyle(italic=True))],
                        self._styles["quote"],
                        ParagraphStyle(),
                    )
                )
            return [KeepTogether(inner)] if inner else []

        if isinstance(block, HorizontalRule):
            from reportlab.platypus import HRFlowable

            return [
                Spacer(1, 6),
                HRFlowable(width="100%", thickness=0.6, color="#cccccc"),
                Spacer(1, 8),
            ]

        if isinstance(block, PageBreak):
            from reportlab.platypus import PageBreak as RLPageBreak

            return [RLPageBreak()]

        if isinstance(block, Image):
            return self._image(block)

        if isinstance(block, Section):
            out: List[Any] = []
            if block.title:
                level = max(1, min(6, block.level or 1))
                out.append(
                    self._paragraph(block.title, self._styles[f"h{level}"], ParagraphStyle())
                )
            for child in block.content:
                out.extend(self._flowables(child, depth))
            return out

        if isinstance(block, Slide):
            out = []
            if block.title:
                out.append(
                    self._paragraph(
                        [Text(block.title)], self._styles["h2"], ParagraphStyle()
                    )
                )
            for child in block.content:
                out.extend(self._flowables(child, depth))
            if block.notes:
                out.append(
                    self._paragraph(
                        [Text(f"Notes: {block.notes}", TextStyle(italic=True))],
                        self._styles["footnote"],
                        ParagraphStyle(),
                    )
                )
            return out

        if isinstance(block, Container):
            out = []
            if block.role == "sheet" and block.name:
                out.append(
                    self._paragraph([Text(block.name)], self._styles["h2"], ParagraphStyle())
                )
            for child in block.content:
                out.extend(self._flowables(child, depth))
            return out

        if isinstance(block, Footnote):
            return []

        if isinstance(block, (Page, BlockContainer)):
            out = []
            for child in getattr(block, "content", []):
                out.extend(self._flowables(child, depth))
            return out

        if block.text:
            return [self._paragraph([Text(block.text)], self._styles["body"], ParagraphStyle())]
        return []

    def _heading_style(self, block: Heading) -> Any:
        return self._styles[f"h{max(1, min(6, block.level))}"]

    # -- paragraphs -----------------------------------------------------------
    def _paragraph(
        self,
        content: Sequence[Inline],
        base: Any,
        block_style: ParagraphStyle,
    ) -> Any:
        from reportlab.platypus import Paragraph as RLParagraph

        style = self._derive(base, block_style)
        markup = self._markup(content) or "&nbsp;"
        return RLParagraph(markup, style)

    def _derive(self, base: Any, block_style: ParagraphStyle) -> Any:
        """Apply a block's own style on top of a base paragraph style."""
        if block_style.is_empty():
            return base
        from reportlab.lib.styles import ParagraphStyle as RLStyle

        style = RLStyle(f"{base.name}+", parent=base)
        if block_style.alignment is not None:
            code = _ALIGNMENT_CODES.get(block_style.alignment)
            if code is not None:
                style.alignment = code
        if block_style.space_before is not None:
            style.spaceBefore = block_style.space_before
        if block_style.space_after is not None:
            style.spaceAfter = block_style.space_after
        if block_style.line_spacing:
            style.leading = (
                style.fontSize * block_style.line_spacing
                if block_style.line_spacing <= 4
                else block_style.line_spacing
            )
        if block_style.indent_left is not None:
            style.leftIndent = (style.leftIndent or 0) + block_style.indent_left
        if block_style.indent_right is not None:
            style.rightIndent = (style.rightIndent or 0) + block_style.indent_right
        if block_style.first_line_indent is not None:
            style.firstLineIndent = block_style.first_line_indent
        if block_style.background:
            style.backColor = block_style.background
        return style

    # -- inline markup --------------------------------------------------------
    def _markup(self, content: Sequence[Inline]) -> str:
        return "".join(self._inline(node) for node in content)

    def _inline(self, node: Inline) -> str:
        if isinstance(node, Text):
            return self._styled(node.text, node.style)
        if isinstance(node, Link):
            inner = self._markup(node.content) or _escape(node.href)
            href = _escape(node.href, quote=True)
            return f'<a href="{href}" color="{self.link_colour}">{inner}</a>'
        if isinstance(node, LineBreak):
            return "<br/>"
        if isinstance(node, InlineImage):
            return self._inline_image(node)
        if isinstance(node, Math):
            return self._styled(node.latex, TextStyle(italic=True))
        if isinstance(node, FootnoteRef):
            return f"<super>{_escape(node.label or node.identifier)}</super>"
        if isinstance(node, DynamicField):
            return _escape(node.fallback)
        return _escape(node.text)

    def _styled(self, text: str, style: TextStyle) -> str:
        if not text:
            return ""
        markup = _escape(text)

        font_attrs: List[str] = []
        if style.is_monospace:
            font_attrs.append(f'face="{_MONO_FONT}"')
        elif style.font_family:
            font_attrs.append(f'face="{_escape(style.font_family, quote=True)}"')
        if style.font_size:
            font_attrs.append(f'size="{style.font_size:g}"')
        if style.color:
            font_attrs.append(f'color="{style.color}"')
        if style.background:
            font_attrs.append(f'backColor="{style.background}"')
        if font_attrs:
            markup = f"<font {' '.join(font_attrs)}>{markup}</font>"

        if style.superscript:
            markup = f"<super>{markup}</super>"
        if style.subscript:
            markup = f"<sub>{markup}</sub>"
        if style.strike:
            markup = f"<strike>{markup}</strike>"
        if style.underline:
            markup = f"<u>{markup}</u>"
        if style.italic:
            markup = f"<i>{markup}</i>"
        if style.bold:
            markup = f"<b>{markup}</b>"
        return markup

    def _inline_image(self, node: InlineImage) -> str:
        """Embed an inline image inside a paragraph via ReportLab's ``<img>`` markup.

        The bytes are spilled to a temporary file and referenced by path, which is the
        only mechanism every ReportLab version supports. A ``data:`` URI is tempting
        since the bytes are already in memory, and it works on 4.x -- but 5.x fails to
        resolve one (raising an internal ``UnboundLocalError`` from its URL reader), so
        the URI approach silently breaks depending on which ReportLab a user happens to
        have. The files are cleaned up in :meth:`build`, after the document is laid out.

        Any failure degrades to the alt text rather than aborting the whole document --
        one unreadable logo should not cost you the conversion.
        """
        if not (self.embed_images and node.data):
            return self._image_fallback(node)

        try:
            from io import BytesIO

            from reportlab.lib.utils import ImageReader

            natural_width, natural_height = ImageReader(BytesIO(node.data)).getSize()
            if not natural_width or not natural_height:
                return self._image_fallback(node)

            width, height = self._fit_inline(
                node.width, node.height, natural_width, natural_height
            )
            path = self._spill(node.data, node.mime_type)
            if path is None:
                return self._image_fallback(node)
            return (
                f'<img src="{path}" '
                f'width="{width:g}" height="{height:g}" valign="middle"/>'
            )
        except Exception:
            return self._image_fallback(node)

    def _spill(self, data: bytes, mime_type: Optional[str]) -> Optional[str]:
        """Write image bytes to a temp file and return a markup-safe path.

        Identical images are spilled once and reused, so a logo repeated on every page
        costs one file rather than dozens.
        """
        import hashlib
        import tempfile

        digest = hashlib.sha1(data).hexdigest()
        existing = self._spilled.get(digest)
        if existing is not None:
            return existing

        suffix = _IMAGE_SUFFIXES.get((mime_type or "").lower(), ".png")
        try:
            handle = tempfile.NamedTemporaryFile(
                prefix="polydoc_img_", suffix=suffix, delete=False
            )
            with handle:
                handle.write(data)
        except OSError:
            return None

        # Forward slashes and no quotes: the path goes inside an XML-ish attribute.
        path = handle.name.replace("\\", "/")
        if '"' in path:
            return None
        self._spilled[digest] = path
        return path

    def _cleanup_spilled(self) -> None:
        """Remove the temp files created for inline images."""
        import os

        for path in self._spilled.values():
            try:
                os.unlink(path)
            except OSError:  # pragma: no cover - already gone, or locked
                pass
        self._spilled.clear()

    def _fit_inline(
        self,
        width: Optional[float],
        height: Optional[float],
        natural_width: float,
        natural_height: float,
    ) -> Tuple[float, float]:
        """Resolve an inline image's size, preserving aspect ratio.

        The model may carry one dimension, both, or neither. A missing dimension is
        derived from the other so a logo is never stretched, and the result is capped to
        the printable width so an oversized image cannot overflow the frame.
        """
        aspect = natural_height / natural_width
        if width and height:
            resolved_width, resolved_height = float(width), float(height)
        elif width:
            resolved_width, resolved_height = float(width), float(width) * aspect
        elif height:
            resolved_width, resolved_height = float(height) / aspect, float(height)
        else:
            resolved_width, resolved_height = float(natural_width), float(natural_height)

        ceiling = getattr(self, "frame_width", 468.0)
        if resolved_width > ceiling:
            scale = ceiling / resolved_width
            resolved_width, resolved_height = resolved_width * scale, resolved_height * scale
        return (resolved_width, resolved_height)

    @staticmethod
    def _image_fallback(node: Any) -> str:
        """What to show when an image cannot be embedded."""
        label = getattr(node, "alt", "") or getattr(node, "caption", "") or ""
        return _escape(f"[{label}]") if label else ""

    # -- lists ----------------------------------------------------------------
    def _list(self, block: ListBlock, depth: int = 0) -> Any:
        from reportlab.platypus import ListFlowable, ListItem as RLListItem

        items: List[Any] = []
        for item in block.items:
            flowables: List[Any] = []
            for child in item.content:
                if isinstance(child, ListBlock):
                    flowables.append(self._list(child, depth + 1))
                elif isinstance(child, Paragraph):
                    content: List[Inline] = list(child.content)
                    if item.checked is not None:
                        glyph = "\u2612 " if item.checked else "\u2610 "
                        content = [Text(glyph)] + content
                    flowables.append(
                        self._paragraph(content, self._styles["body"], child.style)
                    )
                else:
                    flowables.extend(self._flowables(child, depth + 1))
            if not flowables:
                continue
            items.append(RLListItem(flowables, leftIndent=0))

        bullet_type = _BULLET_TYPES.get(block.marker_style, "bullet")
        kwargs: Dict[str, Any] = {
            "bulletType": bullet_type,
            "leftIndent": 16 + depth * 14,
            "bulletFontName": self.base_font,
            "bulletFontSize": self.base_size,
            "spaceBefore": 2,
            "spaceAfter": self.base_size * 0.5,
        }
        if block.marker_style is ListStyle.BULLET:
            kwargs["start"] = _BULLET_GLYPHS[depth % len(_BULLET_GLYPHS)]
        elif block.marker_style is ListStyle.NONE:
            kwargs["bulletType"] = "bullet"
            kwargs["start"] = " "
        elif block.start != 1:
            kwargs["start"] = block.start
        # Numbered lists read better with the separator PowerPoint and Word use.
        if block.marker_style.is_ordered:
            kwargs["bulletFormat"] = "%s."

        return ListFlowable(items, **kwargs)

    # -- tables ---------------------------------------------------------------
    def _table(self, block: Table) -> List[Any]:
        from reportlab.platypus import KeepTogether, Table as RLTable, TableStyle

        rows, columns = block.dimensions
        if not rows or not columns:
            return []

        grid: List[List[Any]] = []
        spans: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []
        occupied: Dict[Tuple[int, int], bool] = {}
        cell_styles: List[Tuple[str, Tuple[int, int], Tuple[int, int], Any]] = []

        for row_index, row in enumerate(block.rows):
            line: List[Any] = [""] * columns
            column = 0
            for cell in row.cells:
                while column < columns and occupied.get((row_index, column)):
                    column += 1
                if column >= columns:
                    break
                line[column] = self._cell(cell, header=row.is_header)

                end_column = min(columns - 1, column + cell.colspan - 1)
                end_row = min(rows - 1, row_index + cell.rowspan - 1)
                if end_column > column or end_row > row_index:
                    spans.append(((column, row_index), (end_column, end_row)))
                for r in range(row_index, end_row + 1):
                    for c in range(column, end_column + 1):
                        occupied[(r, c)] = True

                if cell.background:
                    cell_styles.append(
                        ("BACKGROUND", (column, row_index), (end_column, end_row), cell.background)
                    )
                if cell.valign is not None:
                    code = _VALIGN_CODES.get(cell.valign)
                    if code:
                        cell_styles.append(
                            ("VALIGN", (column, row_index), (end_column, end_row), code)
                        )
                column = end_column + 1
            grid.append(line)

        widths = self._column_widths(block, columns, grid)
        table = RLTable(grid, colWidths=widths, repeatRows=block.header_rows or 0)

        commands: List[Tuple[Any, ...]] = [
            ("GRID", (0, 0), (-1, -1), 0.4, "#b9c0c8"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        if block.header_rows:
            commands.append(("BACKGROUND", (0, 0), (-1, block.header_rows - 1), "#eef1f4"))
            commands.append(
                ("LINEBELOW", (0, block.header_rows - 1), (-1, block.header_rows - 1), 0.8, "#8f99a3")
            )
        commands.extend(cell_styles)
        for start, end in spans:
            commands.append(("SPAN", start, end))
        table.setStyle(TableStyle(commands))

        out: List[Any] = [table]
        if block.caption:
            out.append(
                self._paragraph(
                    [Text(block.caption)], self._styles["caption"], ParagraphStyle()
                )
            )
        else:
            from reportlab.platypus import Spacer

            out.append(Spacer(1, self.base_size * 0.7))
        return [KeepTogether(out)] if rows <= 12 else out

    def _cell(self, cell: TableCell, header: bool) -> Any:
        style = self._styles["cellhead"] if header else self._styles["cell"]
        blocks = list(cell.content)
        if not blocks:
            return ""
        if len(blocks) == 1 and isinstance(blocks[0], Paragraph):
            return self._paragraph(blocks[0].content, style, blocks[0].style)
        flowables: List[Any] = []
        for child in blocks:
            if isinstance(child, Paragraph):
                flowables.append(self._paragraph(child.content, style, child.style))
            else:
                flowables.extend(self._flowables(child))
        return flowables or ""

    def _column_widths(
        self,
        block: Table,
        columns: int,
        grid: Sequence[Sequence[Any]],
    ) -> List[float]:
        """Resolve column widths, always summing to the printable width.

        Platypus will overflow the page rather than shrink a table, so leaving widths
        to it is not an option for content of unknown size.
        """
        available = self.frame_width
        declared = block.column_widths

        if declared and len(declared) >= columns and any(w > 0 for w in declared[:columns]):
            weights = [max(w, 1.0) for w in declared[:columns]]
        else:
            # Measure the text so wide columns get proportionally more room, with a
            # cap so one long cell cannot starve the rest.
            weights = []
            for index in range(columns):
                longest = 1
                for row in grid:
                    value = row[index] if index < len(row) else ""
                    text = _flowable_text(value)
                    longest = max(longest, min(len(text), 60))
                weights.append(float(max(4, longest)))

        total = sum(weights)
        if total <= 0:
            return [available / columns] * columns
        scaled = [available * (w / total) for w in weights]
        # Enforce a readable minimum, then re-normalise.
        minimum = min(28.0, available / columns)
        scaled = [max(minimum, w) for w in scaled]
        factor = available / sum(scaled)
        return [w * factor for w in scaled]

    # -- images ---------------------------------------------------------------
    def _image(self, block: Image) -> List[Any]:
        from reportlab.platypus import Spacer

        out: List[Any] = []
        placed = False
        if self.embed_images and block.data:
            try:
                from io import BytesIO

                from reportlab.lib.utils import ImageReader
                from reportlab.platypus import Image as RLImage

                natural_width, natural_height = ImageReader(BytesIO(block.data)).getSize()
                if natural_width and natural_height:
                    # Shared with the inline path so a figure and a logo scale alike.
                    width, height = self._fit_inline(
                        block.width, block.height, natural_width, natural_height
                    )
                    flowable = RLImage(BytesIO(block.data), width=width, height=height)
                    # ReportLab centres images by default. Honour the block's own
                    # alignment instead, falling back to LEFT so a left-aligned logo in
                    # the source does not drift to the middle of the page.
                    flowable.hAlign = _IMAGE_ALIGN.get(block.style.alignment, "LEFT")
                    out.append(flowable)
                    placed = True
            except Exception:
                # A single unreadable image must not cost the whole document.
                placed = False

        if not placed:
            label = block.alt or block.caption or block.src or "image"
            out.append(
                self._paragraph(
                    [Text(f"[Image: {label}]", TextStyle(italic=True))],
                    self._styles["caption"],
                    ParagraphStyle(),
                )
            )
        if block.caption:
            out.append(
                self._paragraph(
                    [Text(block.caption)], self._styles["caption"], ParagraphStyle()
                )
            )
        else:
            out.append(Spacer(1, 6))
        return out

    # -- footnotes ------------------------------------------------------------
    def _footnotes(self) -> List[Any]:
        from reportlab.platypus import HRFlowable, Spacer

        out: List[Any] = [Spacer(1, 12), HRFlowable(width="35%", thickness=0.6, color="#999999")]
        for note in self.document.footnotes:
            for index, child in enumerate(note.content):
                if isinstance(child, Paragraph):
                    content: List[Inline] = list(child.content)
                    if index == 0:
                        content = [
                            Text(note.identifier, TextStyle(superscript=True)),
                            Text(" "),
                        ] + content
                    out.append(
                        self._paragraph(content, self._styles["footnote"], child.style)
                    )
                else:
                    out.extend(self._flowables(child))
        return out


class _OutlineTemplate:
    """Wraps ``SimpleDocTemplate`` to register a bookmark per heading.

    Implemented as a delegating wrapper rather than a subclass so the ReportLab import
    stays inside the function that needs it, keeping the module importable when
    ReportLab is absent.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from reportlab.platypus import SimpleDocTemplate

        outer = self

        class _Template(SimpleDocTemplate):
            def afterFlowable(self, flowable: Any) -> None:  # noqa: N802
                if not outer.enable_outline:
                    return
                style = getattr(flowable, "style", None)
                name = getattr(style, "name", "") or ""
                if not name.startswith("PDHeading"):
                    return
                digits = "".join(ch for ch in name if ch.isdigit())
                if not digits:
                    return
                try:
                    text = flowable.getPlainText().strip()
                except Exception:  # pragma: no cover
                    return
                if not text:
                    return

                requested = int(digits) - 1
                # ReportLab raises when an outline level jumps by more than one.
                level = min(requested, outer._last_level + 1)
                level = max(0, level)
                key = f"pd-outline-{outer._counter}"
                outer._counter += 1
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text[:120], key, level=level, closed=False)
                outer._last_level = level

        self.enable_outline = True
        self._counter = 0
        self._last_level = -1
        self._template = _Template(*args, **kwargs)

    def build(self, story: List[Any], **kwargs: Any) -> None:
        self._template.build(story, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._template, name)


class _PageDecorator:
    """Draws the page furniture: a page number, and a title in the footer."""

    def __init__(self, document: Document, page_numbers: bool, font: str) -> None:
        self.document = document
        self.page_numbers = page_numbers
        self.font = font

    def __call__(self, canvas: Any, doc: Any) -> None:
        if not self.page_numbers:
            return
        canvas.saveState()
        canvas.setFont(self.font, 8.5)
        canvas.setFillColor("#777777")
        width, _ = canvas._pagesize
        canvas.drawCentredString(width / 2.0, 24, str(doc.page))
        title = self.document.metadata.title
        if title:
            canvas.drawString(doc.leftMargin, 24, title[:70])
        canvas.restoreState()


def _escape(text: str, quote: bool = False) -> str:
    """Escape for ReportLab's mini-markup, which is XML-like."""
    return _html.escape(text, quote=quote)


def _flowable_text(value: Any) -> str:
    """Best-effort plain text of a grid entry, for width measurement."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_flowable_text(item) for item in value)
    getter = getattr(value, "getPlainText", None)
    if callable(getter):
        try:
            return getter()
        except Exception:  # pragma: no cover
            return ""
    return str(value)
