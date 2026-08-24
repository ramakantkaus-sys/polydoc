"""The CSS-like selector engine."""

from __future__ import annotations

import pytest

from polydoc.edit import compile_selector, matches, select, select_one
from polydoc.exceptions import SelectorError
from polydoc.model import (
    Container,
    Document,
    Heading,
    ListBlock,
    ListItem,
    ListStyle,
    Paragraph,
    Section,
    Table,
)


class TestTypeSelectors:
    def test_by_type_name(self, sample_document):
        assert len(select(sample_document, "heading")) == 4

    def test_universal_matches_everything(self, simple_document):
        assert len(select(simple_document, "*")) == len(list(simple_document.walk()))

    def test_unknown_type_matches_nothing(self, sample_document):
        assert select(sample_document, "nonexistent") == []

    @pytest.mark.parametrize(
        "alias,expected_type",
        [
            ("p", "paragraph"),
            ("li", "list_item"),
            ("td", "table_cell"),
            ("tr", "table_row"),
            ("pre", "code_block"),
            ("hr", "horizontal_rule"),
            ("a", "link"),
            ("blockquote", "quote"),
        ],
    )
    def test_html_aliases(self, sample_document, alias, expected_type):
        found = select(sample_document, alias)
        assert found and all(node.type == expected_type for node in found)

    def test_heading_level_aliases(self, sample_document):
        assert len(select(sample_document, "h1")) == 1
        assert len(select(sample_document, "h2")) == 3

    def test_list_aliases_respect_ordering(self, sample_document):
        assert len(select(sample_document, "ol")) == 1
        assert len(select(sample_document, "ul")) == 1


class TestAttributeSelectors:
    def test_equality(self, sample_document):
        assert len(select(sample_document, "heading[level=2]")) == 3

    def test_inequality(self, sample_document):
        assert len(select(sample_document, "heading[level!=2]")) == 1

    def test_numeric_comparisons(self, sample_document):
        assert len(select(sample_document, "heading[level>1]")) == 3
        assert len(select(sample_document, "heading[level<2]")) == 1
        assert len(select(sample_document, "heading[level>=2]")) == 3
        assert len(select(sample_document, "heading[level<=1]")) == 1

    def test_presence(self, sample_document):
        assert select(sample_document, "table[caption]")

    def test_string_operators(self):
        document = Document([Paragraph.of("x")], )
        from polydoc.model import Image

        document.append(Image(src="photos/cat.png", alt="a cat"))
        assert select(document, "image[src^=photos/]")
        assert select(document, "image[src$=.png]")
        assert select(document, "image[src*=cat]")
        assert not select(document, "image[src^=other/]")

    def test_dotted_path_into_style(self, sample_document):
        found = select(sample_document, "paragraph[style.alignment=center]")
        assert len(found) == 1 and found[0].text == "Centred text."

    def test_enum_compares_by_value(self, sample_document):
        assert select(sample_document, "list_block[marker_style=ordered]")

    def test_boolean_attribute(self):
        document = Document([ListBlock([ListItem.of("a")], ListStyle.BULLET)])
        document.body[0].items[0].checked = True
        assert select(document, "list_item[checked=true]")
        assert not select(document, "list_item[checked=false]")

    def test_quoted_values(self, sample_document):
        assert select(sample_document, 'table[caption="Measured rates"]')

    def test_attrs_dict_is_searchable(self):
        block = Paragraph.of("x")
        block.attrs["role"] = "caption"
        assert select(Document([block]), "paragraph[role=caption]")

    def test_malformed_attribute_raises(self, sample_document):
        with pytest.raises(SelectorError):
            select(sample_document, "heading[level")


