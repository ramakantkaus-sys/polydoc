"""Resource limits for untrusted input.

Office formats are ZIP archives, and ZIP is trivially weaponisable: a few hundred
kilobytes of highly compressible XML expands to hundreds of megabytes once parsed. A
service that accepts document uploads and parses them without a ceiling can be taken
down by a single small file. Measured on this library before these guards existed, a
297 KB DOCX expanded to 268 MB of text -- a ratio of roughly 900:1.

Two cheap checks close it:

**Declared size.** A ZIP central directory states each entry's uncompressed size, and
Python's :mod:`zipfile` honours that figure when reading, so it is authoritative for how
much data an entry can yield. Summing it costs no decompression at all.

**Ratio.** Total expanded size divided by archive size. A legitimate Office document sits
well under 50:1; a bomb is in the hundreds or thousands.

Both are configurable per call and globally, because "too big" is a deployment decision.
A batch job on trusted files may legitimately want no ceiling at all
(``max_expanded_bytes=0``).
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from typing import Dict, Optional

from ..exceptions import DocumentTooLargeError

__all__ = [
    "ArchiveReport",
    "Limits",
    "check_archive",
    "check_input_size",
    "check_nesting_depth",
    "get_default_limits",
    "set_default_limits",
]

#: Total uncompressed bytes permitted across an archive's entries.
#: 256 MB comfortably fits real documents (a 500-page illustrated report is far smaller)
#: while stopping a bomb well before it exhausts a typical container.
DEFAULT_MAX_EXPANDED_BYTES = 256 * 1024 * 1024

#: Expanded-to-archive ratio permitted. Real Office files are text-heavy XML and
#: routinely reach 20-30:1, so 100:1 leaves generous headroom.
DEFAULT_MAX_COMPRESSION_RATIO = 100.0

#: Absolute cap on a single input document.
DEFAULT_MAX_INPUT_BYTES = 512 * 1024 * 1024

#: Cap on entry count, to bound the cost of walking a pathological archive.
DEFAULT_MAX_ARCHIVE_ENTRIES = 10_000

#: Cap on markup nesting depth. Readers recurse per level, so without a ceiling a
#: pathologically nested document raises a bare ``RecursionError`` from deep inside the
#: parser. Real documents nest a few dozen levels at most.
DEFAULT_MAX_NESTING_DEPTH = 256


@dataclass(frozen=True)
class Limits:
    """Resource ceilings applied when reading. ``0`` disables an individual check."""

    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES
    max_expanded_bytes: int = DEFAULT_MAX_EXPANDED_BYTES
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO
    max_archive_entries: int = DEFAULT_MAX_ARCHIVE_ENTRIES
    max_nesting_depth: int = DEFAULT_MAX_NESTING_DEPTH

    @classmethod
    def unlimited(cls) -> "Limits":
        """No ceilings. Only appropriate for input you produced or already trust."""
        return cls(0, 0, 0.0, 0, 0)

    def with_overrides(self, **options: object) -> "Limits":
        """A copy with any recognised keyword overrides applied.

        Unrecognised keys are ignored so this can be handed a reader's whole
        ``**options`` dict.
        """
        from dataclasses import replace

        known = {
            key: value
            for key, value in options.items()
            if key in ("max_input_bytes", "max_expanded_bytes",
                       "max_compression_ratio", "max_archive_entries",
                       "max_nesting_depth")
            and value is not None
        }
        return replace(self, **known) if known else self  # type: ignore[arg-type]


_DEFAULTS = Limits()


def get_default_limits() -> Limits:
    """The limits applied when a caller does not override them."""
    return _DEFAULTS


def set_default_limits(limits: Limits) -> None:
    """Set the process-wide default limits.

    Intended to be called once at startup::

        polydoc.formats.limits.set_default_limits(
            Limits(max_expanded_bytes=64 * 1024 * 1024)
        )
    """
    global _DEFAULTS
    if not isinstance(limits, Limits):
        raise TypeError("set_default_limits() expects a Limits instance")
    _DEFAULTS = limits


@dataclass(frozen=True)
class ArchiveReport:
    """What an archive declares about itself, gathered without decompressing."""

    entries: int
    compressed_bytes: int
    expanded_bytes: int
    largest_entry: str = ""
    largest_entry_bytes: int = 0

    @property
    def ratio(self) -> float:
        return self.expanded_bytes / self.compressed_bytes if self.compressed_bytes else 0.0


def check_nesting_depth(
    depth: int,
    limits: Optional[Limits] = None,
    label: str = "markup",
) -> None:
    """Reject markup nested beyond :attr:`Limits.max_nesting_depth`.

    Turns what would be a bare ``RecursionError`` from deep inside a parser into a
    :class:`~polydoc.exceptions.DocumentTooLargeError` the caller can actually handle.
    """
    active = limits or _DEFAULTS
    ceiling = active.max_nesting_depth
    if ceiling and depth > ceiling:
        raise DocumentTooLargeError(
            f"This {label} nests more than {ceiling} levels deep, which is far beyond "
            f"any real document and risks exhausting the interpreter stack. Raise the "
            f"ceiling with max_nesting_depth=... if the input is genuinely this deep."
        )


def check_input_size(size: int, limits: Optional[Limits] = None, label: str = "document") -> None:
    """Reject an input larger than :attr:`Limits.max_input_bytes`."""
    active = limits or _DEFAULTS
    ceiling = active.max_input_bytes
    if ceiling and size > ceiling:
        raise DocumentTooLargeError(
            f"This {label} is {_mb(size)}, above the {_mb(ceiling)} limit. "
            f"Raise it with max_input_bytes=..., or pass "
            f"max_input_bytes=0 to disable the check for trusted input."
        )


def check_archive(data: bytes, limits: Optional[Limits] = None) -> ArchiveReport:
    """Inspect a ZIP-based document and reject it if it declares too much data.

    Returns the report so callers can log it. Raises
    :class:`~polydoc.exceptions.DocumentTooLargeError` when a ceiling is exceeded, and
    leaves malformed archives alone -- the format reader will report those with a better
    message than this function could.

    >>> import io, zipfile
    >>> buffer = io.BytesIO()
    >>> with zipfile.ZipFile(buffer, "w") as archive:
    ...     archive.writestr("a.xml", "<x/>")
    >>> check_archive(buffer.getvalue()).entries
    1
    """
    active = limits or _DEFAULTS
    try:
        with zipfile.ZipFile(BytesIOLike(data)) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, OSError, EOFError):
        # Not a readable archive; let the reader produce the diagnostic.
        return ArchiveReport(0, len(data), 0)

    if active.max_archive_entries and len(infos) > active.max_archive_entries:
        raise DocumentTooLargeError(
            f"This archive declares {len(infos):,} entries, above the "
            f"{active.max_archive_entries:,} limit. Raise it with max_archive_entries=..."
        )

    expanded = 0
    largest_name = ""
    largest_size = 0
    for info in infos:
        size = int(getattr(info, "file_size", 0) or 0)
        expanded += size
        if size > largest_size:
            largest_size, largest_name = size, info.filename

    report = ArchiveReport(
        entries=len(infos),
        compressed_bytes=len(data),
        expanded_bytes=expanded,
        largest_entry=largest_name,
        largest_entry_bytes=largest_size,
    )

    if active.max_expanded_bytes and expanded > active.max_expanded_bytes:
        raise DocumentTooLargeError(
            f"This {_mb(len(data))} archive expands to {_mb(expanded)}, above the "
            f"{_mb(active.max_expanded_bytes)} limit "
            f"(largest entry {largest_name!r} at {_mb(largest_size)}). "
            f"This is the shape of a decompression bomb. If the file is genuinely this "
            f"large, raise the ceiling with max_expanded_bytes=..."
        )

    if (
        active.max_compression_ratio
        and report.ratio > active.max_compression_ratio
        # Ignore the ratio for small archives, where it is noisy and harmless.
        and expanded > 8 * 1024 * 1024
    ):
        raise DocumentTooLargeError(
            f"This archive expands {report.ratio:,.0f}:1 "
            f"({_mb(len(data))} to {_mb(expanded)}), above the "
            f"{active.max_compression_ratio:,.0f}:1 limit. Real documents rarely exceed "
            f"50:1, so this is very likely a decompression bomb. Override with "
            f"max_compression_ratio=..."
        )

    return report


def _mb(size: int) -> str:
    """Human-readable byte count."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.2f} GB"


def BytesIOLike(data: bytes):  # noqa: N802 - factory, named for the type it yields
    """A seekable stream over ``data``, which :mod:`zipfile` requires."""
    from io import BytesIO

    return BytesIO(data)
