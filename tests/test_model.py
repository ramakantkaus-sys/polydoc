"""Model construction, styling, geometry, traversal, and serialisation."""

from __future__ import annotations

import json

import pytest

from polydoc.model import (
    Alignment,
    BBox,
    Color,
    Document,
    Heading,
    Image,
    Link,
    ListBlock,
    ListItem,
    ListStyle,
    Metadata,
    Node,
    PageGeometry,
    Paragraph,
    ParagraphStyle,
    Size,
    Table,
    TableCell,
    Text,
    TextStyle,
    inline_text,
    merge_runs,
    plain,
)


class TestTextStyle:
    def test_defaults_are_all_unset(self):
        style = TextStyle()
        assert style.is_empty()
        assert style.bold is None  # None means "inherit", not False

    def test_merge_overlays_only_set_fields(self):
        base = TextStyle(bold=True, font_size=12)
        result = base.merge(TextStyle(italic=True))
        assert (result.bold, result.italic, result.font_size) == (True, True, 12)

    def test_merge_lets_the_overlay_win(self):
        result = TextStyle(bold=True).merge(TextStyle(bold=False))
        assert result.bold is False

    def test_merge_with_none_is_identity(self):
        style = TextStyle(bold=True)
        assert style.merge(None) is style

    def test_colours_are_normalised(self):
        assert TextStyle(color="RED").color == "#ff0000"
        assert TextStyle(color="#ABC").color == "#aabbcc"
        assert TextStyle(color="rgb(255, 128, 0)").color == "#ff8000"
        assert TextStyle(color="nonsense").color is None

    def test_font_size_is_rounded(self):
        assert TextStyle(font_size=11.999).font_size == 12.0

    @pytest.mark.parametrize(
        "family,expected",
        [
            ("Consolas", True),
            ("Courier New", True),
            ("DejaVu Sans Mono", True),
            ("Helvetica", False),
            (None, False),
        ],
    )
    def test_monospace_detection(self, family, expected):
        assert TextStyle(font_family=family).is_monospace is expected

    def test_code_flag_implies_monospace(self):
        assert TextStyle(code=True).is_monospace

    def test_repr_hides_unset_fields(self):
        assert repr(TextStyle(bold=True)) == "TextStyle(bold=True)"

    def test_to_dict_omits_unset(self):
        assert TextStyle(bold=True).to_dict() == {"bold": True}


class TestColor:
    def test_from_rgb_clamps(self):
        assert Color.from_rgb(300, -5, 128) == "#ff0080"

    def test_from_int(self):
        assert Color.from_int(0xFF8000) == "#ff8000"

    def test_rgb_property(self):
        assert Color("#ff8000").rgb == (255, 128, 0)

    def test_luminance_ordering(self):
        assert Color("#ffffff").luminance > Color("#000000").luminance

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            Color("not a colour")


class TestAlignment:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("center", Alignment.CENTER),
            ("CENTRE", Alignment.CENTER),
            ("both", Alignment.JUSTIFY),
            ("start", Alignment.LEFT),
            ("nonsense", None),
            (None, None),
        ],
    )
    def test_coerce(self, value, expected):
        assert Alignment.coerce(value) == expected


