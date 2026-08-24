"""Format registry and detection.

Detection runs in order of reliability:

1. An explicit ``format=`` argument (always wins).
2. Magic bytes. For ZIP-based Office formats this means looking *inside* the archive,
   since ``.docx``, ``.pptx``, and ``.xlsx`` share the same ``PK`` header.
3. The file extension.
4. Text heuristics -- JSON, HTML, and Markdown all look like plain text otherwise.

Content beats extension deliberately: a mislabelled ``report.txt`` that is really a
PDF should still open, and in practice mislabelled files are common.
"""

from __future__ import annotations

import re
import zipfile
from typing import Dict, Iterable, List, Optional, Tuple, Type, TypeVar

from ..exceptions import FormatDetectionError, UnsupportedFormatError
from .base import Reader, Writer
from .source import Source, SourceLike

__all__ = [
    "detect_format",
    "get_reader",
    "get_writer",
    "list_formats",
    "readable_formats",
    "register_reader",
    "register_writer",
    "resolve_format",
    "writable_formats",
]

R = TypeVar("R", bound=Reader)
W = TypeVar("W", bound=Writer)

#: format name -> reader instance
_READERS: Dict[str, Reader] = {}
#: format name -> writer instance
_WRITERS: Dict[str, Writer] = {}
#: alias (and canonical name) -> canonical name
_ALIASES: Dict[str, str] = {}
#: ".ext" -> canonical format name
_EXTENSIONS: Dict[str, str] = {}

#: Extra spellings users reach for that no backend claims directly.
_EXTRA_ALIASES = {
    "md": "markdown",
    "mdown": "markdown",
    "htm": "html",
    "xhtml": "html",
    "text": "txt",
    "plain": "txt",
    "word": "docx",
    "powerpoint": "pptx",
    "slides": "pptx",
    "excel": "xlsx",
    "spreadsheet": "xlsx",
    "sheet": "xlsx",
}

#: Legacy binary formats we can identify but not parse; better than a vague failure.
_OLE_FORMATS = "doc/xls/ppt (pre-2007 Microsoft Office)"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def _index(cls: type) -> None:
    """Record a format class's names and extensions in the lookup tables."""
    canonical = cls.format  # type: ignore[attr-defined]
    for alias in cls.names():  # type: ignore[attr-defined]
        _ALIASES[alias.lower()] = canonical
    for ext in cls.extensions:  # type: ignore[attr-defined]
        _EXTENSIONS.setdefault(ext.lower(), canonical)


def register_reader(cls: Type[R]) -> Type[R]:
    """Class decorator registering a :class:`~polydoc.formats.base.Reader`."""
    if not cls.format:
        raise ValueError(f"{cls.__name__} must define a non-empty `format`")
    _READERS[cls.format] = cls()
    _index(cls)
    return cls


def register_writer(cls: Type[W]) -> Type[W]:
    """Class decorator registering a :class:`~polydoc.formats.base.Writer`."""
    if not cls.format:
        raise ValueError(f"{cls.__name__} must define a non-empty `format`")
    _WRITERS[cls.format] = cls()
    _index(cls)
    return cls


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def resolve_format(name: str) -> str:
    """Normalise a format name or alias to its canonical form.

    >>> resolve_format("MD")
    'markdown'
    >>> resolve_format(".docx")
    'docx'
    """
    key = str(name).strip().lower().lstrip(".")
    if key in _ALIASES:
        return _ALIASES[key]
    if key in _EXTRA_ALIASES:
        return _EXTRA_ALIASES[key]
    ext_key = f".{key}"
    if ext_key in _EXTENSIONS:
        return _EXTENSIONS[ext_key]
    return key


def get_reader(name: str) -> Reader:
    """The reader for a format name or alias."""
    canonical = resolve_format(name)
    reader = _READERS.get(canonical)
    if reader is None:
        raise UnsupportedFormatError(name, "read", readable_formats())
    return reader


def get_writer(name: str) -> Writer:
    """The writer for a format name or alias."""
    canonical = resolve_format(name)
    writer = _WRITERS.get(canonical)
    if writer is None:
        raise UnsupportedFormatError(name, "writ", writable_formats())
    return writer


def readable_formats() -> List[str]:
    """Canonical names of every format that can be read."""
    return sorted(_READERS)


def writable_formats() -> List[str]:
    """Canonical names of every format that can be written."""
    return sorted(_WRITERS)


def list_formats() -> List[Dict[str, object]]:
    """A table describing every registered format, for docs and the CLI."""
    names = sorted(set(_READERS) | set(_WRITERS))
    rows: List[Dict[str, object]] = []
    for name in names:
        handler = _READERS.get(name) or _WRITERS[name]
        rows.append(
            {
                "format": name,
                "read": name in _READERS,
                "write": name in _WRITERS,
                "extensions": list(handler.extensions),
                "aliases": list(handler.aliases),
                "description": handler.description,
            }
        )
    return rows


