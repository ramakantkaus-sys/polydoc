"""Input normalisation.

Readers accept paths, bytes, or open file objects. Rather than making every reader
handle all three, :class:`Source` normalises them once. It also caches the raw bytes,
because format detection needs a peek at the header and the reader then needs the
whole thing -- reading a stream twice is not always possible.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, BinaryIO, Optional, Union

from ..exceptions import PolydocError

__all__ = ["Source", "SourceLike"]

#: Anything :meth:`Source.coerce` accepts.
SourceLike = Union["Source", str, bytes, bytearray, os.PathLike, BinaryIO]

#: Tried in order when decoding text of unknown encoding.
_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


class Source:
    """A document input, whatever form it arrived in.

    >>> src = Source.from_bytes(b"# Title", name="notes.md")
    >>> src.suffix
    '.md'
    >>> src.text()
    '# Title'
    """

    __slots__ = ("_bytes", "_path", "_name", "_stream")

    def __init__(
        self,
        *,
        path: Optional[Path] = None,
        data: Optional[bytes] = None,
        stream: Optional[BinaryIO] = None,
        name: Optional[str] = None,
    ) -> None:
        if path is None and data is None and stream is None:
            raise ValueError("A Source needs a path, bytes, or a stream")
        self._path = path
        self._bytes: Optional[bytes] = data
        self._stream = stream
        self._name = name or (path.name if path else None)

    # -- constructors ---------------------------------------------------------
    @classmethod
    def coerce(cls, obj: SourceLike) -> "Source":
        """Build a :class:`Source` from any supported input.

        A ``str`` or ``PathLike`` is treated as a filesystem path. To read a document
        out of an in-memory string, use :func:`polydoc.loads` instead, which is
        explicit about the format.
        """
        if isinstance(obj, Source):
            return obj
        if isinstance(obj, (bytes, bytearray)):
            return cls.from_bytes(bytes(obj))
        if isinstance(obj, (str, os.PathLike)):
            return cls.from_path(obj)
        if hasattr(obj, "read"):
            return cls.from_stream(obj)
        raise TypeError(
            f"Cannot read from {type(obj).__name__}; expected a path, bytes, or file object"
        )

    @classmethod
    def from_path(cls, path: Union[str, os.PathLike]) -> "Source":
        resolved = Path(path).expanduser()
        if not resolved.exists():
            raise FileNotFoundError(f"No such file: {resolved}")
        if resolved.is_dir():
            raise PolydocError(f"Expected a file but got a directory: {resolved}")
        return cls(path=resolved)

    @classmethod
    def from_bytes(cls, data: bytes, name: Optional[str] = None) -> "Source":
        return cls(data=bytes(data), name=name)

    @classmethod
    def from_text(
        cls, text: str, name: Optional[str] = None, encoding: str = "utf-8"
    ) -> "Source":
        return cls(data=text.encode(encoding), name=name)

    @classmethod
    def from_stream(cls, stream: Any, name: Optional[str] = None) -> "Source":
        label = name or getattr(stream, "name", None)
        label = os.path.basename(label) if isinstance(label, str) else None
        return cls(stream=stream, name=label)

    # -- identity -------------------------------------------------------------
    @property
    def path(self) -> Optional[Path]:
        """The filesystem path, when the input came from disk."""
        return self._path

    @property
    def name(self) -> Optional[str]:
        """A filename, when one is known. Used for extension-based detection."""
        return self._name

    @property
    def suffix(self) -> str:
        """Lowercased file extension including the dot, or ``""``."""
        return Path(self._name).suffix.lower() if self._name else ""

    # -- content --------------------------------------------------------------
    @property
    def bytes(self) -> bytes:
        """The full content, read and cached on first access."""
        if self._bytes is None:
            if self._path is not None:
                self._bytes = self._path.read_bytes()
            elif self._stream is not None:
                data = self._stream.read()
                if isinstance(data, str):
                    data = data.encode("utf-8")
                self._bytes = data
            else:  # pragma: no cover - guarded in __init__
                self._bytes = b""
        return self._bytes

    def head(self, size: int = 512) -> bytes:
        """The first ``size`` bytes, for magic-number sniffing."""
        return self.bytes[:size]

    def stream(self) -> BinaryIO:
        """A fresh seekable binary stream over the content.

        Always a new object, so backends that consume a stream cannot interfere
        with one another.
        """
        from io import BytesIO

        return BytesIO(self.bytes)

    def text(self, encoding: Optional[str] = None) -> str:
        """Decode the content as text.

        With no ``encoding``, a BOM is honoured first, then a short list of common
        encodings is tried before falling back to lossy UTF-8.
        """
        data = self.bytes
        if encoding:
            return data.decode(encoding)
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return data.decode("utf-16")
        for candidate in _ENCODINGS:
            try:
                return data.decode(candidate)
            except (UnicodeDecodeError, LookupError):
                continue
        return data.decode("utf-8", errors="replace")

    def open_path(self) -> Union[Path, BinaryIO]:
        """A path when available, else a stream.

        Some backends are markedly faster or more capable given a real path
        (PyMuPDF's incremental parsing, for instance), so prefer it when we have one.
        """
        return self._path if self._path is not None else self.stream()

    def __len__(self) -> int:
        return len(self.bytes)

    def __repr__(self) -> str:
        origin = self._path or self._name or "<memory>"
        return f"Source({str(origin)!r}, {len(self.bytes)} bytes)"
