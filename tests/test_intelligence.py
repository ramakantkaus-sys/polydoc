"""The structure-inference layer: heuristics, layout analysis, and outlines."""

from __future__ import annotations

import pytest

from polydoc.intelligence import (
    FontProfile,
    build_nested_list,
    build_sections,
    coalesce_code_blocks,
    detect_columns,
    flatten_sections,
    group_lines,
    infer_heading_levels,
    is_all_caps,
    is_sentence_like,
    looks_like_code,
    looks_like_heading,
    normalise_whitespace,
    parse_list_marker,
    profile_fonts,
    renumber_headings,
    roman_to_int,
    sort_reading_order,
)
from polydoc.intelligence.layout import TextLine, TextSpan
from polydoc.model import BBox, Heading, ListStyle, Paragraph, Text, TextStyle


class TestListMarkers:
    @pytest.mark.parametrize(
        "line,style",
        [
            ("- item", ListStyle.BULLET),
            ("* item", ListStyle.BULLET),
            ("+ item", ListStyle.BULLET),
            ("\u2022 item", ListStyle.BULLET),
            ("1. item", ListStyle.ORDERED),
            ("12) item", ListStyle.ORDERED),
            ("a. item", ListStyle.LOWER_ALPHA),
            ("B) item", ListStyle.UPPER_ALPHA),
            ("iv. item", ListStyle.LOWER_ROMAN),
            ("IX. item", ListStyle.UPPER_ROMAN),
        ],
    )
    def test_styles_are_distinguished(self, line, style):
        assert parse_list_marker(line).style is style

    def test_content_excludes_the_marker(self):
        assert parse_list_marker("- buy milk").content == "buy milk"

    def test_indent_is_measured(self):
        assert parse_list_marker("    - deep").indent == 4

    def test_numbers_are_parsed(self):
        assert parse_list_marker("7. item").number == 7
        assert parse_list_marker("iv. item").number == 4

    def test_checkboxes(self):
        assert parse_list_marker("- [x] done").checked is True
        assert parse_list_marker("- [ ] open").checked is False
        assert parse_list_marker("- plain").checked is None

    @pytest.mark.parametrize(
        "line",
        [
            "not a list",
            "",
            "   ",
            "-no space after dash",
            "1.no space",
        ],
    )
    def test_non_lists(self, line):
        assert parse_list_marker(line) is None

    def test_dashes_are_not_bullets(self):
        # An em dash opens an attribution far more often than a list item, so
        # treating it as a bullet causes real damage.
        assert parse_list_marker("\u2014 Dijkstra") is None
        assert parse_list_marker("\u2013 Knuth") is None

    def test_roman_beats_alpha_for_ambiguous_letters(self):
        # "i." and "v." are valid in both schemes; outline numbering means roman.
        assert parse_list_marker("i. first").style is ListStyle.LOWER_ROMAN
        assert parse_list_marker("v. fifth").style is ListStyle.LOWER_ROMAN

    def test_non_roman_letters_are_alpha(self):
        assert parse_list_marker("b. second").style is ListStyle.LOWER_ALPHA


class TestRomanNumerals:
    @pytest.mark.parametrize(
        "text,value",
        [("i", 1), ("iv", 4), ("ix", 9), ("xiv", 14), ("xl", 40), ("MCMXCIX", 1999)],
    )
    def test_conversion(self, text, value):
        assert roman_to_int(text) == value

    @pytest.mark.parametrize("text", ["", "abc", "hello", "12"])
    def test_invalid(self, text):
        assert roman_to_int(text) is None


