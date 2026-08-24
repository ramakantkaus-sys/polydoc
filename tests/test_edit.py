"""Editing: formatting-preserving text changes and structural operations."""

from __future__ import annotations

import pytest

from polydoc.edit import (
    Pipeline,
    find_text,
    insert_after,
    insert_before,
    map_blocks,
    merge_adjacent_paragraphs,
    move,
    remove,
    remove_all,
    replace_block,
    replace_text,
    restyle,
    select,
    set_text,
    shift_heading_levels,
    strip_empty,
    style_text,
    unwrap,
    wrap,
)
from polydoc.exceptions import EditError
from polydoc.model import (
    Alignment,
    Container,
    Document,
    Heading,
    Link,
    ListBlock,
    ListItem,
    Paragraph,
    Table,
    Text,
    TextStyle,
    plain,
)


class TestCrossRunReplace:
    """The behaviour that motivates the library.

    A phrase split across differently-formatted runs must still be found and replaced,
    with the surrounding formatting intact.
    """

    def test_match_spanning_three_runs(self, template_document):
        paragraph = template_document.body[1]
        assert paragraph.text == "Period: FY2024 Q3 for {{client}}."

        count = replace_text(template_document, "FY2024 Q3", "FY2025 Q1")

        assert count == 1
        assert paragraph.text == "Period: FY2025 Q1 for {{client}}."

    def test_replacement_inherits_the_first_run_style(self, template_document):
        replace_text(template_document, "FY2024 Q3", "FY2025 Q1")
        paragraph = template_document.body[1]
        replaced = [r for r in paragraph.content if "FY2025" in r.text]
        assert replaced and replaced[0].style.bold is True

    def test_surrounding_runs_keep_their_formatting(self, template_document):
        replace_text(template_document, "FY2024", "FY2025")
        paragraph = template_document.body[1]
        assert paragraph.text == "Period: FY2025 Q3 for {{client}}."
        # The unstyled prefix is still unstyled.
        assert paragraph.content[0].style.is_empty()

    def test_match_inside_a_single_run(self, template_document):
        assert replace_text(template_document, "Period", "Term") == 1
        assert template_document.body[1].text.startswith("Term:")

    def test_link_survives_a_replacement_in_its_text(self, template_document):
        count = replace_text(template_document, "FY2024 terms", "FY2025 terms")
        paragraph = template_document.body[2]
        links = [node for node in paragraph.content if isinstance(node, Link)]

        assert count == 1
        assert len(links) == 1
        assert links[0].href == "https://example.com/fy2024"
        assert links[0].text == "the FY2025 terms"
        assert links[0].content[0].style.underline is True

    def test_replacement_across_the_whole_document(self, template_document):
        assert replace_text(template_document, "{{client}}", "Acme Ltd") == 2
        assert "Acme Ltd" in template_document.body[0].text
        assert "Acme Ltd" in template_document.body[1].text

    def test_runs_are_merged_after_editing(self, template_document):
        before = len(template_document.body[1].content)
        replace_text(template_document, "FY2024 Q3", "X")
        after = len(template_document.body[1].content)
        assert after < before

    def test_no_match_returns_zero(self, template_document):
        assert replace_text(template_document, "absent", "x") == 0


