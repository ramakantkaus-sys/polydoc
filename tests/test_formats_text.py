"""Text-based formats: Markdown, HTML, plain text, JSON, CSV."""

from __future__ import annotations

import json

import pytest

from conftest import needs_html, needs_markdown

import polydoc
from polydoc.model import Alignment, Document, Heading, ListStyle, Paragraph, Table


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

MARKDOWN = """\
# Title

Some **bold** and *italic* and `code` and a [link](https://example.com).

## Section

- first
- second
  - nested
- third

1. step one
2. step two

- [x] done
- [ ] pending

| Name | Qty | Price |
|:-----|----:|:-----:|
| Bolt | 4   | 0.50  |

> A quotation.

```python
def hello():
    return 1
```

---
"""


@needs_markdown
class TestMarkdownReader:
    @pytest.fixture
    def document(self):
        return polydoc.loads(MARKDOWN, "markdown")

    def test_headings_and_levels(self, document):
        assert [(h.level, h.text) for h in document.headings] == [
            (1, "Title"),
            (2, "Section"),
        ]

    def test_title_from_first_h1(self, document):
        assert document.metadata.title == "Title"

    def test_inline_styles(self, document):
        runs = {r.text: r.style for r in document.body[1].content if r.type == "text"}
        assert runs["bold"].bold is True
        assert runs["italic"].italic is True
        assert runs["code"].is_monospace

    def test_link(self, document):
        links = [n for n in document.body[1].content if n.type == "link"]
        assert links[0].href == "https://example.com"
        assert links[0].text == "link"

    def test_nested_list(self, document):
        lists = [b for b in document.body if b.type == "list_block"]
        bullet = lists[0]
        assert len(bullet.items) == 3
        assert len(bullet.items[1].sublists) == 1
        assert bullet.items[1].sublists[0].items[0].text == "nested"

    def test_ordered_list(self, document):
        lists = [b for b in document.body if b.type == "list_block"]
        ordered = [item for item in lists if item.marker_style is ListStyle.ORDERED]
        assert ordered and len(ordered[0].items) == 2

    def test_task_list(self, document):
        lists = [b for b in document.body if b.type == "list_block"]
        tasks = [item for item in lists if any(i.checked is not None for i in item.items)]
        assert tasks and [i.checked for i in tasks[0].items] == [True, False]

    def test_table_with_alignment(self, document):
        table = document.tables[0]
        assert table.dimensions == (2, 3)
        assert table.header_rows == 1
        alignments = [
            cell.content[0].style.alignment for cell in table.rows[1].cells
        ]
        assert alignments == [Alignment.LEFT, Alignment.RIGHT, Alignment.CENTER]

    def test_code_fence_language(self, document):
        code = [b for b in document.body if b.type == "code_block"][0]
        assert code.language == "python"
        assert code.code == "def hello():\n    return 1"

    def test_quote(self, document):
        quotes = [b for b in document.body if b.type == "quote"]
        assert quotes and quotes[0].text == "A quotation."

    def test_horizontal_rule(self, document):
        assert any(b.type == "horizontal_rule" for b in document.body)

    def test_front_matter(self):
        source = "---\ntitle: From Front Matter\nauthors: A, B\nlanguage: fr\n---\n\nBody.\n"
        document = polydoc.loads(source, "markdown")
        assert document.metadata.title == "From Front Matter"
        assert document.metadata.authors == ["A", "B"]
        assert document.metadata.language == "fr"

    def test_works_without_linkify_it_installed(self, monkeypatch):
        """Regression: the ``gfm-like`` preset enables ``linkify``, which raises at
        parse time unless the optional ``linkify-it-py`` package is present.

        That package is not a declared dependency, so a clean install must still parse
        Markdown. Only a clean-environment check catches this, hence the explicit test.
        """
        import importlib.util as importlib_util

        real_find_spec = importlib_util.find_spec

        def fake_find_spec(name, *args, **kwargs):
            if name == "linkify_it":
                return None
            return real_find_spec(name, *args, **kwargs)

        monkeypatch.setattr(importlib_util, "find_spec", fake_find_spec)

        document = polydoc.loads("See https://example.com for detail.\n", "markdown")
        assert "example.com" in document.text


