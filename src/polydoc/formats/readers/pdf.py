"""PDF reader: PyMuPDF for text and fonts, pdfplumber for table ruling lines.

A PDF records positioned glyphs and nothing else -- no paragraphs, no headings, no
reading order. Everything structural here is reconstructed, which is why this reader
leans on :mod:`polydoc.intelligence.layout`:

1. Spans are collected with their font, size, weight, and colour.
2. Font sizes are profiled across the *whole document* so heading levels are ranked
   relative to measured body copy. Doing this per page would make an 11pt heading on a
   sparse page outrank a 20pt title elsewhere.
3. Columns are detected from whitespace gutters, and lines sorted into reading order.
4. Lines are merged into paragraphs using vertical gaps, indentation changes, font
   changes, and short-line endings -- with hyphenated words rejoined.
5. Tables are located with pdfplumber; text inside a table's box is removed from the
   prose flow so it is not duplicated.

Every page becomes a :class:`~polydoc.model.Page` block, so pagination survives and
``document.pages`` works. Flowing writers unwrap them automatically.
"""

from __future__ import annotations

import importlib
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ...exceptions import ParseError
from ...intelligence.heuristics import ListMarker, parse_list_marker
from ...intelligence.layout import (
    FontProfile,
    TextLine,
    TextSpan,
    detect_columns,
    group_lines,
    infer_heading_levels,
    profile_fonts,
)
from ...intelligence.structure import build_nested_list, coalesce_code_blocks
from ...model import (
    BBox,
    Block,
    Color,
    Document,
    Heading,
    Image,
    Inline,
    Page,
    PageGeometry,
    Paragraph,
    Size,
    Table,
    TableRow,
    Text,
    TextStyle,
    merge_runs,
)
from ..base import Reader
from ..registry import register_reader
from ..source import Source

__all__ = ["PDFReader"]

#: PyMuPDF span flag bits.
_FLAG_SUPERSCRIPT = 1
_FLAG_ITALIC = 2
_FLAG_MONOSPACE = 8
_FLAG_BOLD = 16

#: Sentence-final punctuation, for deciding whether a short line ends a paragraph.
_SENTENCE_END = ".!?:;\u2026\"')]}\u201d\u2019"