class TestBBox:
    def test_inverted_coordinates_are_normalised(self):
        box = BBox(10, 20, 0, 5)
        assert (box.x0, box.y0, box.x1, box.y1) == (0, 5, 10, 20)

    def test_measurements(self):
        box = BBox(0, 0, 10, 4)
        assert (box.width, box.height, box.area) == (10, 4, 40)
        assert (box.center.x, box.center.y) == (5, 2)

    def test_union_and_intersection(self):
        a, b = BBox(0, 0, 10, 10), BBox(5, 5, 15, 15)
        assert a.union(b) == BBox(0, 0, 15, 15)
        assert a.intersection(b) == BBox(5, 5, 10, 10)
        assert a.intersects(b)

    def test_disjoint_boxes(self):
        a, b = BBox(0, 0, 5, 5), BBox(10, 10, 15, 15)
        assert a.intersection(b) is None
        assert not a.intersects(b)

    def test_vertical_overlap_is_relative_to_shorter_box(self):
        # Same band: full overlap.
        assert BBox(0, 0, 10, 10).vertical_overlap(BBox(20, 0, 30, 10)) == 1.0
        # Separate bands: none.
        assert BBox(0, 0, 10, 10).vertical_overlap(BBox(0, 20, 10, 30)) == 0.0

    def test_contains_with_tolerance(self):
        outer, inner = BBox(0, 0, 10, 10), BBox(-0.5, 0, 10, 10)
        assert not outer.contains(inner)
        assert outer.contains(inner, tolerance=1.0)

    def test_bounding_of_many(self):
        boxes = [BBox(0, 0, 2, 2), BBox(8, 8, 10, 10)]
        assert BBox.bounding(boxes) == BBox(0, 0, 10, 10)
        assert BBox.bounding([]) is None

    def test_transforms(self):
        box = BBox(0, 0, 10, 10)
        assert box.expand(1) == BBox(-1, -1, 11, 11)
        assert box.translate(dx=5) == BBox(5, 0, 15, 10)
        assert box.scale(2) == BBox(0, 0, 20, 20)

    def test_serialisation(self):
        box = BBox(1, 2, 3, 4)
        assert BBox.from_dict(box.to_dict()) == box
        assert BBox.from_dict(None) is None


class TestPageGeometry:
    def test_content_box_respects_margins(self):
        geometry = PageGeometry(size=Size(612, 792), margin_left=72, margin_right=72,
                                margin_top=72, margin_bottom=72)
        assert geometry.content_box == BBox(72, 72, 540, 720)

    def test_round_trip(self):
        geometry = PageGeometry(size=Size(595.28, 841.89))
        assert PageGeometry.from_dict(geometry.to_dict()) == geometry

    def test_landscape_detection(self):
        assert Size(960, 540).is_landscape
        assert not Size(540, 960).is_landscape


class TestInline:
    def test_text_field_shadows_the_node_property(self):
        run = Text("hello")
        assert run.text == "hello"
        run.text = "changed"
        assert run.text == "changed"

    def test_split_at_preserves_style(self):
        left, right = Text("hello world", TextStyle(bold=True)).split_at(5)
        assert (left.text, right.text) == ("hello", " world")
        assert left.style.bold and right.style.bold

    def test_split_at_clamps_out_of_range(self):
        left, right = Text("abc").split_at(99)
        assert (left.text, right.text) == ("abc", "")

    def test_link_reports_its_own_text(self):
        link = Link(plain("click here"), href="https://example.com")
        assert link.text == "click here"

    def test_link_adopts_children(self):
        link = Link(plain("x"), href="#")
        assert link.content[0].parent is link

    def test_plain_builds_styled_runs(self):
        assert plain("hi", bold=True)[0].style.bold is True
        assert plain("") == []

    def test_inline_text_concatenates(self):
        assert inline_text([Text("a"), Text("b")]) == "ab"

    def test_merge_runs_collapses_matching_styles(self):
        merged = merge_runs([Text("a"), Text("b"), Text("c", TextStyle(bold=True))])
        assert [(r.text, r.style.bold) for r in merged] == [("ab", None), ("c", True)]

    def test_merge_runs_drops_empties(self):
        assert merge_runs([Text(""), Text("a"), Text("")]) == [Text("a")]


