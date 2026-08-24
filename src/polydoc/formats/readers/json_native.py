"""JSON reader.

Handles three shapes, in order:

1. **polydoc-native** -- output of :meth:`Document.to_dict`. Restores the document
   losslessly, which is what makes JSON useful as an interchange and cache format.
2. **A list of flat records** -- becomes a :class:`~polydoc.model.Table`, with the
   union of keys as the header. This is the common "export from an API" case.
3. **Anything else** -- preserved verbatim in a JSON :class:`~polydoc.model.CodeBlock`
   so no data is lost.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence

from ...exceptions import ParseError
from ...model import CodeBlock, Document, Heading, Paragraph, Table
from ..base import Reader
from ..registry import register_reader
from ..source import Source

__all__ = ["JSONReader"]


@register_reader
class JSONReader(Reader):
    """Reads polydoc's native JSON, plus tabular and arbitrary JSON."""

    format = "json"
    extensions = (".json", ".pdjson")
    mime_types = ("application/json",)
    description = "polydoc native JSON (lossless), tabular JSON, or raw JSON"

    def read(self, source: Source, **options: Any) -> Document:
        self.enforce_limits(source, **options)
        text = source.text(options.get("encoding"))
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise ParseError(f"Invalid JSON: {exc}") from exc

        if self._is_native(payload):
            document = Document.from_dict(payload)
            return self.finalise(document, source)

        document = Document()
        records = self._as_records(payload)
        if records is not None:
            document.append(Table.from_rows(self._records_to_rows(records), header=True))
        else:
            document.append(
                CodeBlock(json.dumps(payload, indent=2, ensure_ascii=False), "json")
            )
        return self.finalise(document, source)

    # -- internals ------------------------------------------------------------
    @staticmethod
    def _is_native(payload: Any) -> bool:
        return isinstance(payload, dict) and payload.get("type") == "document"

    @staticmethod
    def _as_records(payload: Any) -> Any:
        """Return a list of flat dicts if the payload is tabular, else ``None``."""
        candidate = payload
        if isinstance(payload, dict):
            # Unwrap the common ``{"data": [...]}`` / ``{"items": [...]}`` envelope.
            for key in ("data", "items", "results", "records", "rows"):
                if isinstance(payload.get(key), list):
                    candidate = payload[key]
                    break
            else:
                return None
        if not isinstance(candidate, list) or not candidate:
            return None
        if not all(isinstance(row, dict) for row in candidate):
            return None
        # Nested structures do not flatten cleanly into a table.
        for row in candidate:
            if any(isinstance(v, (dict, list)) for v in row.values()):
                return None
        return candidate

    @staticmethod
    def _records_to_rows(records: Sequence[Dict[str, Any]]) -> List[List[Any]]:
        columns: List[str] = []
        for row in records:
            for key in row:
                if key not in columns:
                    columns.append(key)
        rows: List[List[Any]] = [columns]
        for record in records:
            rows.append([record.get(column, "") for column in columns])
        return rows