@register_reader
class PDFReader(Reader):
    """Reads PDF, reconstructing paragraphs, headings, lists, and tables."""

    format = "pdf"
    extensions = (".pdf",)
    mime_types = ("application/pdf",)
    extra = "pdf"
    description = "PDF, with layout-based structure reconstruction"

    def read(self, source: Source, **options: Any) -> Document:
        self.enforce_limits(source, **options)
        fitz = _import_pymupdf()

        password: Optional[str] = options.get("password")
        detect_tables: bool = options.get("tables", True)
        extract_images: bool = options.get("images", True)
        page_range = options.get("pages")
        detect_headings: bool = options.get("headings", True)
        multi_column: bool = options.get("multi_column", True)
        flatten: bool = options.get("flatten", False)

        try:
            native = fitz.open(stream=source.bytes, filetype="pdf")
        except Exception as exc:
            raise ParseError(f"Could not open PDF: {exc}") from exc

        try:
            if native.needs_pass:
                if not password or not native.authenticate(password):
                    raise ParseError(
                        "This PDF is password protected. Pass password='...' to read it."
                    )

            indices = self._page_indices(native.page_count, page_range)

            # Pass 1: collect lines for every page so fonts can be profiled globally.
            page_lines: Dict[int, List[TextLine]] = {}
            table_boxes: Dict[int, List[Tuple[BBox, Table]]] = {}

            warnings: List[str] = []
            plumber = _open_plumber(source, detect_tables, warnings)
            page_heights: Dict[int, float] = {}
            try:
                for index in indices:
                    page = native.load_page(index)
                    spans = self._page_spans(page)
                    page_lines[index] = group_lines(spans)
                    page_heights[index] = float(page.rect.height)
                    if plumber is not None:
                        table_boxes[index] = self._page_tables(plumber, index, warnings)
            finally:
                if plumber is not None:
                    plumber.close()

            for lines in page_lines.values():
                # Order matters: canonicalise bullets while the gap is still visible,
                # then synthesise the missing word spaces.
                _normalise_bullet_glyphs(lines)
                _insert_gap_spaces(lines)

            if options.get("strip_running_heads", True):
                _strip_running_heads(page_lines, page_heights)

            # Font profiling deliberately ignores text inside tables. Table cells are
            # usually set smaller than body copy, so including them drags the measured
            # "body size" down and every real paragraph then ranks as a heading.
            profile_lines = [
                line
                for index in indices
                for line in page_lines.get(index, [])
                if not _inside_any(line, table_boxes.get(index, []))
            ]
            if not profile_lines:
                profile_lines = [
                    line for index in indices for line in page_lines.get(index, [])
                ]
            profile = profile_fonts(profile_lines) if detect_headings else FontProfile()

            document = Document()
            for index in indices:
                page = native.load_page(index)
                blocks = self._page_blocks(
                    page_lines.get(index, []),
                    table_boxes.get(index, []),
                    profile,
                    detect_headings=detect_headings,
                    multi_column=multi_column,
                )
                if options.get("detect_code_blocks", True):
                    _restore_code_indent(blocks)
                    blocks = coalesce_code_blocks(blocks)
                if extract_images:
                    blocks.extend(self._page_images(native, page))

                geometry = self._page_geometry(page)
                if flatten:
                    document.body.extend(blocks)
                else:
                    document.append(
                        Page(content=blocks, number=index + 1, geometry=geometry)
                    )

            document.adopt(*document.body)
            if warnings:
                document.attrs["warnings"] = warnings
            self._read_metadata(native, document)
            document.geometry = self._page_geometry(native.load_page(indices[0])) if indices else None

            if document.metadata.title is None:
                document.metadata.title = self._infer_title(document)

            return self.finalise(document, source)
        finally:
            native.close()

    # -- page selection -------------------------------------------------------
    @staticmethod
    def _page_indices(count: int, page_range: Any) -> List[int]:
        """Resolve a ``pages`` option into zero-based indices.

        Accepts ``None`` (all), an int, a ``(start, end)`` tuple with 1-based inclusive
        bounds, or any iterable of 1-based page numbers.
        """
        if page_range is None:
            return list(range(count))
        if isinstance(page_range, int):
            return [page_range - 1] if 1 <= page_range <= count else []
        if isinstance(page_range, tuple) and len(page_range) == 2:
            start, end = page_range
            start = max(1, int(start))
            end = min(count, int(end))
            return [i - 1 for i in range(start, end + 1)]
        if isinstance(page_range, range):
            return [i - 1 for i in page_range if 1 <= i <= count]
        if isinstance(page_range, Iterable):
            return [int(i) - 1 for i in page_range if 1 <= int(i) <= count]
        return list(range(count))

    # -- extraction -----------------------------------------------------------
    def _page_spans(self, page: Any) -> List[TextSpan]:
        """Pull styled spans out of a page in PyMuPDF's dict form."""
        spans: List[TextSpan] = []
        try:
            data = page.get_text("dict")
        except Exception:  # pragma: no cover - malformed page
            return spans

        for block in data.get("blocks", []):
            if block.get("type") != 0:  # 0 is text; 1 is an image
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if not text:
                        continue
                    bbox = span.get("bbox")
                    if not bbox:
                        continue
                    flags = int(span.get("flags", 0))
                    font = str(span.get("font", ""))
                    lowered = font.lower()
                    spans.append(
                        TextSpan(
                            text=text,
                            bbox=BBox(*bbox),
                            font_name=font,
                            font_size=float(span.get("size", 0.0)),
                            bold=bool(flags & _FLAG_BOLD)
                            or "bold" in lowered
                            or "black" in lowered
                            or "heavy" in lowered,
                            italic=bool(flags & _FLAG_ITALIC)
                            or "italic" in lowered
                            or "oblique" in lowered,
                            color=Color.from_int(int(span.get("color", 0))),
                        )
                    )
        return spans

    def _page_tables(
        self,
        plumber: Any,
        index: int,
        warnings: Optional[List[str]] = None,
    ) -> List[Tuple[BBox, Table]]:
        """Find tables with pdfplumber, returning each with its bounding box.

        Table detection is best-effort: a failure here must not lose the page's text.
        But swallowing the reason silently makes a missing table impossible to diagnose,
        so any failure is recorded in ``document.attrs["warnings"]``.
        """
        out: List[Tuple[BBox, Table]] = []
        try:
            page = plumber.pages[index]
        except (IndexError, AttributeError) as exc:
            if warnings is not None:
                warnings.append(f"page {index + 1}: could not open for table detection ({exc})")
            return out

        try:
            found = page.find_tables()
        except Exception as exc:  # pragma: no cover - pdfplumber edge cases
            if warnings is not None:
                warnings.append(
                    f"page {index + 1}: table detection failed "
                    f"({type(exc).__name__}: {exc})"
                )
            return out

        for table in found:
            try:
                matrix = table.extract()
            except Exception:  # pragma: no cover
                continue
            cleaned = [
                [(cell or "").strip().replace("\n", " ") for cell in row] for row in matrix
            ]
            # A single row, or a single column, is far more likely to be a false
            # positive from stray ruling lines than a real table.
            if len(cleaned) < 2 or max(len(r) for r in cleaned) < 2:
                continue
            if not any(any(cell for cell in row) for row in cleaned):
                continue

            model = Table.from_rows(cleaned, header=self._table_has_header(cleaned))
            box = BBox(*table.bbox)
            model.bbox = box
            out.append((box, model))
        return out

    @staticmethod
    def _table_has_header(matrix: Sequence[Sequence[str]]) -> bool:
        """A textual first row above rows containing numbers implies a header."""
        if len(matrix) < 2:
            return False

        def numeric(value: str) -> bool:
            stripped = value.strip().replace(",", "").replace("%", "").lstrip("$\u00a3\u20ac")
            if not stripped:
                return False
            try:
                float(stripped)
                return True
            except ValueError:
                return False

        first = [c for c in matrix[0] if c.strip()]
        rest = [c for row in matrix[1:] for c in row if c.strip()]
        if not first or not rest:
            return False
        return not any(numeric(c) for c in first) and any(numeric(c) for c in rest)

    def _page_images(self, native: Any, page: Any) -> List[Image]:
        """Extract embedded raster images with their placement on the page."""
        out: List[Image] = []
        try:
            infos = page.get_image_info(xrefs=True)
        except Exception:  # pragma: no cover
            return out

        seen: set = set()
        for info in infos:
            xref = info.get("xref")
            if not xref or xref in seen:
                continue
            seen.add(xref)
            bbox = info.get("bbox")
            box = BBox(*bbox) if bbox else None
            # Skip hairline artefacts and full-page background scans.
            if box is not None and (box.width < 8 or box.height < 8):
                continue
            data = None
            mime = None
            try:
                extracted = native.extract_image(xref)
                data = extracted.get("image")
                extension = extracted.get("ext", "png")
                mime = f"image/{'jpeg' if extension == 'jpg' else extension}"
            except Exception:  # pragma: no cover
                data = None
            out.append(
                Image(
                    src=f"image-{xref}",
                    data=data,
                    mime_type=mime,
                    width=box.width if box else None,
                    height=box.height if box else None,
                    bbox=box,
                )
            )
        return out

    @staticmethod
    def _page_geometry(page: Any) -> Optional[PageGeometry]:
        try:
            rect = page.rect
        except AttributeError:  # pragma: no cover
            return None
        return PageGeometry(
            size=Size(round(rect.width, 2), round(rect.height, 2)),
            rotation=int(getattr(page, "rotation", 0) or 0),
        )

    # -- structure reconstruction ---------------------------------------------
    def _page_blocks(
        self,
        lines: Sequence[TextLine],
        tables: Sequence[Tuple[BBox, Table]],
        profile: FontProfile,
        detect_headings: bool = True,
        multi_column: bool = True,
    ) -> List[Block]:
        """Turn a page's lines into blocks, splicing tables back in by position."""
        usable = [line for line in lines if line.stripped and line.bbox is not None]
        if not usable and not tables:
            return []

        # Text inside a table's box is already captured by the table.
        table_regions = [box.expand(2.0) for box, _ in tables]
        if table_regions:
            usable = [
                line
                for line in usable
                if not any(region.contains(line.bbox, tolerance=1.0) for region in table_regions)
            ]

        columns = detect_columns(usable) if multi_column else [list(usable)]

        blocks: List[Block] = []
        for column in columns:
            blocks.extend(self._column_blocks(column, profile, detect_headings))

        # Re-insert tables at their vertical position among the text blocks.
        for box, table in tables:
            insert_at = len(blocks)
            for index, block in enumerate(blocks):
                if block.bbox is not None and block.bbox.y0 > box.y0:
                    insert_at = index
                    break
            blocks.insert(insert_at, table)
        return blocks

    def _column_blocks(
        self,
        lines: Sequence[TextLine],
        profile: FontProfile,
        detect_headings: bool,
    ) -> List[Block]:
        ordered = sorted(lines, key=lambda line: (round(line.bbox.y0, 1), line.bbox.x0))
        if not ordered:
            return []

        heading_map = (
            infer_heading_levels(ordered, profile) if detect_headings and profile.body_size else {}
        )

        gap = self._median_gap(ordered)
        right_edge = max(line.bbox.x1 for line in ordered)
        left_edge = min(line.bbox.x0 for line in ordered)
        width = max(1.0, right_edge - left_edge)

        blocks: List[Block] = []
        paragraph: List[TextLine] = []
        markers: List[Tuple[ListMarker, TextLine]] = []

        def flush_paragraph() -> None:
            if paragraph:
                blocks.append(self._paragraph(paragraph))
                paragraph.clear()

        def flush_list() -> None:
            if markers:
                built = build_nested_list([m for m, _ in markers])
                if built is not None:
                    box = BBox.bounding(line.bbox for _, line in markers)
                    built.bbox = box
                    blocks.append(built)
                markers.clear()

        for index, line in enumerate(ordered):
            level = heading_map.get(index)
            marker = parse_list_marker(line.text)

            # "1. Overview" and "2.1 Detail" are numbered *headings* far more often
            # than list items. Font size is the decisive signal: when the line is set
            # larger than body copy, the heading reading wins over the list marker.
            size_level = profile.level_for(line.font_size) if profile.body_size else None
            if level is not None and (marker is None or size_level is not None):
                flush_paragraph()
                flush_list()
                heading = Heading(_deemphasise(self._inlines([line])), level)
                heading.bbox = line.bbox
                blocks.append(heading)
                continue

            if marker is not None:
                flush_paragraph()
                # Convert the visual x-offset into the indent units the list builder
                # expects, so nesting from a PDF matches nesting from plain text.
                indent = int(round((line.bbox.x0 - left_edge) / 12.0)) * 2
                markers.append((marker._replace(indent=indent), line))
                continue

            if markers:
                # A continuation line of the previous list item, not new prose.
                previous = markers[-1][1]
                if self._is_continuation(previous, line, gap, left_edge):
                    merged = markers[-1][0]
                    markers[-1] = (
                        merged._replace(content=self._join(merged.content, line.text)),
                        line,
                    )
                    continue
                flush_list()

            if paragraph and self._should_break(paragraph[-1], line, gap, right_edge, width):
                flush_paragraph()
            paragraph.append(line)

        flush_paragraph()
        flush_list()
        return blocks

    @staticmethod
    def _median_gap(lines: Sequence[TextLine]) -> float:
        """Typical vertical gap between consecutive lines, used as the break yardstick."""
        gaps: List[float] = []
        for previous, current in zip(lines, lines[1:]):
            delta = current.bbox.y0 - previous.bbox.y1
            if -2.0 < delta < 40.0:
                gaps.append(delta)
        if not gaps:
            return 2.0
        value = median(gaps)
        return value if value > 0 else 2.0

    def _should_break(
        self,
        previous: TextLine,
        current: TextLine,
        gap: float,
        right_edge: float,
        width: float,
    ) -> bool:
        """Decide whether ``current`` starts a new paragraph after ``previous``."""
        delta = current.bbox.y0 - previous.bbox.y1

        # A clearly larger gap than usual is the strongest signal.
        if delta > max(gap * 1.8, gap + 3.0):
            return True
        # A change of font size means a different kind of text.
        if abs(current.font_size - previous.font_size) > 0.7:
            return True
        # A first-line indent starts a paragraph.
        if current.bbox.x0 - previous.bbox.x0 > 9.0:
            return True
        # The previous line stopped well short of the margin and ended a sentence.
        short = previous.bbox.x1 < right_edge - 0.14 * width
        if short and previous.stripped.endswith(tuple(_SENTENCE_END)):
            return True
        # A bold/italic switch across the whole line also marks a new block.
        if previous.is_bold != current.is_bold:
            return True
        return False

    @staticmethod
    def _is_continuation(
        previous: TextLine,
        current: TextLine,
        gap: float,
        left_edge: float,
    ) -> bool:
        """True when a line wraps from the preceding list item.

        A wrapped continuation sits close below and is indented past the marker.
        """
        delta = current.bbox.y0 - previous.bbox.y1
        if delta > max(gap * 1.8, gap + 3.0):
            return False
        return current.bbox.x0 > previous.bbox.x0 + 2.0

    @staticmethod
    def _join(left: str, right: str) -> str:
        """Join wrapped lines, repairing hyphenation."""
        left = left.rstrip()
        right = right.lstrip()
        if not left:
            return right
        if not right:
            return left
        if left.endswith("-") and not left.endswith(("--", " -")):
            # A trailing hyphen before a lowercase continuation is word-splitting.
            if right[:1].islower():
                return left[:-1] + right
        return f"{left} {right}"

    def _paragraph(self, lines: Sequence[TextLine]) -> Paragraph:
        block = Paragraph(self._inlines(lines))
        block.bbox = BBox.bounding(line.bbox for line in lines if line.bbox)
        return block

    def _inlines(self, lines: Sequence[TextLine]) -> List[Inline]:
        """Build styled runs from lines, inserting joins between them."""
        out: List[Inline] = []
        for index, line in enumerate(lines):
            if index:
                previous = lines[index - 1].stripped
                if previous.endswith("-") and not previous.endswith("--"):
                    # Drop the soft hyphen from the last emitted run.
                    for node in reversed(out):
                        if isinstance(node, Text) and node.text.rstrip().endswith("-"):
                            trimmed = node.text.rstrip()
                            node.text = trimmed[:-1]
                            break
                else:
                    out.append(Text(" "))

            for span in line.spans:
                text = span.text
                if not text:
                    continue
                out.append(Text(text, self._span_style(span)))

        merged = merge_runs(out)
        # Trim the edges of the assembled paragraph.
        if merged and isinstance(merged[0], Text):
            merged[0] = Text(merged[0].text.lstrip(), merged[0].style)
        if merged and isinstance(merged[-1], Text):
            merged[-1] = Text(merged[-1].text.rstrip(), merged[-1].style)
        return [node for node in merged if not (isinstance(node, Text) and not node.text)]

    @staticmethod
    def _span_style(span: TextSpan) -> TextStyle:
        """Convert a span's typography into a style, dropping document-wide defaults.

        Recording the body font and size on every run would bloat the model and make
        every export carry the source PDF's typography; only distinguishing attributes
        are kept.
        """
        monospace = any(
            token in span.font_name.lower()
            for token in ("mono", "courier", "consolas", "menlo")
        )
        colour = span.color if span.color and span.color != "#000000" else None
        return TextStyle(
            bold=True if span.bold else None,
            italic=True if span.italic else None,
            code=True if monospace else None,
            color=colour,
        )

    # -- metadata -------------------------------------------------------------
    @staticmethod
    def _read_metadata(native: Any, document: Document) -> None:
        info = native.metadata or {}
        meta = document.metadata
        title = (info.get("title") or "").strip()
        # Producers write a placeholder when the author set no title; treating it as
        # real would block the far better guess from the document's own first heading.
        meta.title = None if title.lower() in _PLACEHOLDER_TITLES else (title or None)
        author = (info.get("author") or "").strip()
        if author:
            meta.authors = [p.strip() for p in author.split(";") if p.strip()] or [author]
        meta.subject = (info.get("subject") or "").strip() or None
        keywords = (info.get("keywords") or "").strip()
        if keywords:
            import re

            meta.keywords = [p.strip() for p in re.split(r"[;,]", keywords) if p.strip()]
        meta.producer = (info.get("producer") or "").strip() or None
        meta.created = _parse_pdf_date(info.get("creationDate"))
        meta.modified = _parse_pdf_date(info.get("modDate"))
        creator = (info.get("creator") or "").strip()
        if creator:
            meta.custom["creator"] = creator

        try:
            toc = native.get_toc()
        except Exception:  # pragma: no cover
            toc = []
        if toc:
            document.attrs["outline"] = [
                {"level": entry[0], "title": entry[1], "page": entry[2]} for entry in toc
            ]

    @staticmethod
    def _infer_title(document: Document) -> Optional[str]:
        """Use the first heading, or the largest text on page one, as the title."""
        for block in document.blocks():
            if isinstance(block, Heading):
                text = block.text.strip()
                if text:
                    return text
        for block in document.blocks():
            if isinstance(block, Paragraph):
                text = block.text.strip()
                if text:
                    return text[:120]
        return None


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------