class TestBlocks:
    def test_heading_level_is_clamped(self):
        assert Heading.of("x", 0).level == 1
        assert Heading.of("x", 99).level == 6

    def test_paragraph_empty_detection(self):
        assert Paragraph.of("   ").is_empty
        assert not Paragraph.of("text").is_empty

    def test_table_from_rows_marks_header(self):
        table = Table.from_rows([["a", "b"], ["1", "2"]])
        assert table.header_rows == 1
        assert table.rows[0].is_header
        assert not table.rows[1].is_header

    def test_table_dimensions_account_for_spans(self):
        table = Table.from_rows([["a", "b", "c"], ["1", "2", "3"]])
        assert table.dimensions == (2, 3)
        table.rows[1].cells[0].colspan = 2
        assert table.rows[1].span_width == 4

    def test_table_normalise_reconciles_header_flags(self):
        from polydoc.model import TableRow

        rows = [TableRow.of(["a"], is_header=True), TableRow.of(["b"])]
        table = Table(rows)
        assert table.header_rows == 1

    def test_table_cell_lookup_and_matrix(self):
        table = Table.from_rows([["a", "b"], ["1", "2"]])
        assert table.cell(1, 0).text == "1"
        assert table.cell(99, 0) is None
        assert table.to_matrix() == [["a", "b"], ["1", "2"]]

    def test_table_cell_of_accepts_scalars(self):
        assert TableCell.of(42).text == "42"
        assert TableCell.of(None).text == ""

    def test_list_block_of_sets_marker(self):
        assert ListBlock.of(["a"], ordered=True).marker_style is ListStyle.ORDERED
        assert ListBlock.of(["a"]).marker_style is ListStyle.BULLET

    def test_list_style_ordered_property(self):
        assert ListStyle.ORDERED.is_ordered
        assert ListStyle.LOWER_ROMAN.is_ordered
        assert not ListStyle.BULLET.is_ordered

    def test_list_item_sublists(self):
        inner = ListBlock.of(["x"])
        item = ListItem([Paragraph.of("outer"), inner])
        assert item.sublists == [inner]

    def test_code_block_lines(self):
        from polydoc.model import CodeBlock

        assert CodeBlock("a\nb").lines == ["a", "b"]

    def test_image_text_falls_back_through_caption_then_alt(self):
        assert Image(alt="alt text").text == "alt text"
        assert Image(alt="alt", caption="cap").text == "cap"


class TestTraversal:
    def test_walk_is_depth_first(self, simple_document):
        types = [node.type for node in simple_document.walk()]
        assert types[0] == "document"
        assert "heading" in types and "text" in types

    def test_ancestors_reach_the_root(self, simple_document):
        run = [n for n in simple_document.walk() if n.type == "text"][0]
        assert [a.type for a in run.ancestors()] == ["heading", "document"]

    def test_reparent_fixes_links(self):
        paragraph = Paragraph([Text("a")])
        paragraph.content.append(Text("b"))  # appended without adoption
        assert paragraph.content[1].parent is None
        paragraph.reparent()
        assert paragraph.content[1].parent is paragraph

    def test_detach_removes_from_parent(self, simple_document):
        heading = simple_document.body[0]
        heading.detach()
        assert heading not in simple_document.body
        assert heading.parent is None

    def test_replace_with_preserves_position(self, simple_document):
        heading = simple_document.body[0]
        replacement = Paragraph.of("replaced")
        heading.replace_with(replacement)
        assert simple_document.body[0] is replacement
        assert replacement.parent is simple_document

    def test_replace_with_multiple(self, simple_document):
        simple_document.body[0].replace_with(Paragraph.of("a"), Paragraph.of("b"))
        assert [b.text for b in simple_document.body] == ["a", "b", "One paragraph."]

    def test_replace_without_parent_raises(self):
        with pytest.raises(ValueError):
            Paragraph.of("orphan").replace_with(Paragraph.of("x"))

    def test_find_by_id(self, simple_document):
        heading = simple_document.body[0]
        assert simple_document.find_by_id(heading.nid) is heading

    def test_nid_is_stable_and_lazy(self):
        node = Paragraph.of("x")
        assert node.nid == node.nid

    def test_equality_ignores_identity(self):
        # Two structurally identical nodes compare equal despite distinct nids.
        a, b = Paragraph.of("same"), Paragraph.of("same")
        assert a == b and a.nid != b.nid


