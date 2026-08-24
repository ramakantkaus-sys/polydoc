"""Resource limits: decompression bombs and oversized input.

These are security tests. A service that accepts uploaded documents and parses them
without a ceiling can be taken down by a single small file, because ZIP-based Office
formats compress XML at ratios in the hundreds. Measured before the guard existed, a
297 KB DOCX expanded to 268 MB of text.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from conftest import needs_docx, needs_xlsx

import polydoc
from polydoc.exceptions import DocumentTooLargeError, PolydocError
from polydoc.formats.limits import (
    Limits,
    check_archive,
    check_input_size,
    get_default_limits,
    set_default_limits,
)
from polydoc.model import Document, Paragraph, Table


def zip_bytes(entries: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def poison_entry(data: bytes, entry: str, payload: str) -> bytes:
    """Replace one entry of a valid archive, leaving the rest intact."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as original, zipfile.ZipFile(
        out, "w", zipfile.ZIP_DEFLATED
    ) as rebuilt:
        for info in original.infolist():
            content = payload if info.filename == entry else original.read(info.filename)
            rebuilt.writestr(info.filename, content)
    return out.getvalue()


class TestLimitsConfiguration:
    def test_defaults_are_sane(self):
        limits = get_default_limits()
        assert limits.max_expanded_bytes > 0
        assert limits.max_compression_ratio > 1

    def test_unlimited_disables_everything(self):
        limits = Limits.unlimited()
        assert limits.max_expanded_bytes == 0
        assert limits.max_input_bytes == 0
        check_input_size(10**12, limits)  # must not raise

    def test_with_overrides_ignores_unknown_keys(self):
        limits = Limits().with_overrides(max_expanded_bytes=42, encoding="utf-8")
        assert limits.max_expanded_bytes == 42

    def test_with_overrides_returns_self_when_nothing_matches(self):
        base = Limits()
        assert base.with_overrides(encoding="utf-8") is base

    def test_set_default_limits_round_trip(self):
        original = get_default_limits()
        try:
            set_default_limits(Limits(max_expanded_bytes=1234))
            assert get_default_limits().max_expanded_bytes == 1234
        finally:
            set_default_limits(original)

    def test_set_default_limits_type_checked(self):
        with pytest.raises(TypeError):
            set_default_limits({"max_expanded_bytes": 1})  # type: ignore[arg-type]

    def test_exported_from_the_package_root(self):
        assert polydoc.Limits is Limits
        assert callable(polydoc.set_default_limits)


class TestInputSize:
    def test_rejects_oversized(self):
        with pytest.raises(DocumentTooLargeError, match="above the"):
            check_input_size(2000, Limits(max_input_bytes=1000))

    def test_allows_within_limit(self):
        check_input_size(500, Limits(max_input_bytes=1000))

    def test_zero_disables(self):
        check_input_size(10**9, Limits(max_input_bytes=0))

    def test_message_names_the_override(self):
        with pytest.raises(DocumentTooLargeError, match="max_input_bytes"):
            check_input_size(2000, Limits(max_input_bytes=1000))

    def test_enforced_when_reading(self):
        payload = b"x" * 5000
        with pytest.raises(DocumentTooLargeError):
            polydoc.open(payload, format="txt", max_input_bytes=1000)

    def test_reading_succeeds_under_the_limit(self):
        assert polydoc.open(b"hello", format="txt", max_input_bytes=1000)