#: Single characters used as positioned bullet glyphs by common PDF producers.
#: Restricted to characters that are never standalone English words, so "A quick..."
#: and "I think..." cannot be misread as list items. Word uses "o" at level two and
#: Wingdings private-use code points at level three.
_POSITIONED_BULLETS = frozenset(
    "o\u00a7\u00b7\u2022\u2023\u2043\u25aa\u25ab\u25cb\u25cf\u25e6"
    "\uf020\uf0a7\uf0b7\uf0d8\uf06c"
)


def _deemphasise(content: List[Inline]) -> List[Inline]:
    """Drop bold/italic that is uniform across a heading's runs.

    Being set bold is often *why* a line was identified as a heading. Recording it as
    inline emphasis too makes every export doubly emphatic (``# **Title**``), so uniform
    weight is treated as part of the heading's identity rather than as content. Mixed
    emphasis inside a heading is genuine and left alone.
    """
    from dataclasses import replace as _replace

    runs = [node for node in content if isinstance(node, Text) and node.text.strip()]
    if not runs:
        return content

    all_bold = all(run.style.bold for run in runs)
    all_italic = all(run.style.italic for run in runs)
    if not (all_bold or all_italic):
        return content

    changes = {}
    if all_bold:
        changes["bold"] = None
    if all_italic:
        changes["italic"] = None

    out: List[Inline] = []
    for node in content:
        if isinstance(node, Text):
            node = Text(node.text, _replace(node.style, **changes))
        out.append(node)
    return out


