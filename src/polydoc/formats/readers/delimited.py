"""Delimited-text reader (CSV, TSV).

The delimiter is sniffed by default, because "CSV" files in the wild are frequently
semicolon- or tab-separated. Header detection is also sniffed but can be forced with
``has_header=``.
"""

from __future__ import annotations

import csv
import io
from typing import Any, List, Optional

from ...exceptions import ParseError
from ...model import Document, Table, TableRow
from ..base import Reader
from ..registry import register_reader
from ..source import Source

__all__ = ["CSVReader"]

#: csv chokes on very long single fields otherwise.
_FIELD_LIMIT = 1024 * 1024


@register_reader
class CSVReader(Reader):
    """Reads CSV/TSV into a single-table document."""

    format = "csv"
    extensions = (".csv", ".tsv", ".tab", ".psv")
    mime_types = ("text/csv", "text/tab-separated-values")
    description = "Delimited text with delimiter and header sniffing"

    def read(self, source: Source, **options: Any) -> Document:
        self.enforce_limits(source, **options)
        text = source.text(options.get("encoding"))
        if not text.strip():
            return self.finalise(Document(), source)

        delimiter: Optional[str] = options.get("delimiter")
        has_header: Optional[bool] = options.get("has_header")
        sample = text[:8192]

        if delimiter is None:
            delimiter = self._sniff_delimiter(sample, source.suffix)
        if has_header is None:
            has_header = self._sniff_header(sample, delimiter)

        previous_limit = csv.field_size_limit()
        try:
            csv.field_size_limit(_FIELD_LIMIT)
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)
            raw_rows: List[List[str]] = [row for row in reader]
        except csv.Error as exc:
            raise ParseError(f"Malformed delimited text: {exc}") from exc
        finally:
            csv.field_size_limit(previous_limit)

        # Drop trailing blank lines but keep interior blanks (they may be meaningful).
        while raw_rows and not any(cell.strip() for cell in raw_rows[-1]):
            raw_rows.pop()
        if not raw_rows:
            return self.finalise(Document(), source)

        # Pad short rows so the table is rectangular.
        width = max(len(row) for row in raw_rows)
        padded = [row + [""] * (width - len(row)) for row in raw_rows]

        rows = [TableRow.of(row) for row in padded]
        if has_header and rows:
            rows[0].is_header = True
        table = Table(rows, header_rows=1 if has_header and rows else 0)

        document = Document(body=[table])
        document.attrs["delimiter"] = delimiter
        return self.finalise(document, source)

    # -- internals ------------------------------------------------------------
    @staticmethod
    def _sniff_delimiter(sample: str, suffix: str) -> str:
        by_extension = {".tsv": "\t", ".tab": "\t", ".psv": "|"}
        if suffix in by_extension:
            return by_extension[suffix]
        try:
            return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            # Sniffer gives up on single-column files; fall back to frequency.
            first_line = sample.splitlines()[0] if sample.splitlines() else ""
            counts = {d: first_line.count(d) for d in ",;\t|"}
            best = max(counts, key=lambda d: counts[d])
            return best if counts[best] else ","

    @staticmethod
    def _sniff_header(sample: str, delimiter: str) -> bool:
        try:
            return csv.Sniffer().has_header(sample)
        except csv.Error:
            # Heuristic fallback: a header row is all non-numeric.
            lines = sample.splitlines()
            if len(lines) < 2:
                return False
            first = lines[0].split(delimiter)
            if not first:
                return False

            def numeric(value: str) -> bool:
                try:
                    float(value.strip().strip('"'))
                    return True
                except ValueError:
                    return False

            return not any(numeric(cell) for cell in first if cell.strip())
