"""Plain-text reader.

Plain text carries no markup, so structure has to be inferred. The reader splits on
blank lines, then classifies each chunk as a list, a heading, a code block, or a
paragraph. Inference is opt-out via ``detect_structure=False``, which gives you a flat
sequence of paragraphs -- occasionally what you want for machine-generated text.
"""

from __future__ import annotations

from typing import Any, List, Optional

from ...intelligence.heuristics import (
    ListMarker,
    looks_like_code,
    looks_like_heading,
    parse_list_marker,
)
from ...intelligence.structure import build_nested_list
from ...model import Block, CodeBlock, Document, Heading, LineBreak, Paragraph, Text
from ..base import Reader
from ..registry import register_reader
from ..source import Source

__all__ = ["PlainTextReader"]


@register_reader
class PlainTextReader(Reader):
    """Reads ``.txt`` and other unmarked text, inferring structure."""

    format = "txt"
    extensions = (".txt", ".text", ".log", ".rst", ".asc")
    aliases = ("text", "plain")
    mime_types = ("text/plain",)
    description = "Plain text, with heading and list inference"

    def read(self, source: Source, **options: Any) -> Document:
        self.enforce_limits(source, **options)
        encoding: Optional[str] = options.get("encoding")
        detect: bool = options.get("detect_structure", True)
        preserve_breaks: bool = options.get("preserve_line_breaks", False)

        raw = source.text(encoding).replace("\r\n", "\n").replace("\r", "\n")
        document = Document()
        chunks = self._split_chunks(raw)

        for chunk in chunks:
            block = self._classify(chunk, detect=detect, preserve_breaks=preserve_breaks)
            if block is not None:
                document.append(block)

        # A leading standalone heading is almost always the document title.
        if document.body and isinstance(document.body[0], Heading):
            document.metadata.title = document.body[0].text
        elif document.body and source.name:
            document.metadata.title = None

        return self.finalise(document, source)

    # -- internals ------------------------------------------------------------
    @staticmethod
    def _split_chunks(text: str) -> List[List[str]]:
        """Group lines into blank-line-separated chunks."""
        chunks: List[List[str]] = []
        current: List[str] = []
        for line in text.split("\n"):
            if line.strip():
                current.append(line)
            elif current:
                chunks.append(current)
                current = []
        if current:
            chunks.append(current)
        return chunks

    def _classify(
        self,
        lines: List[str],
        detect: bool,
        preserve_breaks: bool,
    ) -> Optional[Block]:
        text = "\n".join(lines)
        if not text.strip():
            return None

        if not detect:
            return self._paragraph(lines, preserve_breaks=True)

        # A chunk where every line carries a list marker is a list.
        markers: List[ListMarker] = []
        for line in lines:
            marker = parse_list_marker(line)
            if marker is None:
                markers = []
                break
            markers.append(marker)
        if markers:
            built = build_nested_list(markers)
            if built is not None:
                return built

        if len(lines) == 1 and looks_like_heading(lines[0]):
            return Heading.of(lines[0].strip(), self._heading_level(lines[0]))

        if len(lines) > 1 and looks_like_code(text):
            # Strip the common leading indent so the code reads naturally.
            indents = [len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()]
            trim = min(indents) if indents else 0
            return CodeBlock("\n".join(line[trim:] for line in lines))

        return self._paragraph(lines, preserve_breaks=preserve_breaks)

    @staticmethod
    def _heading_level(line: str) -> int:
        """Depth from dotted outline numbering: ``2.1.3`` implies level 3."""
        import re

        match = re.match(r"^\s*(\d+(?:\.\d+)*)", line)
        if match:
            return min(6, match.group(1).count(".") + 1)
        return 1

    @staticmethod
    def _paragraph(lines: List[str], preserve_breaks: bool) -> Paragraph:
        if preserve_breaks and len(lines) > 1:
            content: List[Any] = []
            for index, line in enumerate(lines):
                if index:
                    content.append(LineBreak())
                content.append(Text(line.strip()))
            return Paragraph(content)
        return Paragraph.of(" ".join(line.strip() for line in lines))