def extension_for(name: str) -> str:
    """The canonical file extension for a format, e.g. ``".docx"``."""
    canonical = resolve_format(name)
    handler = _WRITERS.get(canonical) or _READERS.get(canonical)
    return handler.default_extension if handler else ""


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_ZIP_SIGNATURES: Tuple[Tuple[str, str], ...] = (
    ("word/document.xml", "docx"),
    ("ppt/presentation.xml", "pptx"),
    ("xl/workbook.xml", "xlsx"),
    ("xl/workbook.bin", "xlsx"),
    ("content.opf", "epub"),
    ("content.xml", "odt"),
)

_HTML_RE = re.compile(rb"<\s*(!doctype\s+html|html|head|body|div|p|table|h[1-6])[\s>/]", re.I)
_MARKDOWN_RE = re.compile(
    rb"(^|\n)\s{0,3}(#{1,6}\s|[-*+]\s|\d+\.\s|>\s|```|\|.*\|)", re.M
)


def _sniff_zip(data: bytes) -> Optional[str]:
    """Identify a ZIP-based format by inspecting its entry names."""
    from io import BytesIO

    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
            # EPUB is identified by its uncompressed mimetype entry.
            if "mimetype" in names:
                try:
                    if b"epub" in archive.read("mimetype"):
                        return "epub"
                except KeyError:  # pragma: no cover
                    pass
            for entry, fmt in _ZIP_SIGNATURES:
                if entry in names or any(n.endswith(entry) for n in names):
                    return fmt
    except (zipfile.BadZipFile, OSError):
        return None
    return None


def sniff_bytes(data: bytes) -> Optional[str]:
    """Guess a format from raw content alone, or return ``None``.

    Magic-number checks only need the first bytes, but ZIP identification needs the
    whole archive, so pass the complete content when you have it.

    >>> sniff_bytes(b"%PDF-1.7\\n")
    'pdf'
    """
    if not data:
        return None
    head = data[:1024]

    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return _sniff_zip(data)
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "_ole"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head[:8] in (b"\x89PNG\r\n\x1a\n",) or head.startswith(b"\xff\xd8\xff"):
        return "_image"

    stripped = head.lstrip()
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<"):
        return "html" if _HTML_RE.search(head) else "xml"
    if stripped[:1] in (b"{", b"["):
        # Could be JSON; confirm by parsing the whole payload.
        import json

        try:
            parsed = json.loads(data.decode("utf-8", errors="strict"))
        except (ValueError, UnicodeDecodeError):
            return None
        # A polydoc-native payload announces itself.
        if isinstance(parsed, dict) and parsed.get("type") == "document":
            return "json"
        return "json"
    return None


def _sniff_text(data: bytes) -> str:
    """Last-resort classification of plain text as markdown or txt."""
    return "markdown" if _MARKDOWN_RE.search(data[:4096]) else "txt"


def detect_format(
    source: SourceLike,
    hint: Optional[str] = None,
    direction: str = "read",
) -> str:
    """Determine the format of ``source``.

    ``hint`` short-circuits everything else, which is how callers force a format for
    ambiguous or mislabelled input.
    """
    if hint:
        return resolve_format(hint)

    src = Source.coerce(source)
    available = _READERS if direction == "read" else _WRITERS

    # The full content, not just a header: a ZIP's central directory lives at the
    # *end* of the file, and that is the only way to tell .docx from .xlsx from .pptx.
    # Source caches the bytes, so the reader that follows does not re-read them.
    sniffed = sniff_bytes(src.bytes)
    if sniffed == "_ole":
        raise FormatDetectionError(
            f"This file is a legacy {_OLE_FORMATS} document. polydoc reads the modern "
            "XML-based formats; convert it to .docx/.xlsx/.pptx first."
        )
    if sniffed == "_image":
        raise FormatDetectionError(
            "This looks like a bare image file, not a document. Wrap it in a document "
            "or use polydoc.model.Image directly."
        )
    if sniffed and sniffed in available:
        return sniffed

    suffix = src.suffix
    if suffix:
        by_ext = _EXTENSIONS.get(suffix)
        if by_ext and by_ext in available:
            return by_ext
        alias = _EXTRA_ALIASES.get(suffix.lstrip("."))
        if alias and alias in available:
            return alias

    # Nothing conclusive; if it decodes as text, choose between markdown and txt.
    data = src.bytes
    if not data:
        raise FormatDetectionError("Cannot detect the format of an empty document")
    try:
        data[:4096].decode("utf-8")
    except UnicodeDecodeError:
        raise FormatDetectionError(
            f"Could not determine the format of {src!r}. Pass format=... explicitly. "
            f"Known formats: {', '.join(sorted(available))}."
        ) from None
    guess = _sniff_text(data)
    if guess in available:
        return guess
    raise FormatDetectionError(
        f"Could not determine the format of {src!r}. Pass format=... explicitly."
    )


def describe_detection(source: SourceLike) -> Dict[str, object]:
    """Explain how a source would be classified. Aimed at debugging and the CLI."""
    src = Source.coerce(source)
    sniffed = sniff_bytes(src.bytes)
    return {
        "name": src.name,
        "size": len(src),
        "suffix": src.suffix,
        "magic": sniffed,
        "by_extension": _EXTENSIONS.get(src.suffix),
        "resolved": detect_format(src),
    }


def iter_readers() -> Iterable[Reader]:
    return _READERS.values()


def iter_writers() -> Iterable[Writer]:
    return _WRITERS.values()