class TestReplaceOptions:
    def test_count_limits_replacements(self, template_document):
        assert replace_text(template_document, "{{client}}", "X", count=1) == 1
        assert len(find_text(template_document, "{{client}}")) == 1

    def test_selector_scopes_the_edit(self, template_document):
        assert replace_text(template_document, "{{client}}", "X", selector="heading") == 1
        assert "{{client}}" in template_document.body[1].text

    def test_regex_with_backreference(self, template_document):
        count = replace_text(template_document, r"FY(\d{4})", r"fiscal \1", regex=True)
        assert count == 2
        assert "fiscal 2024" in template_document.body[1].text

    def test_callable_replacement(self, template_document):
        count = replace_text(
            template_document,
            r"\d+",
            lambda match: str(int(match.group()) * 2),
            regex=True,
            selector="table",
        )
        assert count == 1
        assert template_document.tables[0].to_matrix()[1][1] == "2000"

    def test_ignore_case(self, template_document):
        assert replace_text(template_document, "period", "Term", ignore_case=True) == 1

    def test_whole_word(self):
        document = Document([Paragraph.of("cat concatenate cat")])
        assert replace_text(document, "cat", "dog", whole_word=True) == 2
        assert document.text == "dog concatenate dog"

    def test_plain_fields_are_included(self, sample_document):
        assert replace_text(sample_document, "Measured", "Recorded") >= 1
        assert sample_document.tables[0].caption == "Recorded rates"

    def test_plain_fields_can_be_excluded(self, sample_document):
        replace_text(sample_document, "Measured", "Recorded", include_plain=False)
        assert sample_document.tables[0].caption == "Measured rates"

    def test_metadata_is_updated(self):
        from polydoc.model import Metadata

        document = Document(
            metadata=Metadata(
                title="Offer for {{client}}",
                subject="Quote for {{client}}",
                keywords=["{{client}}", "offer"],
                authors=["{{client}} Ltd"],
            ),
            body=[Heading.of("Offer for {{client}}")],
        )
        assert replace_text(document, "{{client}}", "Acme") == 5
        assert document.metadata.title == "Offer for Acme"
        assert document.metadata.subject == "Quote for Acme"
        assert document.metadata.keywords == ["Acme", "offer"]
        assert document.metadata.authors == ["Acme Ltd"]

    def test_metadata_excluded_with_include_plain_false(self):
        from polydoc.model import Metadata

        document = Document(metadata=Metadata(title="{{x}}"), body=[Paragraph.of("{{x}}")])
        replace_text(document, "{{x}}", "y", include_plain=False)
        assert document.metadata.title == "{{x}}"
        assert document.body[0].text == "y"

    def test_code_body_is_editable(self, sample_document):
        assert replace_text(sample_document, "x + 1", "x + 2") == 1
        code = [b for b in sample_document.body if b.type == "code_block"][0]
        assert "x + 2" in code.code

    def test_invalid_regex_raises(self, simple_document):
        with pytest.raises(EditError, match="Invalid pattern"):
            replace_text(simple_document, "([unclosed", "x", regex=True)

    def test_document_method_delegates(self, template_document):
        assert template_document.replace_text("{{client}}", "Acme") == 2


class TestFindText:
    def test_finds_across_runs(self, template_document):
        found = find_text(template_document, "FY2024 Q3")
        assert [m.text for m in found] == ["FY2024 Q3"]

    def test_reports_offsets_and_style(self, template_document):
        match = find_text(template_document, "FY2024")[0]
        assert match.start == 8
        assert match.style.bold is True

    def test_context_is_included(self, sample_document):
        match = find_text(sample_document, "bold", context=5)[0]
        assert "bold" in match.context and len(match.context) > 4

    def test_selector_scoping(self, template_document):
        assert len(find_text(template_document, "{{client}}", selector="heading")) == 1

    def test_regex_search(self, template_document):
        assert len(find_text(template_document, r"FY\d{4}", regex=True)) == 2

    def test_repr_is_informative(self, template_document):
        assert "TextMatch" in repr(find_text(template_document, "Period")[0])


class TestStyleText:
    def test_splits_the_match_into_its_own_run(self):
        document = Document([Paragraph.of("Status: OVERDUE today")])
        assert style_text(document, "OVERDUE", bold=True, color="#cc0000") == 1

        runs = [(r.text, r.style.bold, r.style.color) for r in document.body[0].content]
        assert ("OVERDUE", True, "#cc0000") in runs
        # Neighbouring text is untouched.
        assert any(r[0] == "Status: " and r[1] is None for r in runs)

    def test_preserves_existing_style_of_the_match(self):
        document = Document([Paragraph([Text("keep", TextStyle(italic=True))])])
        style_text(document, "keep", bold=True)
        run = document.body[0].content[0]
        assert run.style.italic is True and run.style.bold is True

    def test_multiple_occurrences(self):
        document = Document([Paragraph.of("a X b X c")])
        assert style_text(document, "X", bold=True) == 2

    def test_inside_a_link(self, template_document):
        assert style_text(template_document, "terms", bold=True) == 1
        link = [n for n in template_document.body[2].content if isinstance(n, Link)][0]
        assert link.href  # link intact
        assert any(r.style.bold for r in link.content)

    def test_selector_scoping(self, sample_document):
        assert style_text(sample_document, "Reader", bold=True, selector="table") == 1

    def test_requires_a_style(self, simple_document):
        with pytest.raises(EditError, match="at least one style"):
            style_text(simple_document, "Title")


