"""Delimited-text writer (CSV, TSV).

CSV is flat, so a document has to be projected onto rows. Tables are the obvious
target; when a document has several, they are stacked with a blank separator row by
default (``multi_table="stack"``), or you can select one with ``table_index=``.

A document with no tables falls back to one row per block: ``type, text``. That keeps
``convert("report.pdf", "report.csv")`` useful rather than empty.
"""

from __future__ import annotations

import csv
import io
from typing import Any, BinaryIO, List, Optional, Sequence

from ...exceptions import WriteError
from ...model import Document, Table
from ..base import Writer
from ..registry import register_writer

__all__ = ["CSVWriter"]

_DELIMITERS = {"csv": ",", "tsv": "\t"}


@register_writer
class CSVWriter(Writer):
    """Writes tabular content as delimited text."""

    format = "csv"
    extensions = (".csv", ".tsv", ".tab")
    mime_types = ("text/csv",)
    binary = True
    description = "Delimited text projection of a document's tables"

    def write(self, document: Document, stream: BinaryIO, **options: Any) -> None:
        delimiter: str = options.get("delimiter", ",")
        encoding: str = options.get("encoding", "utf-8")
        table_index: Optional[int] = options.get("table_index")
        multi_table: str = options.get("multi_table", "stack")
        include_header: bool = options.get("include_header", True)
        newline: str = options.get("lineterminator", "\r\n")

        tables = document.tables
        if table_index is not None:
            if not 0 <= table_index < len(tables):
                raise WriteError(
                    f"table_index={table_index} is out of range; document has {len(tables)} tables"
                )
            tables = [tables[table_index]]
        elif multi_table == "first" and tables:
            tables = [tables[0]]

        rows = self._collect_rows(document, tables, include_header)

        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, delimiter=delimiter, lineterminator=newline)
        writer.writerows(rows)
        stream.write(buffer.getvalue().encode(encoding, errors="replace"))

    # -- internals ------------------------------------------------------------
    def _collect_rows(
        self,
        document: Document,
        tables: Sequence[Table],
        include_header: bool,
    ) -> List[List[str]]:
        if tables:
            rows: List[List[str]] = []
            for index, table in enumerate(tables):
                if index:
                    rows.append([])  # blank separator between stacked tables
                matrix = table.to_matrix()
                if not include_header and table.header_rows:
                    matrix = matrix[table.header_rows :]
                rows.extend(self._flatten(matrix))
            return rows

        # No tables: project blocks so the output still carries the content.
        rows = [["type", "text"]] if include_header else []
        for block in document.blocks():
            text = " ".join(block.text.split())
            if text:
                rows.append([block.type, text])
        return rows

    @staticmethod
    def _flatten(matrix: Sequence[Sequence[str]]) -> List[List[str]]:
        """Normalise to a rectangle and collapse newlines inside cells."""
        if not matrix:
            return []
        width = max(len(row) for row in matrix)
        out: List[List[str]] = []
        for row in matrix:
            cleaned = [" ".join(str(cell).split()) for cell in row]
            cleaned += [""] * (width - len(cleaned))
            out.append(cleaned)
        return out