class TestPseudoClasses:
    def test_contains_is_case_insensitive(self, sample_document):
        assert select(sample_document, "heading:contains(findings)")

    def test_matches_uses_regex(self, sample_document):
        assert select(sample_document, r"heading:matches(^Quarterly)")

    def test_bad_regex_raises(self, sample_document):
        with pytest.raises(SelectorError):
            select(sample_document, "heading:matches([unclosed)")

    def test_empty(self):
        document = Document([Paragraph.of(""), Paragraph.of("x")])
        assert len(select(document, "paragraph:empty")) == 1

    def test_not(self):
        document = Document([Paragraph.of(""), Paragraph.of("x")])
        assert len(select(document, "paragraph:not(:empty)")) == 1

    def test_has(self):
        document = Document([Section(content=[Table.from_rows([["a"], ["b"]])], level=1)])
        assert select(document, "section:has(table)")
        assert not select(document, "section:has(image)")

    def test_first_and_last(self, sample_document):
        rows = select(sample_document, "table_row")
        assert select(sample_document, "table_row:first") == [rows[0]]
        assert select(sample_document, "table_row:last") == [rows[-1]]

    def test_nth_is_one_based(self, sample_document):
        rows = select(sample_document, "table_row")
        assert select(sample_document, "table_row:nth(2)") == [rows[1]]

    def test_nth_accepts_negative_index(self, sample_document):
        rows = select(sample_document, "table_row")
        assert select(sample_document, "table_row:nth(-1)") == [rows[-1]]

    def test_root(self, simple_document):
        assert select(simple_document, ":root") == [simple_document]

    def test_only(self):
        document = Document([Heading.of("solo"), Paragraph.of("a"), Paragraph.of("b")])
        assert len(select(document, "heading:only")) == 1
        assert len(select(document, "paragraph:only")) == 0

    def test_unknown_pseudo_raises(self, sample_document):
        with pytest.raises(SelectorError, match="Unknown pseudo-class"):
            select(sample_document, "heading:bogus")

    def test_pseudo_requiring_argument(self, sample_document):
        with pytest.raises(SelectorError, match="requires an argument"):
            select(sample_document, "heading:contains")

    def test_bare_pseudo_rejects_argument(self, sample_document):
        with pytest.raises(SelectorError, match="takes no argument"):
            select(sample_document, "heading:first(2)")


class TestCombinators:
    def test_descendant(self, sample_document):
        assert len(select(sample_document, "table table_cell")) == 9

    def test_direct_child(self, sample_document):
        rows = select(sample_document, "table > table_row")
        assert len(rows) == 3

    def test_direct_child_excludes_deeper(self, sample_document):
        # Cells are grandchildren of the table, so this must find nothing.
        assert select(sample_document, "table > table_cell") == []

    def test_union(self, sample_document):
        assert len(select(sample_document, "heading, table")) == 5

    def test_union_deduplicates_nothing_unexpected(self, sample_document):
        assert len(select(sample_document, "heading, heading")) == 4

    def test_deep_chain(self, sample_document):
        assert select(sample_document, "table table_row table_cell paragraph")

    def test_nested_list_via_descendant(self, sample_document):
        assert select(sample_document, "list_block list_block")


class TestCompilation:
    def test_compiled_selectors_are_cached(self):
        assert compile_selector("heading") is compile_selector("heading")

    def test_compile_accepts_a_selector(self):
        selector = compile_selector("heading")
        assert compile_selector(selector) is selector

    def test_empty_selector_raises(self):
        with pytest.raises(SelectorError, match="Empty selector"):
            compile_selector("   ")

    def test_non_string_raises(self):
        with pytest.raises(SelectorError):
            compile_selector(42)

    def test_repr_keeps_the_source(self):
        assert repr(compile_selector("h1, p")) == "Selector('h1, p')"

    def test_whitespace_is_tolerated(self, sample_document):
        assert select(sample_document, "  table   >   table_row  ")


class TestHelpers:
    def test_select_one_returns_first(self, sample_document):
        assert select_one(sample_document, "heading").text == "Quarterly Report"

    def test_select_one_returns_none(self, sample_document):
        assert select_one(sample_document, "image") is None

    def test_matches_single_node(self, sample_document):
        heading = sample_document.body[0]
        assert matches(heading, "heading[level=1]")
        assert not matches(heading, "heading[level=2]")

    def test_document_find_delegates(self, sample_document):
        assert sample_document.find("h2").text == "Findings"

    def test_document_find_all_delegates(self, sample_document):
        assert len(sample_document.find_all("heading")) == 4

    def test_id_selector(self, sample_document):
        heading = sample_document.body[0]
        assert select(sample_document, f"#{heading.nid}") == [heading]