class TestHeadingHeuristics:
    @pytest.mark.parametrize(
        "text",
        [
            "Executive Summary",
            "EXECUTIVE SUMMARY",
            "2.1 Scope Of Work",
            "Conclusions And Recommendations",
            "Introduction",
        ],
    )
    def test_positive(self, text):
        assert looks_like_heading(text)

    def test_a_single_numbered_line_is_treated_as_a_list_item(self):
        """Documents the deliberate ambiguity in "3. Findings".

        In plain text a line like this is indistinguishable from a one-item ordered
        list, and mis-promoting list items to headings scrambles documents far more
        often than the reverse. Formats that carry typography (PDF, DOCX) resolve it
        properly using font size, so the ambiguity only bites unstructured text.
        """
        assert not looks_like_heading("3. Findings")
        assert parse_list_marker("3. Findings") is not None

    def test_dotted_numbering_is_a_heading(self):
        # "2.1" is not a valid list marker, so there is no ambiguity here.
        assert looks_like_heading("2.1 Scope Of Work")

    @pytest.mark.parametrize(
        "text",
        [
            "This paragraph explains the results in considerable detail.",
            "The quick brown fox jumped over the lazy dog and kept running onwards.",
            "- a list item",
            "",
            "A sentence that ends with a full stop.",
        ],
    )
    def test_negative(self, text):
        assert not looks_like_heading(text)

    def test_requires_a_following_blank_line(self):
        assert not looks_like_heading("Executive Summary", followed_by_blank=False)

    def test_word_limit(self):
        assert not looks_like_heading(" ".join(["Word"] * 30))

    def test_all_caps_needs_enough_letters(self):
        assert is_all_caps("SUMMARY")
        assert not is_all_caps("OK")
        assert not is_all_caps("Mixed Case")

    def test_sentence_detection(self):
        assert is_sentence_like("Ends with a stop.")
        assert not is_sentence_like("No terminal punctuation")


class TestCodeHeuristics:
    def test_detects_python(self):
        assert looks_like_code("def main():\n    return 1\nclass A:\n    pass")

    def test_detects_uniformly_indented_blocks(self):
        assert looks_like_code("    line one\n    line two")

    def test_rejects_prose(self):
        assert not looks_like_code("This is ordinary prose.\nAnd a second line.")

    def test_rejects_empty(self):
        assert not looks_like_code("")


class TestWhitespace:
    def test_normalises_line_endings_and_spaces(self):
        # Runs of spaces collapse to one; leading indentation is kept because it can
        # be meaningful, and trailing whitespace is stripped.
        assert normalise_whitespace("a\r\n  b   c  ") == "a\n b c"

    def test_removes_nbsp_and_zero_width(self):
        assert normalise_whitespace("a\u00a0b\u200bc") == "a bc"

    def test_collapse_can_be_disabled(self):
        assert "   " in normalise_whitespace("a   b", collapse=False)


class TestNestedListBuilding:
    def test_flat_list(self):
        markers = [parse_list_marker(line) for line in ["- a", "- b", "- c"]]
        block = build_nested_list(markers)
        assert len(block.items) == 3

    def test_nesting_from_indentation(self):
        lines = ["- top", "  - nested", "- second"]
        block = build_nested_list([parse_list_marker(line) for line in lines])
        assert len(block.items) == 2
        assert len(block.items[0].sublists[0].items) == 1

    def test_three_levels(self):
        lines = ["- a", "  - b", "    - c"]
        block = build_nested_list([parse_list_marker(line) for line in lines])
        inner = block.items[0].sublists[0]
        assert inner.items[0].sublists[0].items[0].text == "c"

    def test_marker_style_from_first_item(self):
        markers = [parse_list_marker(line) for line in ["1. a", "2. b"]]
        assert build_nested_list(markers).marker_style is ListStyle.ORDERED

    def test_start_number_is_kept(self):
        assert build_nested_list([parse_list_marker("3. c")]).start == 3

    def test_empty_input(self):
        assert build_nested_list([]) is None