class TestSetText:
    def test_replaces_all_content(self):
        heading = Heading.of("Old title")
        assert set_text(heading, "New title").text == "New title"

    def test_keeps_the_first_run_style(self):
        paragraph = Paragraph([Text("a", TextStyle(bold=True)), Text("b")])
        set_text(paragraph, "replaced")
        assert paragraph.content[0].style.bold is True

    def test_empty_string_clears(self):
        assert set_text(Paragraph.of("x"), "").text == ""

    def test_falls_back_to_plain_fields(self):
        from polydoc.model import CodeBlock

        block = CodeBlock("old")
        assert set_text(block, "new").code == "new"

    def test_unsupported_node_raises(self):
        from polydoc.model import HorizontalRule

        with pytest.raises(EditError, match="no editable text"):
            set_text(HorizontalRule(), "x")


class TestStructuralOps:
    def test_insert_before_and_after(self, simple_document):
        insert_before(simple_document.body[0], Paragraph.of("pre"))
        insert_after(simple_document.body[-1], Paragraph.of("post"))
        assert simple_document.body[0].text == "pre"
        assert simple_document.body[-1].text == "post"

    def test_inserted_nodes_are_adopted(self, simple_document):
        block = Paragraph.of("new")
        insert_after(simple_document.body[0], block)
        assert block.parent is simple_document

    def test_insert_without_parent_raises(self):
        with pytest.raises(EditError, match="no parent"):
            insert_after(Paragraph.of("orphan"), Paragraph.of("x"))

    def test_remove(self, simple_document):
        remove(simple_document.body[0])
        assert len(simple_document.body) == 1

    def test_remove_all_by_selector(self):
        document = Document([Heading.of("A"), Paragraph.of(""), Paragraph.of("keep")])
        assert remove_all(document, "paragraph:empty") == 1
        assert len(document.body) == 2

    def test_remove_all_handles_nested(self, sample_document):
        before = len(select(sample_document, "list_item"))
        assert remove_all(sample_document, "list_item") == before
        assert select(sample_document, "list_item") == []

    def test_replace_block(self, simple_document):
        replacement = Paragraph.of("swapped")
        replace_block(simple_document.body[0], replacement)
        assert simple_document.body[0] is replacement

    def test_replace_block_with_nothing_removes(self, simple_document):
        replace_block(simple_document.body[0])
        assert len(simple_document.body) == 1

    def test_move_after(self, simple_document):
        first, second = simple_document.body
        move(first, second, "after")
        assert simple_document.body == [second, first]

    def test_move_into_a_container(self):
        holder = Container(role="aside")
        block = Paragraph.of("x")
        document = Document([block, holder])
        move(block, holder, "end")
        assert holder.content == [block]
        assert block.parent is holder

    def test_move_into_own_subtree_is_rejected(self):
        holder = Container(content=[Paragraph.of("x")], role="a")
        Document([holder])
        with pytest.raises(EditError, match="own subtree"):
            move(holder, holder.content[0], "after")

    def test_move_onto_itself_is_rejected(self, simple_document):
        with pytest.raises(EditError, match="relative to itself"):
            move(simple_document.body[0], simple_document.body[0])

    def test_move_bad_position_is_rejected(self, simple_document):
        first, second = simple_document.body
        with pytest.raises(EditError, match="Unknown position"):
            move(first, second, "sideways")

    def test_wrap(self, simple_document):
        holder = wrap(simple_document.body[0], role="aside")
        assert holder.type == "container" and holder.role == "aside"
        assert simple_document.body[0] is holder
        assert holder.content[0].type == "heading"

    def test_unwrap(self):
        holder = Container(content=[Paragraph.of("a"), Paragraph.of("b")], role="g")
        document = Document([holder])
        promoted = unwrap(holder)
        assert len(promoted) == 2
        assert [b.text for b in document.body] == ["a", "b"]

    def test_unwrap_empty_container_removes_it(self):
        holder = Container(role="g")
        document = Document([holder])
        assert unwrap(holder) == []
        assert document.body == []