@needs_markdown
class TestMarkdownWriter:
    def test_round_trip_is_stable(self):
        once = polydoc.loads(MARKDOWN, "markdown").to_text("markdown")
        twice = polydoc.loads(once, "markdown").to_text("markdown")
        assert once == twice

    def test_blocks_separated_by_blank_lines(self, simple_document):
        assert simple_document.to_text("markdown") == "# Title\n\nOne paragraph.\n"

    def test_structure_survives(self, sample_document):
        text = sample_document.to_text("markdown")
        reread = polydoc.loads(text, "markdown")
        assert [h.text for h in reread.headings] == [h.text for h in sample_document.headings]
        assert reread.tables[0].to_matrix() == sample_document.tables[0].to_matrix()

    def test_escaping_prevents_accidental_markup(self):
        document = Document([Paragraph.of("A * not italic * and _not_ either")])
        text = document.to_text("markdown")
        assert polydoc.loads(text, "markdown").text == document.text

    def test_line_start_escaping(self):
        document = Document([Paragraph.of("1. not a list")])
        reread = polydoc.loads(document.to_text("markdown"), "markdown")
        assert reread.body[0].type == "paragraph"

    def test_heading_needs_no_line_start_escape(self):
        document = Document([Heading.of("1. Overview")])
        assert document.to_text("markdown").startswith("# 1. Overview")

    def test_code_fence_grows_past_inner_backticks(self):
        from polydoc.model import CodeBlock

        document = Document([CodeBlock("a ``` b")])
        text = document.to_text("markdown")
        assert "````" in text
        assert polydoc.loads(text, "markdown").body[0].code == "a ``` b"

    def test_front_matter_option(self, simple_document):
        text = simple_document.to_text("markdown", front_matter=True)
        assert text.startswith("---\ntitle: Simple")

    def test_table_headers_are_not_double_emphasised(self):
        from polydoc.model import Text, TextStyle, TableCell, TableRow

        header = TableRow([TableCell([Paragraph([Text("Name", TextStyle(bold=True))])])],
                          is_header=True)
        table = Table([header, TableRow.of(["x"])])
        assert "**Name**" not in Document([table]).to_text("markdown")


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Sample Page</title>
  <meta name="author" content="Ada Lovelace, Alan Turing">
  <meta name="keywords" content="alpha, beta">
</head>
<body>
  <header>Site header</header>
  <h1>Main Title</h1>
  <p>Intro with <strong>bold</strong>, <em>italic</em>, <code>code</code>,
     and a <a href="https://example.com" title="ex">link</a>.</p>
  <p style="text-align:center;color:#ff0000">Centred red text.</p>
  <ul>
    <li>alpha</li>
    <li>beta<ol start="3" type="a"><li>one</li><li>two</li></ol></li>
  </ul>
  <ul>
    <li><input type="checkbox" checked> done</li>
    <li><input type="checkbox"> pending</li>
  </ul>
  <table>
    <caption>Parts</caption>
    <thead><tr><th>Name</th><th>Qty</th></tr></thead>
    <tbody>
      <tr><td>Bolt</td><td align="right">4</td></tr>
      <tr><td colspan="2">note</td></tr>
    </tbody>
  </table>
  <blockquote><p>Quoted.</p><footer>Someone</footer></blockquote>
  <pre><code class="language-python">def f():
    return 2</code></pre>
  <figure><img src="pic.png" alt="a picture" width="320"><figcaption>Caption</figcaption></figure>
  <hr>
  <div>Bare text then a table.<table><tr><td>x</td></tr></table></div>
