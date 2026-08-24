"""DOCX reader, built on python-docx.

Three things need real work beyond a tag walk:

**Document order.** ``python-docx``'s ``paragraphs`` and ``tables`` collections are
separate, which loses interleaving. ``iter_inner_content()`` yields both in true order,
so that is what we use throughout (body, cells, headers).

**Lists.** Word has no list element -- a list is a run of consecutive paragraphs whose
``numPr`` points at a numbering definition. We collect those runs and rebuild real
nested :class:`~polydoc.model.ListBlock` trees, resolving bullet-versus-ordered from
``numbering.xml`` rather than guessing from the style name.

**Merged cells.** ``gridSpan`` gives horizontal spans directly; vertical merges are
expressed as a ``vMerge`` "continue" marker on later rows, which we fold into the
originating cell's ``rowspan``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ...intelligence.structure import coalesce_code_blocks
from ...model import (
    Alignment,
    Block,
    CodeBlock,
    Container,
    Document,
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
    PageBreak,
    PageGeometry,
    Paragraph,
    ParagraphStyle,
    Quote,
    Size,
    Table,
    TableCell,
    TableRow,
    Text,
    TextStyle,
    VerticalAlign,
    merge_runs,
)
from ..base import Reader, require
from ..registry import register_reader
from ..source import Source

__all__ = ["DocxReader"]

_HEADING_RE = re.compile(r"^(?:heading|kop|titre|berschrift|encabezado)\s*(\d)$", re.I)
_LIST_STYLE_RE = re.compile(r"^list\s*(bullet|number|paragraph)", re.I)

#: Word numbering formats mapped onto the universal list styles.
_NUM_FMT = {
    "bullet": ListStyle.BULLET,
    "decimal": ListStyle.ORDERED,
    "decimalZero": ListStyle.ORDERED,
    "ordinal": ListStyle.ORDERED,
    "cardinalText": ListStyle.ORDERED,
    "lowerLetter": ListStyle.LOWER_ALPHA,
    "upperLetter": ListStyle.UPPER_ALPHA,
    "lowerRoman": ListStyle.LOWER_ROMAN,
    "upperRoman": ListStyle.UPPER_ROMAN,
    "none": ListStyle.NONE,
}

#: Character styles that imply formatting without setting run properties.
_CHAR_STYLES = {
    "strong": TextStyle(bold=True),
    "emphasis": TextStyle(italic=True),
    "intenseemphasis": TextStyle(italic=True, bold=True),
    "subtleemphasis": TextStyle(italic=True),
    "booktitle": TextStyle(italic=True),
    "hyperlink": TextStyle(underline=True),
    "code": TextStyle(code=True),
    "htmlcode": TextStyle(code=True),
    "verbatimchar": TextStyle(code=True),
}


class _ListEntry:
    """A paragraph that belongs to a list, held until the run is complete."""

    __slots__ = ("level", "style", "num_id", "blocks")

    def __init__(self, level: int, style: ListStyle, num_id: Optional[str], blocks: List[Block]):
        self.level = level
        self.style = style
        self.num_id = num_id
        self.blocks = blocks


@register_reader
class DocxReader(Reader):
    """Reads Word documents (``.docx``, ``.docm``)."""

    format = "docx"
    extensions = (".docx", ".docm")
    aliases = ("word",)
    mime_types = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    extra = "docx"
    archive_based = True
    description = "Microsoft Word (Open XML)"

    def read(self, source: Source, **options: Any) -> Document:
        self.enforce_limits(source, **options)
        docx = require("docx", "Reading DOCX", extra="docx", package="python-docx")
        from docx.table import Table as DocxTable  # noqa: N814
        from docx.text.paragraph import Paragraph as DocxParagraph

        handle = source.open_path()
        native = docx.Document(str(handle) if hasattr(handle, "suffix") else handle)

        parser = _DocxParser(
            native,
            docx,
            DocxParagraph,
            DocxTable,
            keep_empty=options.get("keep_empty_paragraphs", False),
            extract_images=options.get("extract_images", True),
        )

        blocks = parser.blocks(native)
        if options.get("detect_code_blocks", True):
            blocks = coalesce_code_blocks(blocks)
        if options.get("attach_captions", True):
            blocks = _attach_captions(blocks)

        document = Document()
        document.body.extend(blocks)
        document.adopt(*document.body)
        document.footnotes.extend(parser.footnotes())
        document.adopt(*document.footnotes)

        self._read_metadata(native, document)
        document.geometry = parser.geometry()

        if options.get("include_headers", False):
            headers, footers = parser.headers_and_footers()
            if headers:
                document.attrs["headers"] = headers
            if footers:
                document.attrs["footers"] = footers

        if document.metadata.title is None:
            for block in document.body:
                if isinstance(block, Heading):
                    document.metadata.title = block.text
                    break

        return self.finalise(document, source)

    @staticmethod
    def _read_metadata(native: Any, document: Document) -> None:
        props = native.core_properties
        meta = document.metadata
        meta.title = props.title or None
        if props.author:
            meta.authors = [p.strip() for p in props.author.split(";") if p.strip()] or [
                props.author
            ]
        meta.subject = props.subject or None
        if props.keywords:
            meta.keywords = [p.strip() for p in re.split(r"[;,]", props.keywords) if p.strip()]
        meta.description = props.comments or None
        meta.category = props.category or None
        meta.language = props.language or None
        meta.created = props.created
        meta.modified = props.modified
        if props.last_modified_by:
            meta.custom["last_modified_by"] = props.last_modified_by
        if props.revision:
            meta.custom["revision"] = props.revision


class _DocxParser:
    """Holds the per-document state the conversion needs."""

    def __init__(
        self,
        native: Any,
        docx_module: Any,
        paragraph_cls: Any,
        table_cls: Any,
        keep_empty: bool = False,
        extract_images: bool = True,
    ) -> None:
        self.native = native
        self.docx = docx_module
        self.Paragraph = paragraph_cls
        self.Table = table_cls
        self.keep_empty = keep_empty
        self.extract_images = extract_images

        from docx.oxml.ns import qn

        self.qn = qn
        self._numbering = self._load_numbering()
        self._footnote_bodies: Dict[str, List[Block]] = {}
        self._footnotes_loaded = False

    # -- numbering ------------------------------------------------------------
    def _load_numbering(self) -> Dict[Tuple[str, int], ListStyle]:
        """Resolve ``(numId, ilvl) -> ListStyle`` from ``numbering.xml``.

        Reading the real numbering definitions is what lets an ``a) b) c)`` list survive
        as :attr:`ListStyle.LOWER_ALPHA` instead of collapsing to generic ordering.
        """
        mapping: Dict[Tuple[str, int], ListStyle] = {}
        try:
            part = self.native.part.numbering_part
        except (AttributeError, KeyError, NotImplementedError):
            return mapping
        if part is None:
            return mapping

        qn = self.qn
        root = part.element

        abstract: Dict[str, Dict[int, ListStyle]] = {}
        for node in root.findall(qn("w:abstractNum")):
            abstract_id = node.get(qn("w:abstractNumId"))
            if abstract_id is None:
                continue
            levels: Dict[int, ListStyle] = {}
            for lvl in node.findall(qn("w:lvl")):
                try:
                    ilvl = int(lvl.get(qn("w:ilvl")) or 0)
                except ValueError:
                    continue
                fmt_node = lvl.find(qn("w:numFmt"))
                fmt = fmt_node.get(qn("w:val")) if fmt_node is not None else None
                levels[ilvl] = _NUM_FMT.get(fmt or "", ListStyle.BULLET)
            abstract[abstract_id] = levels

        for node in root.findall(qn("w:num")):
            num_id = node.get(qn("w:numId"))
            ref = node.find(qn("w:abstractNumId"))
            if num_id is None or ref is None:
                continue
            levels = abstract.get(ref.get(qn("w:val")) or "", {})
            for ilvl, style in levels.items():
                mapping[(num_id, ilvl)] = style
        return mapping

    # -- block assembly -------------------------------------------------------
    def blocks(self, container: Any) -> List[Block]:
        """Convert a body/cell/header container into model blocks, in order."""
        out: List[Block] = []
        pending: List[_ListEntry] = []

        def flush() -> None:
            if pending:
                out.extend(_assemble_lists(pending))
                pending.clear()

        for item in self._iter_content(container):
            if isinstance(item, self.Paragraph):
                produced = self.paragraph(item)
                if produced is None:
                    continue
                if isinstance(produced, _ListEntry):
                    pending.append(produced)
                    continue
                flush()
                out.extend(produced)
            elif isinstance(item, self.Table):
                flush()
                out.append(self.table(item))
        flush()
        return out

    def _iter_content(self, container: Any) -> Iterable[Any]:
        """Prefer true document order; fall back for older python-docx builds."""
        if hasattr(container, "iter_inner_content"):
            return container.iter_inner_content()
        return list(getattr(container, "paragraphs", [])) + list(
            getattr(container, "tables", [])
        )

    # -- paragraphs -----------------------------------------------------------
    def paragraph(self, para: Any) -> Any:
        """Return ``None``, a list of blocks, or a :class:`_ListEntry`."""
        content = self.inline_content(para)
        style_name = self._style_name(para)

        has_break = self._has_page_break(para)
        text_empty = not "".join(
            node.text for node in content if not isinstance(node, InlineImage)
        ).strip()
        has_media = any(isinstance(node, InlineImage) for node in content)

        if text_empty and not has_media:
            # Check for a rule first: Word draws one as a bordered empty paragraph,
            # so the empty-paragraph path below would otherwise discard it.
            if self._is_horizontal_rule(para):
                return ([PageBreak()] if has_break else []) + [HorizontalRule()]
            if has_break:
                return [PageBreak()]
            return [Paragraph([])] if self.keep_empty else None

        # A paragraph holding only an image is a block-level image. The paragraph's
        # own alignment is carried across, since that is what positions a figure or a
        # logo on the page; dropping it would recentre everything on export.
        if has_media and text_empty and len(content) == 1:
            picture = content[0]
            block: Block = Image(
                src=picture.src,
                data=picture.data,
                alt=picture.alt,
                width=picture.width,
                height=picture.height,
                mime_type=picture.mime_type,
                style=self.paragraph_style(para, style_name),
            )
            return ([PageBreak()] if has_break else []) + [block]

        prefix: List[Block] = [PageBreak()] if has_break else []
        para_style = self.paragraph_style(para, style_name)

        list_info = self._list_info(para, style_name)
        if list_info is not None:
            level, marker_style = list_info
            entry_blocks: List[Block] = [Paragraph(content, para_style)]
            if prefix:
                # A page break inside a list is rare; keep it before the item.
                return prefix + [ListBlock([ListItem(entry_blocks)], marker_style)]
            return _ListEntry(level, marker_style, self._num_id(para), entry_blocks)

        heading_level = self._heading_level(style_name)
        if heading_level is not None:
            return prefix + [Heading(content, heading_level, para_style)]

        lowered = (style_name or "").lower().replace(" ", "")
        if lowered in ("quote", "intensequote", "blockquote"):
            return prefix + [Quote([Paragraph(content, para_style)])]
        if lowered in ("code", "htmlpreformatted", "sourcecode", "preformattedtext"):
            return prefix + [CodeBlock("".join(n.text for n in content))]

        block = Paragraph(content, para_style)
        if style_name and style_name not in ("Normal", "Body Text"):
            block.attrs["docx_style"] = style_name
        return prefix + [block]

    @staticmethod
    def _style_name(para: Any) -> Optional[str]:
        try:
            return para.style.name if para.style is not None else None
        except (AttributeError, KeyError):
            return None

    @staticmethod
    def _heading_level(style_name: Optional[str]) -> Optional[int]:
        if not style_name:
            return None
        normalised = style_name.strip()
        match = _HEADING_RE.match(normalised)
        if match:
            return min(6, max(1, int(match.group(1))))
        collapsed = normalised.lower().replace(" ", "")
        if collapsed == "title":
            return 1
        if collapsed == "subtitle":
            return 2
        # Style ids arrive without the space, e.g. "Heading3".
        match = re.match(r"^heading(\d)$", collapsed)
        if match:
            return min(6, max(1, int(match.group(1))))
        return None

    def _list_info(self, para: Any, style_name: Optional[str]) -> Optional[Tuple[int, ListStyle]]:
        """Detect list membership and resolve its level and marker style."""
        qn = self.qn
        p_pr = para._p.pPr
        num_pr = p_pr.numPr if p_pr is not None else None

        if num_pr is None:
            # "List Bullet"/"List Number" styles sometimes carry numbering implicitly.
            match = _LIST_STYLE_RE.match(style_name or "")
            if match and match.group(1).lower() in ("bullet", "number"):
                style = (
                    ListStyle.BULLET
                    if match.group(1).lower() == "bullet"
                    else ListStyle.ORDERED
                )
                return (0, style)
            return None

        level = 0
        ilvl = num_pr.find(qn("w:ilvl"))
        if ilvl is not None:
            try:
                level = int(ilvl.get(qn("w:val")) or 0)
            except ValueError:
                level = 0

        num_id = self._num_id(para)
        style = self._numbering.get((num_id or "", level))
        if style is None:
            match = _LIST_STYLE_RE.match(style_name or "")
            if match and match.group(1).lower() == "number":
                style = ListStyle.ORDERED
            else:
                style = ListStyle.BULLET
        return (level, style)

    def _num_id(self, para: Any) -> Optional[str]:
        qn = self.qn
        p_pr = para._p.pPr
        num_pr = p_pr.numPr if p_pr is not None else None
        if num_pr is None:
            return None
        node = num_pr.find(qn("w:numId"))
        return node.get(qn("w:val")) if node is not None else None

    def _has_page_break(self, para: Any) -> bool:
        qn = self.qn
        for br in para._p.iter(qn("w:br")):
            if br.get(qn("w:type")) == "page":
                return True
        p_pr = para._p.pPr
        if p_pr is not None:
            page_break_before = p_pr.find(qn("w:pageBreakBefore"))
            if page_break_before is not None:
                value = page_break_before.get(qn("w:val"))
                if value in (None, "1", "true", "on"):
                    return True
        return False

    def _is_horizontal_rule(self, para: Any) -> bool:
        """Word draws a rule as a bottom border on an empty paragraph."""
        qn = self.qn
        if para.text.strip():
            return False
        p_pr = para._p.pPr
        if p_pr is None:
            return False
        borders = p_pr.find(qn("w:pBdr"))
        if borders is None:
            return False
        bottom = borders.find(qn("w:bottom"))
        return bottom is not None and bottom.get(qn("w:val")) not in (None, "none", "nil")

    def paragraph_style(self, para: Any, style_name: Optional[str]) -> ParagraphStyle:
        fmt = para.paragraph_format
        return ParagraphStyle(
            alignment=_alignment(fmt.alignment),
            space_before=_points(fmt.space_before),
            space_after=_points(fmt.space_after),
            line_spacing=_line_spacing(fmt.line_spacing),
            indent_left=_points(fmt.left_indent),
            indent_right=_points(fmt.right_indent),
            first_line_indent=_points(fmt.first_line_indent),
            style_name=style_name,
        )

    # -- inline ---------------------------------------------------------------
    def inline_content(self, para: Any) -> List[Inline]:
        """Build inline content, keeping hyperlinks and runs in document order."""
        out: List[Inline] = []
        if hasattr(para, "iter_inner_content"):
            for item in para.iter_inner_content():
                if hasattr(item, "address") or hasattr(item, "url"):
                    out.extend(self._hyperlink(item))
                else:
                    out.extend(self._run(item))
        else:  # pragma: no cover - older python-docx
            for run in para.runs:
                out.extend(self._run(run))
        return merge_runs(out)

    def _hyperlink(self, link: Any) -> List[Inline]:
        href = getattr(link, "address", None) or getattr(link, "url", "") or ""
        fragment = getattr(link, "fragment", None)
        if fragment:
            href = f"{href}#{fragment}" if href else f"#{fragment}"
        content: List[Inline] = []
        for run in getattr(link, "runs", []):
            content.extend(_strip_link_decoration(self._run(run)))
        content = merge_runs(content)
        if not content:
            return []
        if not href:
            return content
        return [Link(content, href=href)]

    def _run(self, run: Any) -> List[Inline]:
        qn = self.qn
        style = self.run_style(run)
        out: List[Inline] = []

        for node in run._r:
            tag = node.tag
            if tag == qn("w:t"):
                if node.text:
                    out.append(Text(node.text, style))
            elif tag == qn("w:tab"):
                out.append(Text("\t", style))
            elif tag == qn("w:br"):
                if node.get(qn("w:type")) != "page":
                    out.append(LineBreak())
            elif tag == qn("w:cr"):
                out.append(LineBreak())
            elif tag == qn("w:noBreakHyphen"):
                out.append(Text("\u2011", style))
            elif tag == qn("w:softHyphen"):
                out.append(Text("\u00ad", style))
            elif tag == qn("w:sym"):
                char = node.get(qn("w:char"))
                if char:
                    try:
                        out.append(Text(chr(int(char, 16)), style))
                    except ValueError:
                        pass
            elif tag in (qn("w:drawing"), qn("w:pict")):
                picture = self._picture(run, node)
                if picture is not None:
                    out.append(picture)
            elif tag == qn("w:footnoteReference") or tag == qn("w:endnoteReference"):
                ident = node.get(qn("w:id"))
                if ident:
                    out.append(FootnoteRef(str(ident)))
        return out

    def run_style(self, run: Any) -> TextStyle:
        font = run.font
        style = TextStyle(
            bold=run.bold,
            italic=run.italic,
            underline=True if run.underline else None,
            strike=font.strike or (True if font.double_strike else None),
            small_caps=font.small_caps,
            superscript=font.superscript,
            subscript=font.subscript,
            font_family=font.name,
            font_size=font.size.pt if font.size is not None else None,
            color=_run_colour(font),
            highlight=True if font.highlight_color is not None else None,
        )
        # A character style can imply formatting the run properties do not set.
        try:
            char_style = run.style.name if run.style is not None else None
        except (AttributeError, KeyError):
            char_style = None
        if char_style:
            implied = _CHAR_STYLES.get(char_style.lower().replace(" ", ""))
            if implied is not None:
                style = implied.merge(style)
        if style.font_family and style.code is None and style.is_monospace:
            style = style.merge(TextStyle(code=True))
        return style

    def _picture(self, run: Any, node: Any) -> Optional[InlineImage]:
        """Resolve a drawing to its embedded bytes via the relationship id."""
        qn = self.qn
        blip = node.find(f".//{qn('a:blip')}")
        rel_id = blip.get(qn("r:embed")) if blip is not None else None
        if rel_id is None:
            # VML fallback used by older documents.
            for image_data in node.iter(qn("v:imagedata")):
                rel_id = image_data.get(qn("r:id"))
                if rel_id:
                    break
        if rel_id is None:
            return None

        alt = ""
        for doc_pr in node.iter(qn("wp:docPr")):
            alt = doc_pr.get("descr") or doc_pr.get("name") or ""
            break

        width = height = None
        for extent in node.iter(qn("wp:extent")):
            try:
                width = _emu_to_points(int(extent.get("cx")))
                height = _emu_to_points(int(extent.get("cy")))
            except (TypeError, ValueError):
                pass
            break

        data = None
        mime = None
        src = rel_id
        try:
            part = run.part.related_parts[rel_id]
            src = getattr(part, "partname", rel_id)
            src = str(src).rsplit("/", 1)[-1]
            mime = getattr(part, "content_type", None)
            if self.extract_images:
                data = part.blob
        except (KeyError, AttributeError):
            pass

        return InlineImage(
            src=src, alt=alt, data=data, width=width, height=height, mime_type=mime
        )

    # -- tables ---------------------------------------------------------------
    def table(self, native_table: Any) -> Table:
        qn = self.qn
        rows: List[TableRow] = []
        # Tracks the cell each column is vertically merged into.
        open_merges: Dict[int, TableCell] = {}

        for native_row in native_table.rows:
            cells: List[TableCell] = []
            column = 0
            for native_cell in native_row.cells:
                tc_pr = native_cell._tc.tcPr
                span = 1
                v_merge_continue = False
                if tc_pr is not None:
                    grid_span = tc_pr.find(qn("w:gridSpan"))
                    if grid_span is not None:
                        try:
                            span = max(1, int(grid_span.get(qn("w:val")) or 1))
                        except ValueError:
                            span = 1
                    v_merge = tc_pr.find(qn("w:vMerge"))
                    if v_merge is not None:
                        value = v_merge.get(qn("w:val"))
                        v_merge_continue = value in (None, "continue")

                if v_merge_continue and column in open_merges:
                    open_merges[column].rowspan += 1
                    column += span
                    continue

                cell = TableCell(
                    self.blocks(native_cell),
                    colspan=span,
                    valign=_cell_valign(tc_pr, qn),
                    background=_cell_shading(tc_pr, qn),
                )
                cells.append(cell)
                if tc_pr is not None and tc_pr.find(qn("w:vMerge")) is not None:
                    open_merges[column] = cell
                else:
                    open_merges.pop(column, None)
                column += span

            # python-docx repeats merged cells; drop the duplicates it yields.
            deduped: List[TableCell] = []
            for cell in cells:
                if deduped and cell is deduped[-1]:
                    continue
                deduped.append(cell)

            row = TableRow(deduped, is_header=self._is_header_row(native_row))
            rows.append(row)

        table = Table(rows)
        widths = self._column_widths(native_table)
        if widths:
            table.column_widths = widths
        try:
            if native_table.style is not None and native_table.style.name:
                table.attrs["docx_style"] = native_table.style.name
        except (AttributeError, KeyError):
            pass
        return table

    def _is_header_row(self, native_row: Any) -> bool:
        qn = self.qn
        tr_pr = native_row._tr.trPr
        if tr_pr is None:
            return False
        header = tr_pr.find(qn("w:tblHeader"))
        if header is None:
            return False
        return header.get(qn("w:val")) in (None, "1", "true", "on")

    def _column_widths(self, native_table: Any) -> Optional[List[float]]:
        qn = self.qn
        grid = native_table._tbl.find(qn("w:tblGrid"))
        if grid is None:
            return None
        widths: List[float] = []
        for col in grid.findall(qn("w:gridCol")):
            raw = col.get(qn("w:w"))
            try:
                widths.append(round(int(raw) / 20.0, 2))  # twentieths of a point
            except (TypeError, ValueError):
                widths.append(0.0)
        return widths if any(widths) else None

    # -- document furniture ---------------------------------------------------
    def geometry(self) -> Optional[PageGeometry]:
        try:
            section = self.native.sections[0]
        except (IndexError, AttributeError):
            return None
        width = _points(section.page_width)
        height = _points(section.page_height)
        if not width or not height:
            return None
        return PageGeometry(
            size=Size(width, height),
            margin_left=_points(section.left_margin) or 72.0,
            margin_right=_points(section.right_margin) or 72.0,
            margin_top=_points(section.top_margin) or 72.0,
            margin_bottom=_points(section.bottom_margin) or 72.0,
        )

    def headers_and_footers(self) -> Tuple[List[Container], List[Container]]:
        headers: List[Container] = []
        footers: List[Container] = []
        for index, section in enumerate(getattr(self.native, "sections", [])):
            for attribute, role, sink in (
                ("header", "header", headers),
                ("footer", "footer", footers),
            ):
                part = getattr(section, attribute, None)
                if part is None or getattr(part, "is_linked_to_previous", False):
                    continue
                blocks = self.blocks(part)
                if blocks:
                    sink.append(Container(blocks, role=role, name=f"section-{index + 1}"))
        return headers, footers

    def footnotes(self) -> List[Footnote]:
        """Read footnote bodies from the footnotes part, when present."""
        if self._footnotes_loaded:
            return list(self._footnote_bodies_as_nodes())
        self._footnotes_loaded = True
        qn = self.qn
        part = None
        try:
            for rel in self.native.part.rels.values():
                if str(rel.reltype).endswith("/footnotes"):
                    part = rel.target_part
                    break
        except (AttributeError, KeyError):  # pragma: no cover
            part = None
        if part is None:
            return []

        try:
            root = part.element
        except AttributeError:  # pragma: no cover
            return []

        for node in root.findall(qn("w:footnote")):
            ident = node.get(qn("w:id"))
            if ident is None or int_or_none(ident) is None or int(ident) < 1:
                # Ids 0 and -1 are the separator/continuation placeholders.
                continue
            blocks: List[Block] = []
            for p in node.findall(qn("w:p")):
                para = self.Paragraph(p, part)
                content = self.inline_content(para)
                if "".join(n.text for n in content).strip():
                    blocks.append(Paragraph(content))
            if blocks:
                self._footnote_bodies[str(ident)] = blocks
        return list(self._footnote_bodies_as_nodes())

    def _footnote_bodies_as_nodes(self) -> Iterable[Footnote]:
        for ident, blocks in self._footnote_bodies.items():
            yield Footnote(identifier=ident, content=blocks)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


#: Colours Word applies via the Hyperlink character style rather than by choice.
_LINK_COLOURS = {"#0563c1", "#0000ff", "#0000ee", "#1155cc", "#0066cc"}


def _strip_link_decoration(content: List[Inline]) -> List[Inline]:
    """Drop underline and link-blue from runs inside a hyperlink.

    Word underlines and colours every hyperlink through its character style. Recording
    that as authorial formatting means a Markdown export renders
    ``[<u>text</u>](url)``, so the conventional decoration is discarded and left to
    whichever format renders the link next.
    """
    out: List[Inline] = []
    for node in content:
        if isinstance(node, Text):
            style = node.style
            changes: Dict[str, Any] = {}
            if style.underline:
                changes["underline"] = None
            if style.color and style.color.lower() in _LINK_COLOURS:
                changes["color"] = None
            if changes:
                from dataclasses import replace as _replace

                node = Text(node.text, _replace(style, **changes))
        out.append(node)
    return out


def _attach_captions(blocks: List[Block]) -> List[Block]:
    """Fold ``Caption``-styled paragraphs into the table or image they describe.

    Word stores a caption as a separate paragraph next to its figure. Keeping it as a
    stray paragraph means the relationship is lost on export, so it is moved onto
    :attr:`Table.caption` / :attr:`Image.caption`.
    """
    out: List[Block] = []
    for block in blocks:
        style_name = ""
        if isinstance(block, Paragraph):
            style_name = str(
                block.attrs.get("docx_style") or block.style.style_name or ""
            ).lower()

        if style_name == "caption" and out:
            text = block.text.strip()
            # Look back past nothing else: the caption sits directly next to its target.
            target = out[-1]
            if isinstance(target, (Table, Image)) and not target.caption and text:
                target.caption = text
                continue
            # Word also allows the caption above the figure.
            out.append(block)
            continue
        out.append(block)

    # Second pass for captions that precede their target.
    result: List[Block] = []
    index = 0
    while index < len(out):
        block = out[index]
        is_caption = (
            isinstance(block, Paragraph)
            and str(block.attrs.get("docx_style") or block.style.style_name or "").lower()
            == "caption"
        )
        if is_caption and index + 1 < len(out):
            nxt = out[index + 1]
            if isinstance(nxt, (Table, Image)) and not nxt.caption and block.text.strip():
                nxt.caption = block.text.strip()
                index += 1
                continue
        result.append(block)
        index += 1
    return result


def _assemble_lists(entries: Sequence[_ListEntry]) -> List[Block]:
    """Rebuild nested list blocks from a run of consecutive Word list paragraphs."""
    result: List[Block] = []
    stack: List[Tuple[int, ListBlock]] = []
    current_num: Optional[str] = None

    for entry in entries:
        # A different numbering definition starts a genuinely new list.
        if not stack or (entry.num_id != current_num and entry.level == 0):
            root = ListBlock(marker_style=entry.style)
            result.append(root)
            stack = [(entry.level, root)]
            current_num = entry.num_id
        else:
            while len(stack) > 1 and entry.level < stack[-1][0]:
                stack.pop()
            level, current = stack[-1]
            if entry.level > level:
                if current.items:
                    sub = ListBlock(marker_style=entry.style, level=len(stack))
                    parent_item = current.items[-1]
                    parent_item.content.append(sub)
                    parent_item.adopt(sub)
                    stack.append((entry.level, sub))
                else:
                    # Indented first item: treat it as this list's own level.
                    stack[-1] = (entry.level, current)

        target = stack[-1][1]
        if not target.items and target.marker_style is not entry.style:
            target.marker_style = entry.style
        item = ListItem(list(entry.blocks))
        target.items.append(item)
        target.adopt(item)

    for block in result:
        if isinstance(block, ListBlock):
            block.tight = all(len(item.content) <= 1 for item in block.items)
    return result


def _alignment(value: Any) -> Optional[Alignment]:
    if value is None:
        return None
    name = getattr(value, "name", None) or str(value)
    return Alignment.coerce(name)


def _points(length: Any) -> Optional[float]:
    """Convert a python-docx ``Length`` to points."""
    if length is None:
        return None
    try:
        return round(float(length.pt), 2)
    except (AttributeError, TypeError, ValueError):
        return None


def _line_spacing(value: Any) -> Optional[float]:
    """Line spacing is either a multiple (float) or an absolute Length."""
    if value is None:
        return None
    if isinstance(value, float):
        return round(value, 3)
    points = _points(value)
    return points


def _run_colour(font: Any) -> Optional[str]:
    try:
        rgb = font.color.rgb if font.color is not None else None
    except (AttributeError, ValueError):
        return None
    if rgb is None:
        return None
    return f"#{str(rgb).lower()}"


def _emu_to_points(value: int) -> float:
    """English Metric Units to points: 914400 EMU per inch, 72 points per inch."""
    return round(value / 12700.0, 2)


def _cell_valign(tc_pr: Any, qn: Any) -> Optional[VerticalAlign]:
    if tc_pr is None:
        return None
    node = tc_pr.find(qn("w:vAlign"))
    if node is None:
        return None
    return VerticalAlign.coerce(node.get(qn("w:val")))


def _cell_shading(tc_pr: Any, qn: Any) -> Optional[str]:
    if tc_pr is None:
        return None
    node = tc_pr.find(qn("w:shd"))
    if node is None:
        return None
    fill = node.get(qn("w:fill"))
    if not fill or fill in ("auto", "FFFFFF"):
        return None
    return f"#{fill.lower()}"