class TestBulkTransforms:
    def test_map_blocks_mutating(self, sample_document):
        def upper(node):
            for run in node.content:
                if isinstance(run, Text):
                    run.text = run.text.upper()

        count = map_blocks(sample_document, upper, "heading")
        assert count == 4
        assert sample_document.body[0].text == "QUARTERLY REPORT"

    def test_map_blocks_replacing(self, simple_document):
        map_blocks(simple_document, lambda node: Paragraph.of("flat"), "heading")
        assert simple_document.body[0].type == "paragraph"

    def test_map_blocks_removing(self, simple_document):
        map_blocks(simple_document, lambda node: False, "heading")
        assert len(simple_document.body) == 1

    def test_map_blocks_expanding(self, simple_document):
        map_blocks(
            simple_document,
            lambda node: [Paragraph.of("a"), Paragraph.of("b")],
            "heading",
        )
        assert [b.text for b in simple_document.body[:2]] == ["a", "b"]

    def test_restyle_routes_attributes(self, sample_document):
        assert restyle(sample_document, "h1", color="#003366", alignment="center") == 1
        heading = sample_document.body[0]
        assert heading.style.alignment is Alignment.CENTER
        assert heading.content[0].style.color == "#003366"

    def test_restyle_rejects_unknown_attribute(self, sample_document):
        with pytest.raises(EditError, match="Unknown style attribute"):
            restyle(sample_document, "h1", nonsense=1)

    def test_shift_heading_levels(self, sample_document):
        assert shift_heading_levels(sample_document, 1) == 4
        assert [h.level for h in sample_document.headings] == [2, 3, 3, 3]

    def test_shift_clamps_at_the_boundaries(self):
        document = Document([Heading.of("a", 1)])
        shift_heading_levels(document, -5)
        assert document.headings[0].level == 1
        shift_heading_levels(document, 99)
        assert document.headings[0].level == 6

    def test_strip_empty_removes_blank_blocks(self):
        document = Document(
            [Heading.of("keep"), Paragraph.of(""), Paragraph.of("   "), Paragraph.of("x")]
        )
        assert strip_empty(document) == 2
        assert [b.text for b in document.body] == ["keep", "x"]

    def test_strip_empty_keeps_rules_and_breaks(self):
        from polydoc.model import HorizontalRule, PageBreak

        document = Document([HorizontalRule(), PageBreak()])
        assert strip_empty(document) == 0
        assert len(document.body) == 2

    def test_strip_empty_keeps_paragraphs_holding_images(self):
        from polydoc.model import InlineImage

        document = Document([Paragraph([InlineImage(src="x.png")])])
        assert strip_empty(document) == 0

    def test_merge_adjacent_paragraphs(self):
        document = Document([Paragraph.of("First half"), Paragraph.of("second half")])
        assert merge_adjacent_paragraphs(document) == 1
        assert document.body[0].text == "First half second half"

    def test_merge_respects_differing_styles(self):
        document = Document(
            [Paragraph.of("body"), Paragraph.of("caption", alignment=Alignment.CENTER)]
        )
        assert merge_adjacent_paragraphs(document) == 0


class TestPipeline:
    def test_composes_steps_in_order(self):
        pipeline = Pipeline().replace("draft", "final").then(strip_empty)
        document = pipeline(Document([Paragraph.of("a draft"), Paragraph.of("")]))
        assert document.text == "a final"
        assert len(document.body) == 1

    def test_is_reusable(self):
        pipeline = Pipeline().replace("x", "y")
        for _ in range(3):
            assert pipeline(Document([Paragraph.of("x")])).text == "y"

    def test_fluent_methods(self, sample_document):
        pipeline = (
            Pipeline(name="tidy")
            .style("bold", bold=True)
            .remove("paragraph:empty")
            .restyle("h1", alignment="center")
            .shift_headings(1)
            .map(lambda node: None, "heading")
        )
        assert len(pipeline) == 5
        result = pipeline(sample_document)
        assert result.headings[0].level == 2

    def test_repr_reports_step_count(self):
        assert "2 steps" in repr(Pipeline().replace("a", "b").remove("p:empty"))

    def test_step_returning_a_document_replaces_it(self):
        replacement = Document([Paragraph.of("new")])
        pipeline = Pipeline(lambda doc: replacement)
        assert pipeline(Document()) is replacement

    def test_usable_as_a_convert_transform(self, tmp_path, simple_document):
        import polydoc

        source = tmp_path / "in.md"
        source.write_text("# Draft title\n\nDraft body.\n", encoding="utf-8")
        target = tmp_path / "out.md"
        polydoc.convert(source, target, transform=Pipeline().replace("Draft", "Final"))
        assert "Final title" in target.read_text(encoding="utf-8")
