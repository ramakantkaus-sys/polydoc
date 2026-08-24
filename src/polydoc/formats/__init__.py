"""Format plumbing: sources, the reader/writer contracts, and the registry.

Importing this package loads every reader and writer, which is what populates the
registry. :mod:`polydoc` does this for you.

Adding a format from outside the library is the same work the built-ins do::

    from polydoc.formats import Reader, Source, register_reader

    @register_reader
    class MyReader(Reader):
        format = "myfmt"
        extensions = (".myf",)

        def read(self, source: Source, **options):
            ...
            return self.finalise(document, source)
"""

from __future__ import annotations

from .base import Reader, TextWriter, Writer, require
from .limits import (
    ArchiveReport,
    Limits,
    check_archive,
    check_input_size,
    get_default_limits,
    set_default_limits,
)
from .registry import (
    detect_format,
    describe_detection,
    extension_for,
    get_reader,
    get_writer,
    list_formats,
    readable_formats,
    register_reader,
    register_writer,
    resolve_format,
    sniff_bytes,
    writable_formats,
)
from .source import Source, SourceLike

# Populate the registry. Order matters only for tie-breaking extensions.
from . import readers as _readers  # noqa: E402,F401  (import for side effects)
from . import writers as _writers  # noqa: E402,F401  (import for side effects)

__all__ = [
    "ArchiveReport",
    "Limits",
    "Reader",
    "Source",
    "SourceLike",
    "TextWriter",
    "Writer",
    "check_archive",
    "check_input_size",
    "describe_detection",
    "get_default_limits",
    "set_default_limits",
    "detect_format",
    "extension_for",
    "get_reader",
    "get_writer",
    "list_formats",
    "readable_formats",
    "register_reader",
    "register_writer",
    "require",
    "resolve_format",
    "sniff_bytes",
    "writable_formats",
]
