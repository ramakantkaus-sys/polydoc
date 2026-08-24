"""The public API and format detection."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from conftest import needs_docx, needs_html, needs_markdown, needs_pdf_write, needs_xlsx

import polydoc
from polydoc.exceptions import (
    FormatDetectionError,
    MissingDependencyError,
    PolydocError,
    UnsupportedFormatError,
)
from polydoc.formats import Source, detect_format, resolve_format
from polydoc.model import Document, Heading, Paragraph


class TestOpen:
    def test_from_path(self, tmp_path):
        path = tmp_path / "a.md"
        path.write_text("# Title\n", encoding="utf-8")
        assert polydoc.open(path).headings[0].text == "Title"

    def test_from_string_path(self, tmp_path):
        path = tmp_path / "a.md"
        path.write_text("# Title\n", encoding="utf-8")
        assert polydoc.open(str(path)).headings[0].text == "Title"

    def test_from_bytes_with_explicit_format(self):
        assert polydoc.open(b"# Title\n", format="markdown").headings[0].text == "Title"

    def test_from_file_object(self, tmp_path):
        path = tmp_path / "a.md"
        path.write_text("# Title\n", encoding="utf-8")
        with open(path, "rb") as handle:
            assert polydoc.open(handle, format="markdown").headings[0].text == "Title"

    def test_from_bytesio(self):
        stream = io.BytesIO(b"a,b\n1,2\n")
        assert polydoc.open(stream, format="csv").tables[0].dimensions == (2, 2)

    def test_provenance_is_recorded(self, tmp_path):
        path = tmp_path / "a.md"
        path.write_text("# Title\n", encoding="utf-8")
        document = polydoc.open(path)
        assert document.source_format == "markdown"
        assert document.source_path == str(path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            polydoc.open(tmp_path / "nope.md")

    def test_directory_raises(self, tmp_path):
        with pytest.raises(PolydocError, match="directory"):
            polydoc.open(tmp_path)

    def test_unsupported_format_raises(self):
        with pytest.raises(UnsupportedFormatError, match="No reader registered"):
            polydoc.open(b"data", format="ancient-scroll")

    def test_reader_options_are_forwarded(self):
        document = polydoc.open(b"- a\n- b\n", format="txt", detect_structure=False)
        assert document.body[0].type == "paragraph"


class TestLoads:
    def test_string_input(self):
        assert polydoc.loads("# T\n", "markdown").headings[0].text == "T"

    def test_bytes_input(self):
        assert polydoc.loads(b"# T\n", "markdown").headings[0].text == "T"

    def test_name_influences_detection_helpers(self):
        document = polydoc.loads("a\tb\n1\t2\n", "csv", name="x.tsv")
        assert document.tables[0].dimensions[1] == 2

    def test_format_is_required(self):
        with pytest.raises(TypeError):
            polydoc.loads("# T\n")  # type: ignore[call-arg]


class TestSaveAndDumps:
    def test_save_infers_format_from_extension(self, simple_document, tmp_path):
        path = simple_document.save(tmp_path / "out.md")
        assert path.read_text(encoding="utf-8").startswith("# Title")

    def test_save_creates_parent_directories(self, simple_document, tmp_path):
        path = simple_document.save(tmp_path / "deep" / "nested" / "out.md")
        assert path.exists()

    def test_save_returns_the_path(self, simple_document, tmp_path):
        target = tmp_path / "out.md"
        assert simple_document.save(target) == target

    def test_explicit_format_overrides_extension(self, simple_document, tmp_path):
        path = simple_document.save(tmp_path / "out.dat", format="markdown")
        assert path.read_text(encoding="utf-8").startswith("# Title")

    def test_unknown_extension_raises(self, simple_document, tmp_path):
        with pytest.raises(UnsupportedFormatError):
            simple_document.save(tmp_path / "out.xyz")

    def test_no_extension_raises(self, simple_document, tmp_path):
        with pytest.raises(UnsupportedFormatError):
            simple_document.save(tmp_path / "out")

    def test_dumps_returns_bytes(self, simple_document):
        assert polydoc.dumps(simple_document, "markdown") == b"# Title\n\nOne paragraph.\n"

    def test_to_text_decodes(self, simple_document):
        assert simple_document.to_text("markdown").startswith("# Title")

    def test_writer_options_are_forwarded(self, simple_document):
        assert simple_document.to_text("html", standalone=False).strip().startswith("<h1>")


class TestConvert:
    def test_basic_conversion(self, tmp_path):
        source = tmp_path / "a.md"
        source.write_text("# Title\n\nBody.\n", encoding="utf-8")
        target = polydoc.convert(source, tmp_path / "b.txt")
        assert "Title" in target.read_text(encoding="utf-8")

    def test_transform_callable(self, tmp_path):
        source = tmp_path / "a.md"
        source.write_text("# Draft\n", encoding="utf-8")
        target = polydoc.convert(
            source,
            tmp_path / "b.md",
            transform=lambda doc: doc.replace_text("Draft", "Final"),
        )
        assert "Final" in target.read_text(encoding="utf-8")

    def test_transform_list_runs_in_order(self, tmp_path):
        source = tmp_path / "a.md"
        source.write_text("# A\n", encoding="utf-8")
        target = polydoc.convert(
            source,
            tmp_path / "b.md",
            transform=[
                lambda doc: doc.replace_text("A", "B"),
                lambda doc: doc.replace_text("B", "C"),
            ],
        )
        assert "# C" in target.read_text(encoding="utf-8")

    def test_transform_returning_a_document_replaces_it(self, tmp_path):
        source = tmp_path / "a.md"
        source.write_text("# A\n", encoding="utf-8")
        target = polydoc.convert(
            source,
            tmp_path / "b.md",
            transform=lambda doc: Document([Heading.of("Replaced")]),
        )
        assert "# Replaced" in target.read_text(encoding="utf-8")

    def test_read_and_write_options(self, tmp_path):
        source = tmp_path / "a.md"
        source.write_text("# T\n", encoding="utf-8")
        target = polydoc.convert(
            source, tmp_path / "b.html", write_options={"standalone": False}
        )
        assert not target.read_text(encoding="utf-8").startswith("<!DOCTYPE")

    def test_explicit_formats(self, tmp_path):
        source = tmp_path / "data"
        source.write_text("a,b\n1,2\n", encoding="utf-8")
        target = polydoc.convert(
            source, tmp_path / "out", source_format="csv", target_format="markdown"
        )
        assert "| a" in target.read_text(encoding="utf-8")

    def test_convert_bytes_stays_in_memory(self):
        result = polydoc.convert_bytes("# T\n\nBody.\n", "markdown", "txt")
        assert b"Title" in result or b"T" in result

    def test_convert_bytes_with_transform(self):
        result = polydoc.convert_bytes(
            "# Draft\n",
            "markdown",
            "markdown",
            transform=lambda doc: doc.replace_text("Draft", "Final"),
        )
        assert b"Final" in result


class TestConversionMatrix:
    """Every readable format must be writable to every writable format."""

    SOURCES = {
        "markdown": "# Title\n\nBody **text**.\n\n| a | b |\n| - | - |\n| 1 | 2 |\n",
        "txt": "TITLE\n\nBody text.\n\n- one\n- two\n",
        "csv": "name,qty\nBolt,4\nNut,12\n",
        "html": "<h1>Title</h1><p>Body <b>text</b>.</p><table><tr><td>1</td></tr></table>",
    }

    @pytest.mark.parametrize("source_format", list(SOURCES))
    @pytest.mark.parametrize(
        "target_format",
        ["markdown", "html", "txt", "json", "csv", "docx", "pptx", "xlsx", "pdf"],
    )
    def test_pair(self, source_format, target_format, tmp_path):
        import importlib.util

        required = {
            "docx": "docx",
            "pptx": "pptx",
            "xlsx": "openpyxl",
            "pdf": "reportlab",
            "html": "bs4",
            "markdown": "markdown_it",
        }
        for fmt in (source_format, target_format):
            module = required.get(fmt)
            if module and importlib.util.find_spec(module) is None:
                pytest.skip(f"{module} not installed")

        document = polydoc.loads(self.SOURCES[source_format], source_format)
        data = polydoc.dumps(document, target_format)
        assert data, f"{source_format} -> {target_format} produced nothing"


class TestFormatDetection:
    def test_pdf_magic(self):
        assert detect_format(b"%PDF-1.7\nrest") == "pdf"

    def test_json_content(self):
        assert detect_format(b'{"type": "document", "body": []}') == "json"

    def test_html_content(self):
        assert detect_format(b"<!DOCTYPE html><html><body><p>x</p></body></html>") == "html"

    def test_markdown_heuristic(self):
        assert detect_format(b"# Heading\n\n- item\n") == "markdown"

    def test_plain_text_fallback(self):
        assert detect_format(b"Just some words with no markup at all.\n") == "txt"

    @needs_docx
    def test_docx_by_zip_members(self, simple_document, tmp_path):
        path = simple_document.save(tmp_path / "a.docx")
        assert detect_format(path.read_bytes()) == "docx"

    @needs_xlsx
    def test_xlsx_by_zip_members(self, tmp_path):
        from polydoc.model import Table

        path = Document([Table.from_rows([["a"], ["1"]])]).save(tmp_path / "a.xlsx")
        assert detect_format(path.read_bytes()) == "xlsx"

    def test_content_beats_a_wrong_extension(self, tmp_path):
        # A PDF mislabelled as .txt should still open as a PDF.
        path = tmp_path / "mislabelled.txt"
        path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        assert detect_format(path) == "pdf"

    def test_extension_used_when_content_is_ambiguous(self, tmp_path):
        path = tmp_path / "data.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        assert detect_format(path) == "csv"

    def test_explicit_hint_wins(self):
        assert detect_format(b"# Not markdown", hint="txt") == "txt"

    def test_legacy_office_is_reported_clearly(self):
        ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
        with pytest.raises(FormatDetectionError, match="legacy"):
            detect_format(ole)

    def test_bare_image_is_reported_clearly(self):
        with pytest.raises(FormatDetectionError, match="image"):
            detect_format(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    def test_empty_input_is_reported(self):
        with pytest.raises(FormatDetectionError, match="empty"):
            detect_format(b"")

    def test_binary_noise_is_reported(self):
        with pytest.raises(FormatDetectionError, match="Could not determine"):
            detect_format(bytes(range(256)) * 4)

    def test_unknown_zip_is_not_misidentified(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("random.txt", "data")
        with pytest.raises(FormatDetectionError):
            detect_format(buffer.getvalue())

    def test_detect_helper(self):
        assert polydoc.detect(b"%PDF-1.4") == "pdf"


class TestFormatNames:
    @pytest.mark.parametrize(
        "given,expected",
        [
            ("MD", "markdown"),
            ("md", "markdown"),
            (".docx", "docx"),
            ("htm", "html"),
            ("word", "docx"),
            ("excel", "xlsx"),
            ("powerpoint", "pptx"),
            ("Markdown", "markdown"),
        ],
    )
    def test_aliases_resolve(self, given, expected):
        assert resolve_format(given) == expected

    def test_formats_lists_are_populated(self):
        assert "markdown" in polydoc.formats("read")
        assert "markdown" in polydoc.formats("write")
        assert set(polydoc.formats()) >= set(polydoc.formats("read"))

    def test_supported_formats_table(self):
        rows = {row["format"]: row for row in polydoc.supported_formats()}
        assert rows["markdown"]["read"] and rows["markdown"]["write"]
        assert ".md" in rows["markdown"]["extensions"]
        assert rows["markdown"]["description"]


class TestSource:
    def test_from_bytes_and_text(self):
        source = Source.from_bytes(b"hello", name="a.txt")
        assert source.text() == "hello"
        assert source.suffix == ".txt"
        assert len(source) == 5

    def test_stream_is_fresh_each_call(self):
        source = Source.from_bytes(b"abc")
        assert source.stream().read() == b"abc"
        assert source.stream().read() == b"abc"

    def test_encoding_fallback(self):
        # cp1252 bytes that are not valid UTF-8.
        source = Source.from_bytes(b"caf\xe9")
        assert "caf" in source.text()

    def test_bom_is_handled(self):
        source = Source.from_bytes("\ufeffhello".encode("utf-8"))
        assert source.text() == "hello"

    def test_utf16_is_handled(self):
        source = Source.from_bytes("hello".encode("utf-16"))
        assert source.text() == "hello"

    def test_coerce_rejects_nonsense(self):
        with pytest.raises(TypeError):
            Source.coerce(42)

    def test_head_limits_bytes(self):
        assert Source.from_bytes(b"0123456789").head(4) == b"0123"

    def test_repr_is_informative(self):
        assert "5 bytes" in repr(Source.from_bytes(b"hello"))


class TestExtensibility:
    def test_a_third_party_format_can_be_registered(self):
        from polydoc.formats import Reader, TextWriter, register_reader, register_writer
        from polydoc.formats.registry import _READERS, _WRITERS

        @register_reader
        class UpperReader(Reader):
            format = "upper-test"
            extensions = (".upper",)
            description = "test format"

            def read(self, source, **options):
                document = Document([Paragraph.of(source.text().upper())])
                return self.finalise(document, source)

        @register_writer
        class UpperWriter(TextWriter):
            format = "upper-test"
            extensions = (".upper",)

            def render(self, document, **options):
                return document.text.upper()

        try:
            document = polydoc.loads("hello", "upper-test")
            assert document.text == "HELLO"
            assert polydoc.dumps(document, "upper-test") == b"HELLO"
            assert "upper-test" in polydoc.formats("read")
        finally:
            _READERS.pop("upper-test", None)
            _WRITERS.pop("upper-test", None)

    def test_format_without_a_name_is_rejected(self):
        from polydoc.formats import Reader, register_reader

        with pytest.raises(ValueError, match="non-empty"):

            @register_reader
            class Nameless(Reader):
                def read(self, source, **options):  # pragma: no cover
                    ...


class TestErrorHierarchy:
    def test_everything_derives_from_the_base(self):
        for error in (
            UnsupportedFormatError,
            FormatDetectionError,
            MissingDependencyError,
        ):
            assert issubclass(error, PolydocError)

    def test_missing_dependency_names_the_install_command(self):
        error = MissingDependencyError("python-docx", "Reading DOCX", "docx")
        assert "pip install" in str(error)
        assert "polydoc[docx]" in str(error)

    def test_unsupported_format_lists_alternatives(self):
        error = UnsupportedFormatError("xyz", "read", ["markdown", "txt"])
        assert "markdown" in str(error)