class TestSections:
    def test_builds_a_hierarchy(self):
        blocks = [
            Heading.of("A", 1),
            Paragraph.of("under a"),
            Heading.of("A.1", 2),
            Paragraph.of("under a1"),
            Heading.of("B", 1),
        ]
        tree = build_sections(blocks)
        assert [s.title_text for s in tree] == ["A", "B"]
        assert [s.title_text for s in tree[0].subsections] == ["A.1"]

    def test_preamble_becomes_a_level_zero_section(self):
        tree = build_sections([Paragraph.of("intro"), Heading.of("A", 1)])
        assert tree[0].level == 0
        assert tree[0].text == "intro"

    def test_shares_blocks_without_reparenting(self):
        paragraph = Paragraph.of("body")
        blocks = [Heading.of("A", 1), paragraph]
        build_sections(blocks)
        # The view must not steal the block from its real parent.
        assert paragraph.parent is None

    def test_copy_blocks_produces_an_independent_tree(self):
        paragraph = Paragraph.of("body")
        tree = build_sections([Heading.of("A", 1), paragraph], copy_blocks=True)
        assert tree[0].content[0] is not paragraph

    def test_max_level_limits_nesting(self):
        blocks = [Heading.of("A", 1), Heading.of("B", 3)]
        tree = build_sections(blocks, max_level=2)
        assert tree[0].subsections == []

    def test_flatten_is_the_inverse(self):
        blocks = [Heading.of("A", 1), Paragraph.of("x"), Heading.of("A.1", 2)]
        flat = flatten_sections(build_sections(blocks))
        assert [b.type for b in flat] == ["heading", "paragraph", "heading"]

    def test_renumber_compresses_gaps(self):
        headings = [Heading.of("a", 1), Heading.of("b", 3), Heading.of("c", 4)]
        renumber_headings(headings)
        assert [h.level for h in headings] == [1, 2, 3]

    def test_renumber_with_no_headings(self):
        renumber_headings([Paragraph.of("x")])  # must not raise


class TestCodeCoalescing:
    def _mono(self, text):
        return Paragraph([Text(text, TextStyle(font_family="Consolas"))])

    def test_consecutive_monospaced_paragraphs_merge(self):
        blocks = [self._mono("def f():"), self._mono("    return 1")]
        out = coalesce_code_blocks(blocks)
        assert len(out) == 1
        assert out[0].type == "code_block"
        assert out[0].code == "def f():\n    return 1"

    def test_a_single_monospaced_paragraph_is_left_alone(self):
        out = coalesce_code_blocks([self._mono("just `code` inline")])
        assert out[0].type == "paragraph"

    def test_explicit_style_promotes_a_single_paragraph(self):
        block = self._mono("x = 1")
        block.attrs["docx_style"] = "Code"
        assert coalesce_code_blocks([block])[0].type == "code_block"

    def test_prose_is_untouched(self):
        blocks = [Paragraph.of("prose one"), Paragraph.of("prose two")]
        assert all(b.type == "paragraph" for b in coalesce_code_blocks(blocks))

    def test_runs_are_separated_by_prose(self):
        blocks = [
            self._mono("a = 1"),
            self._mono("b = 2"),
            Paragraph.of("Explanation."),
            self._mono("c = 3"),
            self._mono("d = 4"),
        ]
        out = coalesce_code_blocks(blocks)
        assert [b.type for b in out] == ["code_block", "paragraph", "code_block"]

    def test_recurses_into_containers(self):
        from polydoc.model import Quote

        quote = Quote([self._mono("x = 1"), self._mono("y = 2")])
        coalesce_code_blocks([quote])
        assert quote.content[0].type == "code_block"


# ---------------------------------------------------------------------------
# Layout analysis
# ---------------------------------------------------------------------------


def span(text, x0, y0, x1, y1, size=10.0, **kwargs):
    return TextSpan(text=text, bbox=BBox(x0, y0, x1, y1), font_size=size, **kwargs)


def line(*spans):
    """A TextLine with its bbox computed, as ``group_lines`` would produce."""
    built = TextLine(list(spans))
    built.recompute_bbox()
    return built


