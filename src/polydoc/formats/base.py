"""Reader and Writer contracts.

Two deliberately small interfaces:

* :class:`Reader` turns a :class:`~polydoc.formats.source.Source` into a
  :class:`~polydoc.model.Document`.
* :class:`Writer` serialises a ``Document`` into a binary stream.

Making :meth:`Writer.write` stream-based (rather than path-based) means
``save()`` and ``dumps()`` share one code path, and callers can write straight into
an HTTP response or a zip entry. :class:`TextWriter` exists because half the formats
are text: subclasses implement :meth:`TextWriter.render` and get encoding for free.

:func:`require` centralises optional-backend handling so every format fails the same
way -- with the exact ``pip install`` line needed.
"""

from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from typing import Any, BinaryIO, ClassVar, Dict, Optional, Sequence, Tuple

from ..exceptions import MissingDependencyError
from ..model import Document
from .source import Source

__all__ = ["Reader", "TextWriter", "Writer", "require"]

_MODULE_CACHE: Dict[str, Any] = {}


def require(module: str, purpose: str, extra: Optional[str] = None, package: Optional[str] = None):
    """Import an optional backend or raise an actionable error.

    >>> require("json", "testing")  # doctest: +ELLIPSIS
    <module 'json'...>
    """
    cached = _MODULE_CACHE.get(module)
    if cached is not None:
        return cached
    try:
        imported = importlib.import_module(module)
    except ImportError as exc:
        raise MissingDependencyError(package or module, purpose, extra) from exc
    _MODULE_CACHE[module] = imported
    return imported


class _FormatBase(ABC):
    """Shared registration metadata for readers and writers."""

    #: Canonical format name, e.g. ``"docx"``. Required.
    format: ClassVar[str] = ""
    #: File extensions (with dots) that imply this format.
    extensions: ClassVar[Tuple[str, ...]] = ()
    #: Alternative names accepted by the API and CLI.
    aliases: ClassVar[Tuple[str, ...]] = ()
    #: MIME types, used for content negotiation and detection.
    mime_types: ClassVar[Tuple[str, ...]] = ()
    #: Optional-dependency extra that enables this format, e.g. ``"pdf"``.
    extra: ClassVar[Optional[str]] = None
    #: One-line description shown by ``polydoc formats``.
    description: ClassVar[str] = ""

    @classmethod
    def names(cls) -> Tuple[str, ...]:
        """The canonical name plus every alias."""
        return (cls.format, *cls.aliases)

    @property
    def default_extension(self) -> str:
        return self.extensions[0] if self.extensions else ""

    @property
    def mime_type(self) -> str:
        return self.mime_types[0] if self.mime_types else "application/octet-stream"

    def __repr__(self) -> str:
        return f"{type(self).__name__}(format={self.format!r})"


class Reader(_FormatBase):
    """Parses a source into a :class:`~polydoc.model.Document`."""

    #: True for ZIP-based formats, which need decompression-bomb screening.
    archive_based: ClassVar[bool] = False

    @abstractmethod
    def read(self, source: Source, **options: Any) -> Document:
        """Parse ``source`` and return a document.

        Implementations should set ``document.source_format`` and ``source_path``,
        must not mutate the source, and should call :meth:`enforce_limits` first.
        """

    def enforce_limits(self, source: Source, **options: Any) -> None:
        """Apply resource ceilings before any parsing work begins.

        Called at the top of :meth:`read`. Screening happens here, before a backend is
        handed the data, because by the time a parser has expanded a bomb the memory is
        already gone. For archives the check reads only the ZIP central directory, so it
        costs no decompression.
        """
        from .limits import check_archive, check_input_size, get_default_limits

        limits = options.get("limits") or get_default_limits()
        limits = limits.with_overrides(**options)

        check_input_size(len(source), limits, label=f"{self.format} document")
        if self.archive_based:
            check_archive(source.bytes, limits)

    def finalise(self, document: Document, source: Source) -> Document:
        """Stamp provenance and repair parent links. Call at the end of :meth:`read`."""
        document.source_format = self.format
        if source.path is not None:
            document.source_path = str(source.path)
        elif source.name:
            document.source_path = source.name
        document.reparent()
        return document


class Writer(_FormatBase):
    """Serialises a :class:`~polydoc.model.Document` to a binary stream."""

    #: False for formats whose natural output is text (markdown, html, csv...).
    binary: ClassVar[bool] = True

    @abstractmethod
    def write(self, document: Document, stream: BinaryIO, **options: Any) -> None:
        """Write ``document`` into ``stream``."""

    def dumps(self, document: Document, **options: Any) -> bytes:
        """Convenience wrapper returning bytes."""
        from io import BytesIO

        buffer = BytesIO()
        self.write(document, buffer, **options)
        return buffer.getvalue()


class TextWriter(Writer):
    """Base for text formats. Subclasses implement :meth:`render`."""

    binary: ClassVar[bool] = False
    #: Default output encoding.
    encoding: ClassVar[str] = "utf-8"

    @abstractmethod
    def render(self, document: Document, **options: Any) -> str:
        """Return the document as a string."""

    def write(self, document: Document, stream: BinaryIO, **options: Any) -> None:
        encoding = options.pop("encoding", self.encoding)
        text = self.render(document, **options)
        stream.write(text.encode(encoding, errors="replace"))


def unwrap_pages(document: Document) -> Sequence[Any]:
    """Flatten :class:`~polydoc.model.Page` / :class:`~polydoc.model.Slide` wrappers.

    Flowing formats (Markdown, HTML, DOCX) do not care that a PDF had pages, and
    should render the blocks inside rather than the wrappers. Page boundaries survive
    as :class:`~polydoc.model.PageBreak` between them.
    """
    from ..model import Page, PageBreak, Slide

    out: list = []
    for index, block in enumerate(document.body):
        if isinstance(block, (Page, Slide)):
            if index:
                out.append(PageBreak())
            out.extend(block.content)
        else:
            out.append(block)
    return out
