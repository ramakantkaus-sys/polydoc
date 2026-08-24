"""Editing: find things, change them, keep the formatting.

Most document libraries let you *read* or *build*, not *revise*. This package is the
revision half:

* :mod:`~polydoc.edit.selector` -- CSS-like queries over the document tree.
* :mod:`~polydoc.edit.text` -- text changes that survive run boundaries and preserve
  character formatting.
* :mod:`~polydoc.edit.ops` -- structural changes, plus :class:`~polydoc.edit.ops.Pipeline`
  for composing them.

The methods on :class:`~polydoc.model.Document` (``find``, ``find_all``,
``replace_text``) delegate here, so the short form is usually enough::

    doc = polydoc.open("contract.docx")
    doc.replace_text("{{client}}", "Acme Ltd")
    doc.save("contract-acme.pdf")
"""

from __future__ import annotations

from .ops import (
    Pipeline,
    insert_after,
    insert_before,
    map_blocks,
    merge_adjacent_paragraphs,
    move,
    remove,
    remove_all,
    replace_block,
    restyle,
    shift_heading_levels,
    strip_empty,
    unwrap,
    wrap,
)
from .selector import Selector, compile_selector, matches, select, select_one
from .text import (
    TextMatch,
    find_text,
    iter_inline_containers,
    replace_text,
    set_text,
    style_text,
)

__all__ = [
    "Pipeline",
    "Selector",
    "TextMatch",
    "compile_selector",
    "find_text",
    "insert_after",
    "insert_before",
    "iter_inline_containers",
    "map_blocks",
    "matches",
    "merge_adjacent_paragraphs",
    "move",
    "remove",
    "remove_all",
    "replace_block",
    "replace_text",
    "restyle",
    "select",
    "select_one",
    "set_text",
    "shift_heading_levels",
    "strip_empty",
    "unwrap",
    "wrap",
]
