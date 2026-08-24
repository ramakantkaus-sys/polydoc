"""The public API: :func:`open_document`, :func:`save`, :func:`convert`, and friends.

Everything here is a thin, well-behaved wrapper over the registry. The design goal is
that the simple thing is one line::

    polydoc.convert("report.pdf", "report.docx")

while the powerful thing is still one expression::

    doc = polydoc.open("report.pdf")
    doc.replace_text("FY2024", "FY2025")
    doc.save("report.docx")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from .exceptions import UnsupportedFormatError
from .formats import (
    Source,
    SourceLike,
    detect_format,
    get_reader,
    get_writer,
    list_formats,
    readable_formats,
    resolve_format,
    writable_formats,
)
from .model import Document

__all__ = [
    "convert",
    "convert_bytes",
    "detect",
    "dumps",
    "formats",
    "loads",
    "open",
    "open_document",
    "save",
    "supported_formats",
]

#: A callable applied to the document between reading and writing.
Transform = Callable[[Document], Optional[Document]]

TargetLike = Union[str, Path]


def open_document(
    source: SourceLike,
    format: Optional[str] = None,
    **options: Any,
) -> Document:
    """Read a document from a path, bytes, or file object.

    The format is detected from content and extension unless ``format`` is given.
    Extra keyword arguments are passed to the reader; see each reader's docstring for
    what it accepts.

    >>> import polydoc
    >>> doc = polydoc.open_document(b"# Title\\n\\nBody.", format="markdown")
    >>> doc.headings[0].text
    'Title'
    """
    src = Source.coerce(source)
    fmt = detect_format(src, hint=format, direction="read")
    reader = get_reader(fmt)
    return reader.read(src, **options)


def loads(data: Union[str, bytes], format: str, name: Optional[str] = None, **options: Any) -> Document:
    """Read a document from an in-memory string or bytes.

    Unlike :func:`open_document`, ``format`` is required -- a bare string is content,
    never a path, so there is no extension to infer from.

    >>> loads("a,b\\n1,2", "csv").tables[0].dimensions
    (2, 2)
    """
    src = Source.from_text(data, name=name) if isinstance(data, str) else Source.from_bytes(data, name=name)
    reader = get_reader(format)
    return reader.read(src, **options)


def save(
    document: Document,
    target: TargetLike,
    format: Optional[str] = None,
    **options: Any,
) -> Path:
    """Write ``document`` to ``target`` and return the path written.

    The format comes from the file extension unless given explicitly. Parent
    directories are created as needed.
    """
    path = Path(target).expanduser()
    fmt = format or _format_from_path(path)
    writer = get_writer(fmt)

    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("wb") as handle:
        writer.write(document, handle, **options)
    return path


def dumps(document: Document, format: str, **options: Any) -> bytes:
    """Serialise ``document`` to bytes in ``format``.

    >>> from polydoc.model import Document, Heading
    >>> dumps(Document([Heading.of("Hi")]), "markdown").decode()
    '# Hi\\n'
    """
    return get_writer(format).dumps(document, **options)


def convert(
    source: SourceLike,
    target: TargetLike,
    source_format: Optional[str] = None,
    target_format: Optional[str] = None,
    read_options: Optional[Dict[str, Any]] = None,
    write_options: Optional[Dict[str, Any]] = None,
    transform: Optional[Union[Transform, List[Transform]]] = None,
) -> Path:
    """Convert a document from one format to another in one call.

    ``transform`` runs between reading and writing, which is where an edit belongs::

        polydoc.convert(
            "template.docx",
            "offer.pdf",
            transform=lambda d: d.replace_text("{{name}}", "Ada Lovelace"),
        )

    A transform may mutate the document in place and return ``None``, or return a
    different document to use instead.
    """
    document = open_document(source, format=source_format, **(read_options or {}))

    if transform is not None:
        transforms = transform if isinstance(transform, list) else [transform]
        for step in transforms:
            returned = step(document)
            if isinstance(returned, Document):
                document = returned

    path = Path(target).expanduser()
    fmt = target_format or _format_from_path(path)
    return save(document, path, format=fmt, **(write_options or {}))


def convert_bytes(
    data: Union[str, bytes],
    source_format: str,
    target_format: str,
    read_options: Optional[Dict[str, Any]] = None,
    write_options: Optional[Dict[str, Any]] = None,
    transform: Optional[Transform] = None,
) -> bytes:
    """Convert between formats entirely in memory.

    Useful in web handlers and pipelines where nothing should touch the filesystem.
    """
    document = loads(data, source_format, **(read_options or {}))
    if transform is not None:
        returned = transform(document)
        if isinstance(returned, Document):
            document = returned
    return dumps(document, target_format, **(write_options or {}))


def detect(source: SourceLike) -> str:
    """Return the detected format name of ``source`` without parsing it fully.

    >>> detect(b"%PDF-1.4 ...")
    'pdf'
    """
    return detect_format(source)


def formats(direction: Optional[str] = None) -> List[str]:
    """List supported format names.

    ``direction`` may be ``"read"``, ``"write"``, or ``None`` for the union.
    """
    if direction == "read":
        return readable_formats()
    if direction == "write":
        return writable_formats()
    return sorted(set(readable_formats()) | set(writable_formats()))


def supported_formats() -> List[Dict[str, Any]]:
    """A detailed table of every format, its extensions, and its capabilities."""
    return list_formats()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _format_from_path(path: Path) -> str:
    """Infer a writable format from a filename, with a helpful error if we cannot."""
    suffix = path.suffix.lower()
    if not suffix:
        raise UnsupportedFormatError(
            str(path), "writ", writable_formats()
        )
    fmt = resolve_format(suffix)
    if fmt not in writable_formats():
        raise UnsupportedFormatError(suffix, "writ", writable_formats())
    return fmt


#: ``polydoc.open`` reads naturally at the call site; the builtin is untouched
#: because this only shadows inside the ``polydoc`` namespace.
open = open_document