def _normalise_bullet_glyphs(lines: Sequence[TextLine], ratio: float = 0.25) -> None:
    """Rewrite positioned bullet glyphs to a canonical "\u2022", in place.

    A PDF draws a bullet as its own text-showing operation at a fixed offset, so it
    arrives as a separate single-character span with a gap before the item text. That
    shape is a strong signal, which is what makes it safe to accept glyphs like a bare
    "o" that would be ambiguous in plain text.
    """
    for line in lines:
        spans = line.spans
        if len(spans) < 2:
            continue
        first = spans[0]
        glyph = first.text.strip()
        if len(glyph) != 1 or glyph not in _POSITIONED_BULLETS:
            continue
        gap = spans[1].bbox.x0 - first.bbox.x1
        if gap < max(0.8, ratio * max(first.font_size, 1.0)):
            continue
        first.text = "\u2022 "


def _insert_gap_spaces(lines: Sequence[TextLine], ratio: float = 0.22) -> None:
    """Insert spaces where a PDF only left a visual gap, in place.

    PDF has no concept of a word separator: a generator can place "o" and "headings"
    as adjacent spans with a gap and no space character. Extraction then yields
    "oheadings", which defeats both list-marker detection and plain word splitting.

    A gap wider than ``ratio`` of the font size is treated as a space. The threshold is
    relative to font size because the same absolute gap means different things at 8pt
    and 24pt.
    """
    for line in lines:
        spans = line.spans
        for index in range(len(spans) - 1):
            current, following = spans[index], spans[index + 1]
            if current.text.endswith((" ", "\t")) or following.text.startswith((" ", "\t")):
                continue
            if not current.text or not following.text:
                continue
            gap = following.bbox.x0 - current.bbox.x1
            threshold = max(0.8, ratio * max(current.font_size, following.font_size, 1.0))
            if gap > threshold:
                current.text = current.text + " "