class TestLineGrouping:
    def test_spans_on_one_line_are_grouped(self):
        spans = [span("Hello ", 0, 0, 30, 12), span("world", 30, 0, 60, 12)]
        lines = group_lines(spans)
        assert len(lines) == 1
        assert lines[0].text == "Hello world"

    def test_separate_lines_stay_separate(self):
        spans = [span("first", 0, 0, 30, 12), span("second", 0, 20, 30, 32)]
        assert len(group_lines(spans)) == 2

    def test_spans_are_ordered_left_to_right(self):
        spans = [span("world", 30, 0, 60, 12), span("Hello ", 0, 0, 30, 12)]
        assert group_lines(spans)[0].text == "Hello world"

    def test_a_line_does_not_swallow_the_next(self):
        """Regression guard: the accumulated bbox must not grow greedily downwards.

        A short bullet glyph on the following line otherwise falls inside the previous
        line's expanded band, merging two list items into one.
        """
        spans = [
            span("\u2022", 10, 0, 16, 12),
            span("first item", 20, 0, 90, 12),
            span("\u2022", 10, 20, 16, 32),
            span("second item", 20, 20, 95, 32),
        ]
        lines = group_lines(spans)
        assert len(lines) == 2
        assert lines[0].text == "\u2022first item"

    def test_empty_input(self):
        assert group_lines([]) == []


class TestTextLine:
    def test_font_size_is_weighted_by_character_count(self):
        line = TextLine([
            span("a long stretch of body text", 0, 0, 100, 10, size=10),
            span("x", 100, 0, 104, 14, size=20),
        ])
        assert line.font_size == 10

    def test_bold_requires_a_majority(self):
        line = TextLine([
            span("mostly bold text here", 0, 0, 80, 10, bold=True),
            span("x", 80, 0, 84, 10),
        ])
        assert line.is_bold

    def test_not_bold_when_minority(self):
        line = TextLine([
            span("plain text is dominant", 0, 0, 80, 10),
            span("b", 80, 0, 84, 10, bold=True),
        ])
        assert not line.is_bold

    def test_recompute_bbox(self):
        line = TextLine([span("a", 0, 0, 10, 10), span("b", 10, 0, 20, 10)])
        assert line.recompute_bbox() == BBox(0, 0, 20, 10)

    def test_span_to_style(self):
        style = span("x", 0, 0, 5, 10, bold=True, italic=True, color="#ff0000").to_style()
        assert style.bold and style.italic and style.color == "#ff0000"


class TestFontProfiling:
    def _body_lines(self, count=8, size=10.0):
        return [
            TextLine([span("a reasonably long line of body copy", 0, i * 15, 200, i * 15 + 12, size=size)])
            for i in range(count)
        ]

    def test_body_size_is_the_most_common(self):
        profile = profile_fonts(self._body_lines())
        assert profile.body_size == 10.0

    def test_larger_sizes_are_ranked_as_headings(self):
        lines = self._body_lines()
        lines.append(TextLine([span("Big Title", 0, 500, 200, 530, size=24)]))
        lines.append(TextLine([span("Medium", 0, 540, 200, 560, size=16)]))
        profile = profile_fonts(lines)
        assert profile.level_for(24) == 1
        assert profile.level_for(16) == 2
        assert profile.level_for(10) is None

    def test_near_identical_sizes_collapse(self):
        lines = self._body_lines()
        lines.append(TextLine([span("Title", 0, 500, 200, 530, size=18.0)]))
        lines.append(TextLine([span("Also title", 0, 540, 200, 570, size=17.96)]))
        profile = profile_fonts(lines)
        assert profile.level_for(18.0) == profile.level_for(17.96) == 1

    def test_relative_ranking_not_absolute_sizes(self):
        # In a document set in 14pt, 14pt is body text, not a heading.
        lines = self._body_lines(size=14.0)
        lines.append(TextLine([span("Heading", 0, 500, 200, 530, size=20)]))
        profile = profile_fonts(lines)
        assert profile.body_size == 14.0
        assert profile.level_for(14.0) is None
        assert profile.level_for(20) == 1

    def test_empty_input(self):
        assert profile_fonts([]).body_size == 0.0
        assert not profile_fonts([]).has_headings


