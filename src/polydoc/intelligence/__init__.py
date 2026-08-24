"""Structure inference.

Formats differ enormously in how much structure they record. A DOCX says "this
paragraph uses Heading 1"; a PDF says "these glyphs are 18pt Helvetica-Bold at these
coordinates". This package closes that gap, so downstream code can rely on the model
being meaningful regardless of where it came from.

* :mod:`~polydoc.intelligence.heuristics` -- line-level judgements (list markers,
  heading-ish lines, code).
* :mod:`~polydoc.intelligence.structure` -- outlines and nested lists.
* :mod:`~polydoc.intelligence.layout` -- coordinate work: reading order, columns,
  and heading inference from font statistics.
"""

from __future__ import annotations

from .heuristics import (
    ListMarker,
    is_all_caps,
    is_sentence_like,
    looks_like_code,
    looks_like_heading,
    normalise_whitespace,
    parse_list_marker,
    roman_to_int,
)
from .layout import (
    FontProfile,
    TextLine,
    TextSpan,
    detect_columns,
    group_lines,
    infer_heading_levels,
    profile_fonts,
    sort_reading_order,
)
from .structure import (
    build_nested_list,
    build_sections,
    coalesce_code_blocks,
    flatten_sections,
    renumber_headings,
)

__all__ = [
    "FontProfile",
    "ListMarker",
    "TextLine",
    "TextSpan",
    "build_nested_list",
    "build_sections",
    "coalesce_code_blocks",
    "detect_columns",
    "flatten_sections",
    "group_lines",
    "infer_heading_levels",
    "is_all_caps",
    "is_sentence_like",
    "looks_like_code",
    "looks_like_heading",
    "normalise_whitespace",
    "parse_list_marker",
    "profile_fonts",
    "renumber_headings",
    "roman_to_int",
    "sort_reading_order",
]