def _restore_code_indent(blocks: Sequence[Block]) -> None:
    """Rebuild leading indentation on monospaced lines, in place.

    Code indentation in a PDF is a horizontal offset, not space characters, so a naive
    read flattens every listing to the left margin. Because the text is monospaced, the
    character width can be measured from the line itself
    (``width / len(text)``), which self-calibrates to the font and size actually used.
    """
    candidates = [
        block
        for block in blocks
        if isinstance(block, Paragraph)
        and block.bbox is not None
        and block.content
        and all(
            isinstance(node, Text) and node.style.is_monospace for node in block.content
        )
    ]
    if len(candidates) < 2:
        return

    left = min(block.bbox.x0 for block in candidates if block.bbox)
    for block in candidates:
        box = block.bbox
        text = block.text
        if box is None or not text.strip():
            continue
        char_width = box.width / max(1, len(text))
        if char_width <= 0:
            continue
        indent = int(round((box.x0 - left) / char_width))
        if indent <= 0:
            continue
        first = block.content[0]
        if isinstance(first, Text):
            first.text = " " * indent + first.text


def _strip_running_heads(
    page_lines: Dict[int, List[TextLine]],
    page_heights: Dict[int, float],
    margin_ratio: float = 0.09,
    min_share: float = 0.5,
) -> None:
    """Remove repeating headers, footers, and page numbers, in place.

    Running heads are page furniture, not content: left in, they interrupt the prose on
    every page boundary and pollute a converted document. They are identified by two
    properties -- they sit in the top or bottom margin band, and the same text (ignoring
    the digits that change) appears on at least ``min_share`` of pages.

    A lone number in the bottom band is dropped regardless of repetition, since a page
    number is never content.
    """
    indices = [i for i in page_lines if page_lines[i]]
    if not indices:
        return
    if len(indices) < 2:
        # Repetition is unavailable with one page, so fall back to geometry.
        _strip_single_page_footer(page_lines, page_heights, indices[0], margin_ratio)
        return

    def normalise(text: str) -> str:
        # Collapse digits so "Page 3 of 12" matches across pages.
        return "".join("#" if ch.isdigit() else ch for ch in " ".join(text.split())).lower()

    #: normalised text -> set of pages it appears in a margin band on
    occurrences: Dict[str, set] = {}
    banded: Dict[int, List[Tuple[TextLine, str]]] = {}

    for index in indices:
        height = page_heights.get(index) or 0.0
        if height <= 0:
            continue
        top_limit = height * margin_ratio
        bottom_limit = height * (1.0 - margin_ratio)
        entries: List[Tuple[TextLine, str]] = []
        for line in page_lines[index]:
            if line.bbox is None or not line.stripped:
                continue
            in_band = line.bbox.y1 <= top_limit or line.bbox.y0 >= bottom_limit
            if not in_band:
                continue
            key = normalise(line.stripped)
            entries.append((line, key))
            occurrences.setdefault(key, set()).add(index)
        banded[index] = entries

    threshold = max(2, int(len(indices) * min_share))
    for index, entries in banded.items():
        drop = set()
        for line, key in entries:
            stripped = line.stripped
            bare_number = stripped.replace("#", "").strip()
            is_page_number = (
                len(occurrences.get(key, ())) >= 2
                and bare_number.isdigit()
                or (stripped.isdigit() and len(stripped) <= 4)
            )
            if is_page_number or len(occurrences.get(key, ())) >= threshold:
                drop.add(id(line))
        if drop:
            page_lines[index] = [
                line for line in page_lines[index] if id(line) not in drop
            ]