class TestHeadingInference:
    def _lines(self):
        lines = [
            TextLine([span("Document Title", 0, 0, 200, 30, size=24, bold=True)]),
            TextLine([span("Section One", 0, 40, 200, 58, size=16, bold=True)]),
        ]
        for i in range(6):
            lines.append(
                TextLine([span(
                    "This is a body paragraph line with plenty of words in it.",
                    0, 70 + i * 14, 300, 82 + i * 14, size=10,
                )])
            )
        return lines

    def test_levels_are_assigned_by_size(self):
        lines = self._lines()
        levels = infer_heading_levels(lines)
        assert levels[0] == 1
        assert levels[1] == 2

    def test_body_text_is_not_a_heading(self):
        lines = self._lines()
        levels = infer_heading_levels(lines)
        assert 2 not in levels

    def test_bold_at_body_size_can_be_promoted(self):
        lines = self._lines()
        lines.append(TextLine([span("Short Bold Label", 0, 200, 120, 212, size=10, bold=True)]))
        levels = infer_heading_levels(lines)
        assert len(lines) - 1 in levels

    def test_long_lines_are_never_headings(self):
        lines = self._lines()
        lines.append(
            TextLine([span(" ".join(["word"] * 40), 0, 300, 400, 330, size=24)])
        )
        assert len(lines) - 1 not in infer_heading_levels(lines)

    def test_no_body_size_yields_nothing(self):
        assert infer_heading_levels([]) == {}


class TestColumns:
    def test_single_column_is_the_default(self):
        lines = [
            line(span("text across the page", 50, i * 15, 500, i * 15 + 12))
            for i in range(10)
        ]
        assert len(detect_columns(lines)) == 1

    def test_two_columns_are_detected(self):
        lines = []
        for i in range(8):
            lines.append(line(span("left column text", 50, i * 15, 250, i * 15 + 12)))
            lines.append(line(span("right column text", 320, i * 15, 520, i * 15 + 12)))
        assert len(detect_columns(lines)) == 2

    def test_too_few_lines_stays_single(self):
        lines = [line(span("a", 50, 0, 100, 12)), line(span("b", 400, 0, 450, 12))]
        assert len(detect_columns(lines)) == 1

    def test_mismatched_column_heights_stay_single(self):
        """A full-width heading plus one tall column must not read as two columns."""
        lines = [line(span("left body text here", 50, i * 15, 250, i * 15 + 12))
                 for i in range(10)]
        for i in range(3):
            lines.append(line(span("short", 320, i * 15, 520, i * 15 + 12)))
        assert len(detect_columns(lines)) == 1

    def test_reading_order_follows_columns(self):
        lines = []
        for i in range(6):
            lines.append(line(span(f"L{i}", 50, i * 15, 250, i * 15 + 12)))
        for i in range(6):
            lines.append(line(span(f"R{i}", 320, i * 15, 520, i * 15 + 12)))
        ordered = sort_reading_order(lines)
        texts = [item.text for item in ordered]
        # The whole left column precedes the right one.
        assert texts.index("L5") < texts.index("R0")

    def test_reading_order_can_ignore_columns(self):
        lines = [
            line(span("R", 320, 0, 520, 12)),
            line(span("L", 50, 0, 250, 12)),
        ]
        ordered = sort_reading_order(lines, detect_multi_column=False)
        # Same vertical band, so ordering falls to the x coordinate.
        assert [item.text for item in ordered] == ["L", "R"]

    def test_empty_input(self):
        assert sort_reading_order([]) == []
