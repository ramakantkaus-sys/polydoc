"""Exception hierarchy for polydoc.

Every error raised by the library derives from :class:`PolydocError`, so callers
can guard an entire pipeline with a single ``except``.
"""

from __future__ import annotations

from typing import Iterable, Optional


class PolydocError(Exception):
    """Base class for all polydoc errors."""


class UnsupportedFormatError(PolydocError):
    """Raised when no reader/writer is registered for a format."""

    def __init__(
        self,
        fmt: str,
        direction: str = "read",
        available: Optional[Iterable[str]] = None,
    ) -> None:
        self.format = fmt
        self.direction = direction
        self.available = sorted(available) if available else []
        msg = f"No {direction}er registered for format {fmt!r}."
        if self.available:
            msg += f" Available: {', '.join(self.available)}."
        super().__init__(msg)


class FormatDetectionError(PolydocError):
    """Raised when the format of a source cannot be determined."""


class MissingDependencyError(PolydocError):
    """Raised when an optional backend package is required but not installed."""

    def __init__(self, package: str, purpose: str, extra: Optional[str] = None) -> None:
        self.package = package
        self.purpose = purpose
        self.extra = extra
        hint = f"pip install {package}"
        if extra:
            hint = f"pip install 'polydoc[{extra}]'  (or: {hint})"
        super().__init__(f"{purpose} requires the {package!r} package. Install it with: {hint}")


class ParseError(PolydocError):
    """Raised when a source document is malformed and cannot be parsed."""


class DocumentTooLargeError(PolydocError):
    """Raised when input exceeds a configured resource ceiling.

    Most often a decompression bomb: a small ZIP-based document whose declared
    uncompressed size is enormous. See :mod:`polydoc.formats.limits`.
    """


class WriteError(PolydocError):
    """Raised when a document cannot be serialised to the target format."""


class SelectorError(PolydocError):
    """Raised when an element selector expression is invalid."""


class EditError(PolydocError):
    """Raised when an editing operation cannot be applied."""