def _strip_single_page_footer(
    page_lines: Dict[int, List[TextLine]],
    page_heights: Dict[int, float],
    index: int,
    margin_ratio: float,
) -> None:
    """Drop a footer from a one-page document, where repetition cannot be used.

    Requires every signal to agree, because a false positive here deletes real content:
    the line must be the last on the page, sit in the bottom margin band, be separated
    from the body by an unusually large gap, and end in a bare integer -- a page number.
    """
    lines = [line for line in page_lines[index] if line.bbox is not None and line.stripped]
    if len(lines) < 3:
        return
    height = page_heights.get(index) or 0.0
    if height <= 0:
        return

    ordered = sorted(lines, key=lambda line: line.bbox.y0)
    last, previous = ordered[-1], ordered[-2]

    if last.bbox.y0 < height * (1.0 - margin_ratio):
        return

    gaps = [
        b.bbox.y0 - a.bbox.y1
        for a, b in zip(ordered, ordered[1:])
        if -2.0 < (b.bbox.y0 - a.bbox.y1) < 40.0
    ]
    typical = median(gaps) if gaps else 2.0
    separation = last.bbox.y0 - previous.bbox.y1
    if separation < max(typical * 2.5, typical + 8.0):
        return

    tail = last.stripped.split()[-1] if last.stripped.split() else ""
    if not tail.isdigit():
        return

    page_lines[index] = [line for line in page_lines[index] if line is not last]


