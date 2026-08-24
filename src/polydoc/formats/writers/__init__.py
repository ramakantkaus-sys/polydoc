"""Writer implementations.

Importing this package registers every writer. See
:mod:`polydoc.formats.readers` for the rationale behind the defensive imports.
"""

from __future__ import annotations

import importlib
import warnings
from typing import List, Tuple

_MODULES: Tuple[Tuple[str, str], ...] = (
    ("plaintext", "txt"),
    ("markdown", "markdown"),
    ("html", "html"),
    ("json_native", "json"),
    ("delimited", "csv"),
    ("docx", "docx"),
    ("pptx", "pptx"),
    ("xlsx", "xlsx"),
    ("pdf", "pdf"),
)

#: Formats whose writer module could not be imported at all.
unavailable: List[Tuple[str, str]] = []


def _load() -> None:
    for module_name, fmt in _MODULES:
        try:
            importlib.import_module(f".{module_name}", __name__)
        except Exception as exc:  # pragma: no cover - defensive
            unavailable.append((fmt, str(exc)))
            warnings.warn(
                f"polydoc: the {fmt!r} writer is unavailable ({exc})",
                RuntimeWarning,
                stacklevel=2,
            )


_load()
