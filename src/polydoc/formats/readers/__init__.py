"""Reader implementations.

Importing this package registers every reader. Each module is imported defensively:
a reader whose *own* module fails to import (a broken optional backend, say) must not
take the rest of the library down with it. Missing backends are still reported
properly at read time via :func:`polydoc.formats.base.require`.
"""

from __future__ import annotations

import importlib
import warnings
from typing import List, Tuple

#: (module, format) pairs, in registration order.
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

#: Formats whose reader module could not be imported at all.
unavailable: List[Tuple[str, str]] = []


def _load() -> None:
    for module_name, fmt in _MODULES:
        try:
            importlib.import_module(f".{module_name}", __name__)
        except Exception as exc:  # pragma: no cover - defensive
            unavailable.append((fmt, str(exc)))
            warnings.warn(
                f"polydoc: the {fmt!r} reader is unavailable ({exc})",
                RuntimeWarning,
                stacklevel=2,
            )


_load()