#: Titles that tools write when the author set none; treated as absent.
_PLACEHOLDER_TITLES = {
    "untitled",
    "unnamed",
    "no title",
    "document",
    "document1",
    "microsoft word - document1",
    "(anonymous)",
}


def _import_pymupdf() -> Any:
    """Import PyMuPDF under either of its module names."""
    from ...exceptions import MissingDependencyError

    for name in ("pymupdf", "fitz"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    raise MissingDependencyError("pymupdf", "Reading PDF", "pdf")


def _inside_any(line: TextLine, regions: Sequence[Tuple[BBox, Table]]) -> bool:
    """True when a line sits inside one of the given table boxes."""
    if line.bbox is None or not regions:
        return False
    return any(box.expand(2.0).contains(line.bbox, tolerance=1.0) for box, _ in regions)


def _open_plumber(
    source: Source,
    enabled: bool,
    warnings: Optional[List[str]] = None,
) -> Any:
    """Open the source with pdfplumber for table detection, if available.

    Table detection is a nice-to-have: a missing or failing pdfplumber must not stop us
    reading the text. The reason is recorded so a silently table-free result can be
    explained.
    """
    if not enabled:
        return None
    try:
        import pdfplumber
    except ImportError:
        if warnings is not None:
            warnings.append("tables not detected: pdfplumber is not installed")
        return None
    try:
        return pdfplumber.open(source.stream())
    except Exception as exc:  # pragma: no cover - corrupt file
        if warnings is not None:
            warnings.append(
                f"tables not detected: pdfplumber could not open the file "
                f"({type(exc).__name__}: {exc})"
            )
        return None


def _parse_pdf_date(value: Optional[str]) -> Any:
    """Parse a PDF date string (``D:YYYYMMDDHHmmSS+ZZ'zz'``)."""
    if not value:
        return None
    import datetime as _dt

    text = str(value).strip()
    if text.startswith("D:"):
        text = text[2:]
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 8:
        return None
    try:
        year, month, day = int(digits[0:4]), int(digits[4:6]), int(digits[6:8])
        hour = int(digits[8:10]) if len(digits) >= 10 else 0
        minute = int(digits[10:12]) if len(digits) >= 12 else 0
        second = int(digits[12:14]) if len(digits) >= 14 else 0
        return _dt.datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None
