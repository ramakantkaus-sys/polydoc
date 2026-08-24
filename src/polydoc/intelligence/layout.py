"""Coordinate-level inference for page-based formats.

A PDF has no headings, no paragraphs, and no reading order -- only positioned glyphs.
This module reconstructs that structure:

* :func:`group_lines` clusters spans into lines by vertical overlap.
* :func:`detect_columns` finds whitespace gutters, so a two-column paper is not read
  straight across.
* :func:`sort_reading_order` puts lines into the order a human would read them.
* :func:`profile_fonts` measures which font size dominates the body text, and
  :func:`infer_heading_levels` ranks the larger sizes into heading levels 1-6.

The font-statistics approach matters because absolute sizes mean nothing on their own:
14pt is a heading in a document set in 10pt and body text in one set in 14pt. Ranking
*relative* to the measured body size is what makes this work across documents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..model.geometry import BBox
from ..model.style import TextStyle
from .heuristics import is_all_caps, is_sentence_like

__all__ = [
    "FontProfile",
    "TextLine",
    "TextSpan",
    "detect_columns",
    "group_lines",
    "infer_heading_levels",
    "profile_fonts",
    "sort_reading_order",
]


@dataclass
class TextSpan:
    """A run of characters sharing one font, as extracted from a page."""

    text: str
    bbox: BBox
    font_name: str = ""
    font_size: float = 0.0
    bold: bool = False
    italic: bool = False
    color: Optional[str] = None

    def to_style(self) -> TextStyle:
        """The equivalent universal :class:`~polydoc.model.TextStyle`."""
        return TextStyle(
            bold=True if self.bold else None,
            italic=True if self.italic else None,
            font_family=self.font_name or None,
            font_size=self.font_size or None,
            color=self.color,
        )


@dataclass
class TextLine:
    """One visual line of text, assembled from spans."""

    spans: List[TextSpan] = field(default_factory=list)
    bbox: Optional[BBox] = None

    @property
    def text(self) -> str:
        return "".join(span.text for span in self.spans)

    @property
    def stripped(self) -> str:
        return self.text.strip()

    @property
    def font_size(self) -> float:
        """The dominant font size, weighted by character count.

        Weighting matters: a footnote marker should not redefine the line's size.
        """
        if not self.spans:
            return 0.0
        weights: Dict[float, int] = {}
        for span in self.spans:
            size = round(span.font_size, 1)
            weights[size] = weights.get(size, 0) + max(1, len(span.text.strip()))
        return max(weights.items(), key=lambda kv: kv[1])[0]

    @property
    def font_name(self) -> str:
        return self.spans[0].font_name if self.spans else ""

    @property
    def is_bold(self) -> bool:
        """True when most of the line's characters are bold."""
        total = sum(len(s.text.strip()) for s in self.spans)
        if not total:
            return False
        bold = sum(len(s.text.strip()) for s in self.spans if s.bold)
        return bold / total > 0.6

    @property
    def is_italic(self) -> bool:
        total = sum(len(s.text.strip()) for s in self.spans)
        if not total:
            return False
        italic = sum(len(s.text.strip()) for s in self.spans if s.italic)
        return italic / total > 0.6

    def recompute_bbox(self) -> Optional[BBox]:
        self.bbox = BBox.bounding(span.bbox for span in self.spans)
        return self.bbox


# ---------------------------------------------------------------------------
# Line assembly
# ---------------------------------------------------------------------------


def group_lines(
    spans: Sequence[TextSpan],
    overlap_threshold: float = 0.45,
) -> List[TextLine]:
    """Cluster spans into visual lines.

    Two spans belong to the same line when their bounding boxes overlap vertically by
    more than ``overlap_threshold`` of the shorter box's height. This is more robust
    than comparing baselines, which subscripts and inline formula glyphs break.
    """
    if not spans:
        return []

    ordered = sorted(spans, key=lambda s: (round(s.bbox.y0, 1), s.bbox.x0))
    lines: List[TextLine] = []

    current = TextLine([ordered[0]], ordered[0].bbox)
    # Compared against the tallest span seen on the line, not the union of all of
    # them. A union grows vertically with every span, so a line becomes progressively
    # greedier and eventually swallows the line below it -- which merges consecutive
    # list items into one. A real glyph box cannot drift, so this stays bounded.
    anchor = ordered[0].bbox

    for span in ordered[1:]:
        same_line = anchor.vertical_overlap(span.bbox) >= overlap_threshold
        if same_line:
            # Guard against a short glyph (a bullet, a subscript) on the next line
            # sitting inside a tall anchor's band.
            centre_gap = abs(anchor.center.y - span.bbox.center.y)
            if centre_gap > 0.6 * max(anchor.height, span.bbox.height):
                same_line = False

        if same_line:
            current.spans.append(span)
            assert current.bbox is not None
            current.bbox = current.bbox.union(span.bbox)
            if span.bbox.height > anchor.height:
                anchor = span.bbox
        else:
            current.spans.sort(key=lambda s: s.bbox.x0)
            lines.append(current)
            current = TextLine([span], span.bbox)
            anchor = span.bbox

    current.spans.sort(key=lambda s: s.bbox.x0)
    lines.append(current)
    return lines