class TestArchiveInspection:
    def test_reports_declared_sizes(self):
        data = zip_bytes({"a.xml": "<x/>", "b.xml": "y" * 100})
        report = check_archive(data)
        assert report.entries == 2
        assert report.expanded_bytes == len("<x/>") + 100
        assert report.largest_entry == "b.xml"

    def test_ratio_is_computed(self):
        report = check_archive(zip_bytes({"a.txt": "A" * 100_000}))
        assert report.ratio > 1

    def test_malformed_archive_is_left_to_the_reader(self):
        # check_archive must not mask a parse error with a size error.
        report = check_archive(b"not a zip at all")
        assert report.entries == 0

    def test_rejects_oversized_expansion(self):
        data = zip_bytes({"word/document.xml": "A" * (20 * 1024 * 1024)})
        with pytest.raises(DocumentTooLargeError, match="expands to"):
            check_archive(data, Limits(max_expanded_bytes=1024 * 1024))

    def test_rejects_excessive_ratio(self):
        # 40 MB of a single repeated byte compresses to almost nothing.
        data = zip_bytes({"word/document.xml": "A" * (40 * 1024 * 1024)})
        with pytest.raises(DocumentTooLargeError, match="decompression bomb"):
            check_archive(
                data,
                Limits(max_expanded_bytes=0, max_compression_ratio=50.0),
            )

    def test_small_archives_skip_the_ratio_check(self):
        # A tiny highly-compressible file is harmless; flagging it would be noise.
        data = zip_bytes({"a.txt": "A" * 200_000})
        check_archive(data, Limits(max_expanded_bytes=0, max_compression_ratio=2.0))

    def test_nesting_depth_guard(self):
        from polydoc.formats.limits import check_nesting_depth

        check_nesting_depth(10, Limits(max_nesting_depth=100))
        with pytest.raises(DocumentTooLargeError, match="nests more than"):
            check_nesting_depth(200, Limits(max_nesting_depth=100))

    def test_nesting_depth_zero_disables(self):
        from polydoc.formats.limits import check_nesting_depth

        check_nesting_depth(10**6, Limits(max_nesting_depth=0))

    def test_rejects_too_many_entries(self):
        data = zip_bytes({f"f{index}.txt": "x" for index in range(50)})
        with pytest.raises(DocumentTooLargeError, match="entries"):
            check_archive(data, Limits(max_archive_entries=10))

    def test_unlimited_allows_a_bomb_through(self):
        data = zip_bytes({"word/document.xml": "A" * (20 * 1024 * 1024)})
        report = check_archive(data, Limits.unlimited())
        assert report.expanded_bytes == 20 * 1024 * 1024


@needs_docx
class TestDocxBombRejected:
    #: Deliberately under libxml2's 10 MB text-node ceiling.
    #:
    #: lxml on Linux and macOS refuses a single text node larger than that
    #: ("Resource limit exceeded: Text node too long"), while the Windows build
    #: happily parses it. A larger payload here made `test_opt_out_still_possible`
    #: pass on Windows and fail everywhere else. 4 MB is still far above the 1 MB
    #: ceiling these tests set, so the guard is exercised just as well.
    BOMB_BYTES = 4 * 1024 * 1024

    @pytest.fixture
    def bomb(self):
        """A structurally valid DOCX whose document.xml expands well past the limit.

        Structural validity matters: a stub archive is rejected for missing
        ``[Content_Types].xml``, which proves nothing about decompression limits.
        """
        valid = polydoc.dumps(polydoc.loads("# T\n\nBody.\n", "markdown"), "docx")
        payload = (
            '<?xml version="1.0"?><w:document '
            'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>" + ("A" * self.BOMB_BYTES) + "</w:t></w:r></w:p>"
            "</w:body></w:document>"
        )
        return poison_entry(valid, "word/document.xml", payload)

    def test_bomb_is_small_on_disk(self, bomb):
        # A tiny archive that expands to several megabytes: the bomb shape.
        assert len(bomb) < 256 * 1024
        assert self.BOMB_BYTES > 8 * len(bomb)

    def test_bomb_is_rejected(self, bomb):
        with pytest.raises(DocumentTooLargeError):
            polydoc.open(bomb, format="docx", max_expanded_bytes=1024 * 1024)

    def test_rejection_happens_before_parsing(self, bomb):
        """The guard must fire without expanding the entry, so it has to be cheap."""
        import time

        started = time.perf_counter()
        with pytest.raises(DocumentTooLargeError):
            polydoc.open(bomb, format="docx", max_expanded_bytes=1024 * 1024)
        assert time.perf_counter() - started < 2.0

    def test_error_is_actionable(self, bomb):
        with pytest.raises(DocumentTooLargeError, match="max_expanded_bytes"):
            polydoc.open(bomb, format="docx", max_expanded_bytes=1024 * 1024)

    def test_opt_out_still_possible(self, bomb):
        # Trusted input may legitimately be large; the ceiling is a policy, not a wall.
        document = polydoc.open(bomb, format="docx", limits=Limits.unlimited())
        assert len(document.text) > 1_000_000

    def test_real_documents_are_unaffected(self, sample_document, tmp_path):
        path = sample_document.save(tmp_path / "normal.docx")
        assert polydoc.open(path).word_count > 0

    def test_default_limits_reject_a_large_bomb(self):
        valid = polydoc.dumps(polydoc.loads("# T\n", "markdown"), "docx")
        payload = (
            '<?xml version="1.0"?><w:document '
            'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>" + ("A" * (300 * 1024 * 1024)) + "</w:t></w:r></w:p>"
            "</w:body></w:document>"
        )
        bomb = poison_entry(valid, "word/document.xml", payload)
        # No explicit limit: the shipped default must catch this.
        with pytest.raises(DocumentTooLargeError):
            polydoc.open(bomb, format="docx")