</body>
</html>
"""


@needs_html
class TestHTMLReader:
    @pytest.fixture
    def document(self):
        return polydoc.loads(HTML, "html")

    def test_metadata_read_before_head_is_stripped(self, document):
        assert document.metadata.title == "Sample Page"
        assert document.metadata.authors == ["Ada Lovelace", "Alan Turing"]
        assert document.metadata.keywords == ["alpha", "beta"]
        assert document.metadata.language == "en"

    def test_inline_styles_and_links(self, document):
        paragraph = document.find("paragraph:contains(Intro with)")
        runs = {r.text: r.style for r in paragraph.content if r.type == "text"}
        assert runs["bold"].bold and runs["italic"].italic and runs["code"].is_monospace
        links = [n for n in paragraph.content if n.type == "link"]
        assert links[0].href == "https://example.com" and links[0].title == "ex"

    def test_inline_css_is_parsed(self, document):
        centred = [b for b in document.body if getattr(b.style, "alignment", None)]
        assert centred[0].style.alignment is Alignment.CENTER
        assert centred[0].content[0].style.color == "#ff0000"

    def test_whitespace_is_collapsed(self, document):
        # The source HTML indents this paragraph across two lines.
        text = document.find("paragraph:contains(Intro with)").text
        assert "\n" not in text and "  " not in text

    def test_nested_ordered_list_attributes(self, document):
        lists = [b for b in document.blocks() if b.type == "list_block"]
        nested = lists[0].items[1].sublists[0]
        assert nested.start == 3
        assert nested.marker_style is ListStyle.LOWER_ALPHA

    def test_task_list_checkboxes(self, document):
        lists = [b for b in document.blocks() if b.type == "list_block"]
        tasks = [item for item in lists if any(i.checked is not None for i in item.items)]
        assert [i.checked for i in tasks[0].items] == [True, False]

    def test_table_caption_header_and_span(self, document):
        table = document.tables[0]
        assert table.caption == "Parts"
        assert table.header_rows == 1
        assert table.rows[2].cells[0].colspan == 2

    def test_quote_attribution(self, document):
        quote = [b for b in document.blocks() if b.type == "quote"][0]
        assert quote.attribution == "Someone"
        assert quote.text == "Quoted."

    def test_code_language_from_class(self, document):
        code = [b for b in document.blocks() if b.type == "code_block"][0]
        assert code.language == "python"

    def test_figure_becomes_image_with_caption(self, document):
        image = document.images[0]
        assert (image.src, image.alt, image.caption) == ("pic.png", "a picture", "Caption")
        assert image.width == 320

    def test_semantic_container(self, document):
        containers = [b for b in document.body if b.type == "container"]
        assert containers and containers[0].role == "header"

    def test_mixed_content_in_a_div(self, document):
        # Bare text next to a table must produce both, in order.
        types = [b.type for b in document.body]
        assert types[-2:] == ["paragraph", "table"]


@needs_html
class TestHTMLWriter:
    def test_standalone_document(self, simple_document):
        html = simple_document.to_text("html")
        assert html.startswith("<!DOCTYPE html>")
        assert "<title>Simple</title>" in html
        assert 'name="generator" content="polydoc"' in html

    def test_fragment_mode(self, simple_document):
        html = simple_document.to_text("html", standalone=False)
        assert not html.startswith("<!DOCTYPE")
        assert html.strip().startswith("<h1>")

    def test_semantic_tags_not_style_spans(self):
        from polydoc.model import Text, TextStyle

        document = Document([Paragraph([Text("x", TextStyle(bold=True, italic=True))])])
        html = document.to_text("html", standalone=False)
        assert "<strong>" in html and "<em>" in html

    def test_escaping(self):
        document = Document([Paragraph.of("a < b & c > d")])
        html = document.to_text("html", standalone=False)
        assert "&lt;" in html and "&amp;" in html

    def test_thead_for_header_rows(self, sample_document):
        html = sample_document.to_text("html", standalone=False)
        assert "<thead>" in html and "<th>" in html

    def test_round_trip_preserves_text(self, sample_document):
        html = sample_document.to_text("html", standalone=False)
        reread = polydoc.loads(html, "html")
        assert " ".join(reread.text.split()) == " ".join(sample_document.text.split())

    def test_round_trip_is_stable(self):
        once = polydoc.loads(HTML, "html")
        twice = polydoc.loads(once.to_text("html", standalone=False), "html")
        assert len(twice.body) == len(once.body)


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------


class TestPlainTextReader:
    def test_paragraphs_split_on_blank_lines(self):
        document = polydoc.loads("First para.\n\nSecond para.\n", "txt")
        assert [b.text for b in document.body] == ["First para.", "Second para."]

    def test_wrapped_lines_are_joined(self):
        document = polydoc.loads("A sentence that\nwraps across lines.\n", "txt")
        assert document.body[0].text == "A sentence that wraps across lines."

    def test_all_caps_heading(self):
        document = polydoc.loads("EXECUTIVE SUMMARY\n\nBody text here.\n", "txt")
        assert document.body[0].type == "heading"
        assert document.metadata.title == "EXECUTIVE SUMMARY"

    def test_numbered_heading_level_from_depth(self):
        document = polydoc.loads("2.1 Scope Of Work\n\nBody.\n", "txt")
        assert document.body[0].type == "heading"
        assert document.body[0].level == 2

    def test_list_detection_with_nesting(self):
        document = polydoc.loads("- one\n- two\n  - nested\n", "txt")
        block = document.body[0]
        assert block.type == "list_block"
        assert len(block.items) == 2
        assert block.items[1].sublists[0].items[0].text == "nested"

    def test_ordered_list_detection(self):
        document = polydoc.loads("1. first\n2. second\n", "txt")
        assert document.body[0].marker_style is ListStyle.ORDERED

    def test_code_detection(self):
        source = "def f():\n    return 1\n\nclass A:\n    pass\n"
        document = polydoc.loads(source, "txt")
        assert any(b.type == "code_block" for b in document.body)

    def test_structure_detection_can_be_disabled(self):
        document = polydoc.loads("- one\n- two\n", "txt", detect_structure=False)
        assert document.body[0].type == "paragraph"

    def test_preserve_line_breaks(self):
        document = polydoc.loads("a\nb\n", "txt", preserve_line_breaks=True)
        assert any(n.type == "line_break" for n in document.body[0].content)

    def test_prose_is_not_mistaken_for_a_heading(self):
        document = polydoc.loads("This is a normal sentence.\n", "txt")
        assert document.body[0].type == "paragraph"


class TestPlainTextWriter:
    def test_headings_are_underlined(self, simple_document):
        text = simple_document.to_text("txt")
        assert "Title\n=====" in text

    def test_tables_are_drawn_as_a_grid(self, sample_document):
        text = sample_document.to_text("txt")
        assert "+------" in text and "| Component" in text

    def test_links_shown_inline(self, sample_document):
        assert "<https://example.com>" in sample_document.to_text("txt")

    def test_links_can_be_hidden(self, sample_document):
        assert "<https://example.com>" not in sample_document.to_text("txt", show_links=False)

    def test_lists_are_indented(self, sample_document):
        text = sample_document.to_text("txt")
        assert "  * nested a" in text or "  1. " in text or "* nested a" in text


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


class TestJSON:
    def test_native_round_trip_is_lossless(self, sample_document):
        payload = sample_document.to_text("json")
        reread = polydoc.loads(payload, "json")
        assert reread.body == sample_document.body
        assert reread.metadata.title == sample_document.metadata.title

    def test_text_projection(self, sample_document):
        payload = json.loads(sample_document.to_text("json", mode="text"))
        assert "blocks" in payload
        assert all("text" in item for item in payload["blocks"])

    def test_outline_projection(self, sample_document):
        payload = json.loads(sample_document.to_text("json", mode="outline"))
        assert payload["outline"][0]["title"] == "Quarterly Report"

    def test_tabular_json_becomes_a_table(self):
        source = '[{"name": "Bolt", "qty": 4}, {"name": "Nut", "qty": 12}]'
        document = polydoc.loads(source, "json")
        assert document.tables[0].to_matrix() == [
            ["name", "qty"],
            ["Bolt", "4"],
            ["Nut", "12"],
        ]

    def test_enveloped_records(self):
        source = '{"data": [{"a": 1}, {"a": 2}]}'
        assert polydoc.loads(source, "json").tables[0].dimensions == (3, 1)

    def test_arbitrary_json_is_preserved_as_code(self):
        document = polydoc.loads('{"nested": {"deep": [1, 2]}}', "json")
        assert document.body[0].type == "code_block"
        assert json.loads(document.body[0].code) == {"nested": {"deep": [1, 2]}}

    def test_invalid_json_raises(self):
        from polydoc.exceptions import ParseError

        with pytest.raises(ParseError, match="Invalid JSON"):
            polydoc.loads("{not json", "json")


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


class TestCSV:
    def test_basic_read(self):
        document = polydoc.loads("a,b\n1,2\n", "csv")
        assert document.tables[0].to_matrix() == [["a", "b"], ["1", "2"]]

    def test_header_is_detected(self):
        document = polydoc.loads("name,qty\nBolt,4\nNut,12\n", "csv")
        assert document.tables[0].header_rows == 1

    def test_semicolon_delimiter_is_sniffed(self):
        document = polydoc.loads("name;qty\nBolt;4\n", "csv")
        assert document.tables[0].to_matrix() == [["name", "qty"], ["Bolt", "4"]]

    def test_tab_delimiter_via_extension(self):
        document = polydoc.loads("a\tb\n1\t2\n", "csv", name="data.tsv")
        assert document.tables[0].dimensions[1] == 2

    def test_explicit_delimiter(self):
        document = polydoc.loads("a|b\n1|2\n", "csv", delimiter="|")
        assert document.tables[0].to_matrix()[0] == ["a", "b"]

    def test_ragged_rows_are_padded(self):
        document = polydoc.loads("a,b,c\n1,2\n", "csv")
        assert all(len(row) == 3 for row in document.tables[0].to_matrix())

    def test_empty_input(self):
        assert polydoc.loads("", "csv").body == []

    def test_writer_emits_the_table(self, sample_document):
        output = polydoc.dumps(sample_document, "csv").decode()
        assert "Component,Trials,Rate" in output

    def test_writer_falls_back_to_blocks(self, simple_document):
        output = polydoc.dumps(simple_document, "csv").decode()
        assert "type,text" in output
        assert "heading,Title" in output

    def test_round_trip(self):
        document = polydoc.loads("a,b\n1,2\n", "csv")
        again = polydoc.loads(polydoc.dumps(document, "csv").decode(), "csv")
        assert again.tables[0].to_matrix() == document.tables[0].to_matrix()

    def test_table_index_selection(self, sample_document):
        output = polydoc.dumps(sample_document, "csv", table_index=0).decode()
        assert "Component" in output

    def test_out_of_range_table_index(self, sample_document):
        from polydoc.exceptions import WriteError

        with pytest.raises(WriteError, match="out of range"):
            polydoc.dumps(sample_document, "csv", table_index=99)