# ---------------------------------------------------------------------------
# Columns and reading order
# ---------------------------------------------------------------------------


def detect_columns(
    lines: Sequence[TextLine],
    min_gutter_ratio: float = 0.035,
    min_lines_per_column: int = 3,
    bins: int = 240,
) -> List[List[TextLine]]:
    """Split lines into columns by finding vertical whitespace gutters.

    Returns a list of column groups, left to right. A single-element list means the
    page is single-column, which is the common case and the safe default: the checks
    below are deliberately strict, because wrongly splitting a page scrambles the text
    far more badly than wrongly keeping it together.
    """
    usable = [line for line in lines if line.bbox is not None and line.stripped]
    if len(usable) < min_lines_per_column * 2:
        return [list(lines)]

    page = BBox.bounding(line.bbox for line in usable if line.bbox)
    if page is None or page.width <= 0:
        return [list(lines)]

    # Mark which horizontal bins any line covers.
    occupied = [False] * bins
    scale = bins / page.width
    for line in usable:
        assert line.bbox is not None
        start = max(0, int((line.bbox.x0 - page.x0) * scale))
        end = min(bins - 1, int((line.bbox.x1 - page.x0) * scale))
        for index in range(start, end + 1):
            occupied[index] = True

    # Interior runs of empty bins are candidate gutters.
    min_gutter_bins = max(2, int(min_gutter_ratio * bins))
    gutters: List[Tuple[int, int]] = []
    run_start: Optional[int] = None
    for index in range(bins):
        if not occupied[index]:
            if run_start is None:
                run_start = index
        else:
            if run_start is not None and index - run_start >= min_gutter_bins:
                gutters.append((run_start, index))
            run_start = None
    # A trailing run touches the right margin, so it is not a gutter.

    # Ignore gutters hugging either margin.
    edge = max(1, int(0.08 * bins))
    gutters = [g for g in gutters if g[0] > edge and g[1] < bins - edge]
    if not gutters:
        return [list(lines)]

    boundaries = [page.x0 + ((start + end) / 2) / scale for start, end in gutters]
    groups: List[List[TextLine]] = [[] for _ in range(len(boundaries) + 1)]
    for line in lines:
        if line.bbox is None:
            groups[0].append(line)
            continue
        centre = line.bbox.center.x
        index = 0
        for boundary in boundaries:
            if centre > boundary:
                index += 1
            else:
                break
        groups[index].append(line)

    populated = [g for g in groups if len(g) >= min_lines_per_column]
    if len(populated) < 2:
        return [list(lines)]

    # A real multi-column layout has columns of comparable height. Full-width
    # headings spanning the gutter would otherwise fool the split.
    heights = []
    for group in populated:
        box = BBox.bounding(line.bbox for line in group if line.bbox)
        heights.append(box.height if box else 0.0)
    if min(heights) < 0.4 * max(heights):
        return [list(lines)]

    # Lines that straddle a boundary (spanning headings) belong to the flow, not a
    # column; keep them with the first column so they are read first.
    return populated


def sort_reading_order(lines: Sequence[TextLine], detect_multi_column: bool = True) -> List[TextLine]:
    """Order lines the way a reader would consume them.

    Single-column pages sort top-to-bottom; multi-column pages are read column by
    column.
    """
    if not lines:
        return []
    if not detect_multi_column:
        return sorted(lines, key=_line_sort_key)

    columns = detect_columns(lines)
    if len(columns) <= 1:
        return sorted(lines, key=_line_sort_key)

    ordered: List[TextLine] = []
    for column in columns:
        ordered.extend(sorted(column, key=_line_sort_key))
    return ordered


def _line_sort_key(line: TextLine) -> Tuple[float, float]:
    if line.bbox is None:
        return (0.0, 0.0)
    # Round y so slight baseline jitter does not reorder a line's fragments.
    return (round(line.bbox.y0, 1), line.bbox.x0)