class TestDocument:
    def test_text_joins_blocks(self, simple_document):
        assert simple_document.text == "Title\nOne paragraph."

    def test_append_adopts(self):
        document = Document()
        block = Paragraph.of("x")
        document.append(block)
        assert block.parent is document

    def test_insert_positions(self, simple_document):
        simple_document.insert(0, Paragraph.of("first"))
        assert simple_document.body[0].text == "first"

    def test_convenience_collections(self, sample_document):
        assert len(sample_document.headings) == 4
        assert len(sample_document.tables) == 1
        assert sample_document.word_count > 0

    def test_len_and_iteration(self, simple_document):
        assert len(simple_document) == 2
        assert [b.type for b in simple_document] == ["heading", "paragraph"]

    def test_summary_counts_block_types(self, sample_document):
        counts = sample_document.summary()["block_counts"]
        assert counts["heading"] == 4
        assert counts["table"] == 1

    def test_remove_and_clear(self, simple_document):
        simple_document.remove(simple_document.body[0])
        assert len(simple_document.body) == 1
        simple_document.clear()
        assert len(simple_document.body) == 0

    def test_outline_builds_a_tree(self, sample_document):
        outline = sample_document.outline()
        top = [s for s in outline if s.level == 1]
        assert top and top[0].title_text == "Quarterly Report"
        assert len(top[0].subsections) == 3

    def test_outline_does_not_reparent_the_document(self, sample_document):
        original = sample_document.body[1].parent
        sample_document.outline()
        assert sample_document.body[1].parent is original

    def test_apply_runs_transforms(self, simple_document):
        def rename(document):
            document.metadata.title = "Renamed"

        simple_document.apply(rename)
        assert simple_document.metadata.title == "Renamed"


class TestMetadata:
    def test_author_property_joins_and_splits(self):
        meta = Metadata(authors=["A", "B"])
        assert meta.author == "A, B"
        meta.author = "C, D"
        assert meta.authors == ["C", "D"]

    def test_author_setter_handles_empty(self):
        meta = Metadata(authors=["A"])
        meta.author = None
        assert meta.authors == []

    def test_round_trip_with_dates(self):
        from datetime import datetime

        meta = Metadata(title="T", created=datetime(2026, 1, 2, 3, 4, 5))
        restored = Metadata.from_dict(meta.to_dict())
        assert restored.title == "T"
        assert restored.created == datetime(2026, 1, 2, 3, 4, 5)

    def test_from_dict_ignores_unknown_keys(self):
        assert Metadata.from_dict({"title": "T", "bogus": 1}).title == "T"


class TestSerialisation:
    def test_full_round_trip_is_lossless(self, sample_document):
        payload = sample_document.to_dict()
        restored = Document.from_dict(payload)
        assert restored == sample_document

    def test_payload_is_json_safe(self, sample_document):
        text = json.dumps(sample_document.to_dict())
        assert Document.from_dict(json.loads(text)) == sample_document

    def test_unset_styles_are_omitted(self):
        payload = Paragraph.of("x").to_dict()
        assert "style" not in payload

    def test_include_ids_preserves_identity(self):
        heading = Heading.of("x")
        restored = Node.from_dict(heading.to_dict(include_ids=True))
        assert restored.nid == heading.nid

    def test_copy_is_deep_and_independent(self, sample_document):
        clone = sample_document.copy()
        assert clone == sample_document
        clone.body[0].content[0].text = "changed"
        assert sample_document.body[0].content[0].text != "changed"

    def test_bytes_survive_serialisation(self):
        image = Image(data=b"\x89PNG\r\n\x1a\n", mime_type="image/png")
        assert Node.from_dict(image.to_dict()).data == image.data

    def test_unknown_type_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown node type"):
            Node.from_dict({"type": "not_a_real_type"})

    def test_abstract_type_is_rejected(self):
        with pytest.raises(ValueError, match="abstract"):
            Node.from_dict({"type": "block"})

    def test_missing_type_is_rejected(self):
        with pytest.raises(ValueError, match="without a 'type'"):
            Node.from_dict({})

    def test_enum_fields_survive(self):
        block = ListBlock.of(["a"], ordered=True)
        assert Node.from_dict(block.to_dict()).marker_style is ListStyle.ORDERED

    def test_paragraph_style_survives(self):
        block = Paragraph.of("x", alignment=Alignment.CENTER)
        assert Node.from_dict(block.to_dict()).style.alignment is Alignment.CENTER

    def test_parents_are_rewired_on_load(self, sample_document):
        restored = Document.from_dict(sample_document.to_dict())
        assert all(
            node.parent is not None for node in restored.walk(include_self=False)
        )