@needs_xlsx
class TestXlsxBombRejected:
    def test_bomb_is_rejected(self):
        valid = polydoc.dumps(Document([Table.from_rows([["a"], ["1"]])]), "xlsx")
        with zipfile.ZipFile(io.BytesIO(valid)) as archive:
            target = next(
                name for name in archive.namelist() if name.startswith("xl/worksheets/sheet")
            )
        cells = "".join(
            f'<c r="A{index}" t="inlineStr"><is><t>{"A" * 1024}</t></is></c>'
            for index in range(1, 40_000)
        )
        sheet = (
            '<?xml version="1.0"?><worksheet '
            'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData><row r="1">{cells}</row></sheetData></worksheet>'
        )
        bomb = poison_entry(valid, target, sheet)
        with pytest.raises(DocumentTooLargeError):
            polydoc.open(bomb, format="xlsx", max_expanded_bytes=1024 * 1024)

    def test_normal_workbook_unaffected(self, tmp_path):
        path = Document([Table.from_rows([["a", "b"], ["1", "2"]])]).save(tmp_path / "n.xlsx")
        assert polydoc.open(path).sheets


class TestNestedMarkup:
    """Pathological nesting must give a clear error, not a bare RecursionError."""

    def test_deeply_nested_html_is_rejected_clearly(self):
        markup = "<div>" * 5000 + "x" + "</div>" * 5000
        with pytest.raises(DocumentTooLargeError, match="nests more than"):
            polydoc.loads(markup, "html")

    def test_normal_nesting_is_fine(self):
        markup = "<div>" * 20 + "<p>text</p>" + "</div>" * 20
        assert polydoc.loads(markup, "html").text == "text"

    def test_limit_is_configurable(self):
        markup = "<div>" * 40 + "<p>x</p>" + "</div>" * 40
        with pytest.raises(DocumentTooLargeError):
            polydoc.loads(markup, "html", max_nesting_depth=10)
        assert polydoc.loads(markup, "html", max_nesting_depth=500)

    def test_realistic_documents_are_well_inside_the_default(self, sample_document):
        markup = sample_document.to_text("html")
        assert polydoc.loads(markup, "html").word_count > 0


class TestErrorHierarchy:
    def test_derives_from_the_base_error(self):
        assert issubclass(DocumentTooLargeError, PolydocError)

    def test_catchable_as_polydoc_error(self):
        with pytest.raises(PolydocError):
            check_input_size(2000, Limits(max_input_bytes=1000))