# ---------------------------------------------------------------------------
# Font statistics and heading inference
# ---------------------------------------------------------------------------


@dataclass
class FontProfile:
    """Measured typography of a document, used to rank headings."""

    #: font size -> number of non-space characters set at that size
    size_weights: Dict[float, int] = field(default_factory=dict)
    #: The size carrying the most text; treated as body copy.
    body_size: float = 0.0
    #: Sizes larger than the body, descending -- heading candidates.
    heading_sizes: List[float] = field(default_factory=list)
    #: Size -> heading level (1 is largest).
    level_map: Dict[float, int] = field(default_factory=dict)
    #: Most common font family in body text.
    body_font: str = ""

    @property
    def has_headings(self) -> bool:
        return bool(self.heading_sizes)

    def level_for(self, size: float) -> Optional[int]:
        """Heading level for a font size, or ``None`` if it is body text."""
        return self.level_map.get(round(size, 1))


def profile_fonts(
    lines: Sequence[TextLine],
    heading_ratio: float = 1.08,
    max_levels: int = 6,
) -> FontProfile:
    """Measure font usage and rank the larger sizes into heading levels.

    ``heading_ratio`` is how much bigger than body copy a size must be to count as a
    heading. 8% is enough to catch a 10pt/11pt distinction while ignoring the sub-point
    jitter that PDF extraction produces.

    >>> lines = [TextLine([TextSpan("body text here", BBox(0, 0, 50, 10), font_size=10)])] * 5
    >>> lines.append(TextLine([TextSpan("Big Title", BBox(0, 20, 50, 40), font_size=20)]))
    >>> profile_fonts(lines).level_for(20)
    1
    """
    weights: Dict[float, int] = {}
    fonts: Dict[str, int] = {}

    for line in lines:
        for span in line.spans:
            characters = len(span.text.strip())
            if not characters:
                continue
            size = round(span.font_size, 1)
            if size <= 0:
                continue
            weights[size] = weights.get(size, 0) + characters
            if span.font_name:
                fonts[span.font_name] = fonts.get(span.font_name, 0) + characters

    if not weights:
        return FontProfile()

    body_size = max(weights.items(), key=lambda kv: kv[1])[0]
    candidates = sorted((s for s in weights if s >= body_size * heading_ratio), reverse=True)

    # Collapse near-identical sizes (18.0 and 17.96 are the same heading level).
    collapsed: List[float] = []
    for size in candidates:
        if not collapsed or abs(collapsed[-1] - size) > max(0.4, size * 0.02):
            collapsed.append(size)

    level_map = {size: index + 1 for index, size in enumerate(collapsed[:max_levels])}
    # Sizes beyond six distinct levels clamp to the deepest level rather than vanish.
    for size in collapsed[max_levels:]:
        level_map[size] = max_levels

    body_font = max(fonts.items(), key=lambda kv: kv[1])[0] if fonts else ""

    return FontProfile(
        size_weights=weights,
        body_size=body_size,
        heading_sizes=collapsed,
        level_map=level_map,
        body_font=body_font,
    )


def infer_heading_levels(
    lines: Sequence[TextLine],
    profile: Optional[FontProfile] = None,
    max_heading_words: int = 20,
) -> Dict[int, int]:
    """Decide which lines are headings, and at what level.

    Returns ``{line_index: heading_level}``. Three signals are combined:

    1. **Font size** relative to measured body copy -- the strongest signal.
    2. **Bold or all-caps** at body size, which is how many documents mark
       lower-level headings.
    3. **Shape** -- headings are short and do not read as sentences. This is applied
       as a veto, so a large-font pull quote is not mistaken for a heading.
    """
    if profile is None:
        profile = profile_fonts(lines)
    if not profile.body_size:
        return {}

    result: Dict[int, int] = {}
    # The level used for bold-at-body-size headings sits below the size-based ones.
    implicit_level = min(6, len(profile.heading_sizes) + 1)

    for index, line in enumerate(lines):
        text = line.stripped
        if not text:
            continue
        words = text.split()
        if len(words) > max_heading_words:
            continue

        size = line.font_size
        level = profile.level_for(size)

        if level is None:
            # Not larger than body copy: allow bold or all-caps to promote it, but
            # only for genuinely short, non-sentence lines.
            if len(words) > 12 or is_sentence_like(text):
                continue
            if line.is_bold or is_all_caps(text):
                level = implicit_level
            else:
                continue
        elif is_sentence_like(text) and len(words) > 12:
            # Large but clearly prose (a pull quote or lead paragraph).
            continue

        result[index] = level

    return result
