"""JSON writer -- polydoc's lossless interchange format.

Everything the model holds is serialised, so ``read -> write -> read`` returns an
equal document. That property makes JSON the right choice for caching an expensive
PDF parse, or for shipping a document between processes.

``mode="text"`` gives a compact text-only projection instead, for indexing and
retrieval pipelines that do not care about styling.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from ...model import Block, Document, Heading, Table
from ..base import TextWriter
from ..registry import register_writer

__all__ = ["JSONWriter"]


@register_writer
class JSONWriter(TextWriter):
    """Serialises the full document model to JSON."""

    format = "json"
    extensions = (".json", ".pdjson")
    mime_types = ("application/json",)
    description = "polydoc native JSON (lossless round trip)"

    def render(self, document: Document, **options: Any) -> str:
        mode = options.get("mode", "full")
        indent = options.get("indent", 2)
        ensure_ascii = options.get("ensure_ascii", False)

        if mode == "text":
            payload: Any = self._text_projection(document)
        elif mode == "outline":
            payload = self._outline_projection(document)
        else:
            payload = document.to_dict(include_ids=options.get("include_ids", False))

        return json.dumps(payload, indent=indent, ensure_ascii=ensure_ascii, default=str)

    # -- projections ----------------------------------------------------------
    @staticmethod
    def _text_projection(document: Document) -> Dict[str, Any]:
        """A flat, style-free view: one record per block."""
        items: List[Dict[str, Any]] = []
        for block in document.blocks():
            text = block.text
            if not text.strip():
                continue
            record: Dict[str, Any] = {"type": block.type, "text": text}
            if isinstance(block, Heading):
                record["level"] = block.level
            if isinstance(block, Table):
                record["rows"] = block.to_matrix()
            if block.bbox is not None:
                record["bbox"] = block.bbox.to_dict()
            items.append(record)
        return {
            "metadata": document.metadata.to_dict(),
            "blocks": items,
        }

    @staticmethod
    def _outline_projection(document: Document) -> Dict[str, Any]:
        """Headings only, as a nested tree."""

        def walk(sections: Any) -> List[Dict[str, Any]]:
            return [
                {
                    "title": section.title_text,
                    "level": section.level,
                    "words": len(section.text.split()),
                    "children": walk(section.subsections),
                }
                for section in sections
            ]

        return {
            "metadata": document.metadata.to_dict(),
            "outline": walk(document.outline()),
        }
