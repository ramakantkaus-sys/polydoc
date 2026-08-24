"""Structural editing: insert, remove, move, wrap, and compose.

Where :mod:`polydoc.edit.text` changes what a document *says*, this changes its
*shape*. Every operation works through parent links, so a node found by a selector can
be edited without knowing where in the tree it sits::

    from polydoc.edit import insert_after, remove_all, Pipeline

    remove_all(doc, "paragraph:empty")
    insert_after(doc.find("heading[level=1]"), Paragraph.of("Revised edition."))

:class:`Pipeline` bundles a sequence of edits into a reusable callable, which is what
``convert(..., transform=)`` expects.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass, replace as _replace
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Union

from ..exceptions import EditError
from ..model.base import Node
from ..model.blocks import (
    Block,
    BlockContainer,
    Container,
    Heading,
    ListBlock,
    ListItem,
    Paragraph,
    Quote,
    Section,
    Table,
)
from ..model.document import Document
from ..model.inline import Inline, Text, merge_runs
from ..model.style import ParagraphStyle, TextStyle
from .selector import Selector, compile_selector, select

__all__ = [
    "Pipeline",
    "insert_after",
    "insert_before",
    "map_blocks",
    "merge_adjacent_paragraphs",
    "move",
    "remove",
    "remove_all",
    "replace_block",
    "restyle",
    "shift_heading_levels",
    "strip_empty",
    "unwrap",
    "wrap",
]


# ---------------------------------------------------------------------------
# Positional helpers
# ---------------------------------------------------------------------------


def _sibling_list(node: Node) -> "tuple[Node, List[Node], int]":
    """Locate the list a node lives in, returning ``(parent, list, index)``."""
    parent = node.parent
    if parent is None:
        raise EditError(f"{node.type} has no parent, so it has no siblings")
    if not is_dataclass(parent):
        raise EditError(f"{parent.type} cannot hold a list of children")
    for spec in fields(parent):
        value = getattr(parent, spec.name, None)
        if isinstance(value, list):
            for index, item in enumerate(value):
                if item is node:
                    return (parent, value, index)
    raise EditError(f"{node.type} was not found among its parent's children")


def insert_before(node: Node, *blocks: Node) -> Node:
    """Insert ``blocks`` immediately before ``node``. Returns ``node``."""
    parent, siblings, index = _sibling_list(node)
    siblings[index:index] = list(blocks)
    parent.adopt(*blocks)
    return node


def insert_after(node: Node, *blocks: Node) -> Node:
    """Insert ``blocks`` immediately after ``node``. Returns ``node``."""
    parent, siblings, index = _sibling_list(node)
    siblings[index + 1 : index + 1] = list(blocks)
    parent.adopt(*blocks)
    return node


def replace_block(node: Node, *blocks: Node) -> None:
    """Swap ``node`` for ``blocks``, keeping its position."""
    if not blocks:
        remove(node)
        return
    parent, siblings, index = _sibling_list(node)
    siblings[index : index + 1] = list(blocks)
    parent.adopt(*blocks)
    node.parent = None


def remove(node: Node) -> Node:
    """Detach a node from its parent. Returns the detached node."""
    return node.detach()


def remove_all(root: Node, selector: Union[str, Selector]) -> int:
    """Remove every node matching ``selector``. Returns how many were removed.

    Matches are collected before any removal, so mutating the tree cannot disturb the
    traversal.

    >>> from polydoc.model import Document, Paragraph, Heading
    >>> doc = Document([Heading.of("A"), Paragraph.of(""), Paragraph.of("keep")])
    >>> remove_all(doc, "paragraph:empty")
    1
    >>> len(doc.body)
    2
    """
    targets = select(root, selector)
    removed = 0
    for node in targets:
        if node is root:
            continue
        try:
            node.detach()
            removed += 1
        except (EditError, ValueError):  # pragma: no cover - defensive
            continue
    return removed


def move(node: Node, target: Node, position: str = "after") -> Node:
    """Relocate ``node`` relative to ``target``.

    ``position`` is ``"before"``, ``"after"``, ``"start"`` (first child of target) or
    ``"end"`` (last child of target).
    """
    if node is target:
        raise EditError("Cannot move a node relative to itself")
    if any(ancestor is node for ancestor in target.ancestors()):
        raise EditError("Cannot move a node inside its own subtree")

    node.detach()
    if position == "before":
        insert_before(target, node)
    elif position == "after":
        insert_after(target, node)
    elif position in ("start", "end"):
        holder = _content_list(target)
        if position == "start":
            holder.insert(0, node)
        else:
            holder.append(node)
        target.adopt(node)
    else:
        raise EditError(
            f"Unknown position {position!r}; use 'before', 'after', 'start' or 'end'"
        )
    return node


def _content_list(node: Node) -> List[Node]:
    """The list a container keeps its children in."""
    for attribute in ("body", "content", "items", "rows", "cells"):
        value = getattr(node, attribute, None)
        if isinstance(value, list):
            return value
    raise EditError(f"{node.type} cannot contain child blocks")


def wrap(node: Node, container: Optional[BlockContainer] = None, **kwargs: Any) -> Node:
    """Wrap ``node`` in a container block, in place. Returns the container.

    >>> from polydoc.model import Document, Paragraph
    >>> doc = Document([Paragraph.of("quoted")])
    >>> holder = wrap(doc.body[0], role="aside")
    >>> holder.type, holder.role
    ('container', 'aside')
    """
    holder = container if container is not None else Container(**kwargs)
    parent, siblings, index = _sibling_list(node)
    siblings[index] = holder
    _content_list(holder).append(node)
    holder.adopt(node)
    parent.adopt(holder)
    return holder


def unwrap(container: Node) -> List[Node]:
    """Replace a container with its children. Returns the promoted children."""
    children = list(_content_list(container))
    if not children:
        remove(container)
        return []
    parent, siblings, index = _sibling_list(container)
    siblings[index : index + 1] = children
    parent.adopt(*children)
    container.parent = None
    return children


# ---------------------------------------------------------------------------
# Bulk transforms
# ---------------------------------------------------------------------------


def map_blocks(
    root: Node,
    function: Callable[[Node], Any],
    selector: Optional[Union[str, Selector]] = None,
) -> int:
    """Apply ``function`` to matching nodes.

    The return value decides what happens:

    * ``None`` -- keep the node as it is (the function may have mutated it).
    * a :class:`~polydoc.model.base.Node` -- replace the node with it.
    * a list of nodes -- replace the node with all of them.
    * ``False`` -- remove the node.

    Returns the number of nodes the function was applied to.
    """
    targets = select(root, selector) if selector is not None else list(root.walk())
    count = 0
    for node in targets:
        if node is root:
            continue
        result = function(node)
        count += 1
        if result is None:
            continue
        if result is False:
            node.detach()
        elif isinstance(result, Node):
            replace_block(node, result)
        elif isinstance(result, (list, tuple)):
            replace_block(node, *result)
    return count


def restyle(
    root: Node,
    selector: Union[str, Selector],
    text_style: Optional[Dict[str, Any]] = None,
    paragraph_style: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> int:
    """Apply styling to whole blocks matched by ``selector``.

    Text attributes are applied to every run in the block; paragraph attributes to the
    block itself. Attributes are routed automatically by name, so the common case is a
    single flat call::

        restyle(doc, "heading[level=1]", color="#003366", alignment="center")

    Returns the number of blocks restyled.
    """
    text_fields = {spec.name for spec in fields(TextStyle)}
    paragraph_fields = {spec.name for spec in fields(ParagraphStyle)}

    text_updates = dict(text_style or {})
    paragraph_updates = dict(paragraph_style or {})
    for key, value in kwargs.items():
        if key in text_fields:
            text_updates[key] = value
        elif key in paragraph_fields:
            paragraph_updates[key] = value
        else:
            raise EditError(
                f"Unknown style attribute {key!r}. Text: {sorted(text_fields)}; "
                f"paragraph: {sorted(paragraph_fields)}"
            )

    overlay = TextStyle(**text_updates) if text_updates else None
    count = 0
    for node in select(root, selector):
        changed = False
        if paragraph_updates and hasattr(node, "style"):
            current = getattr(node, "style")
            if isinstance(current, ParagraphStyle):
                node.style = _replace(current, **paragraph_updates)  # type: ignore[misc]
                changed = True
        if overlay is not None:
            for descendant in node.walk():
                if isinstance(descendant, Text):
                    descendant.style = descendant.style.merge(overlay)
                    changed = True
        if changed:
            count += 1
    return count


def shift_heading_levels(root: Node, delta: int) -> int:
    """Promote or demote every heading by ``delta``, clamped to 1-6.

    Demoting by one is what you want when nesting a document inside another::

        shift_heading_levels(chapter, +1)

    Returns the number of headings changed.
    """
    count = 0
    for node in root.walk():
        if isinstance(node, Heading):
            new_level = max(1, min(6, node.level + delta))
            if new_level != node.level:
                node.level = new_level
                count += 1
    return count


def strip_empty(root: Node) -> int:
    """Remove blocks that contain no text and no media. Returns the count removed.

    Converted documents accumulate these: an empty Word paragraph used for spacing, a
    table cell with only a stray space. They add noise to every subsequent export.
    """
    from ..model.blocks import HorizontalRule, Image, PageBreak

    keep_types = (HorizontalRule, PageBreak, Image)
    removed = 0
    # Deepest first, so emptying a container makes it eligible in the same pass.
    for node in sorted(root.walk(include_self=False), key=_depth, reverse=True):
        if isinstance(node, keep_types) or node.parent is None:
            continue
        if isinstance(node, (Paragraph, Heading)):
            if node.text.strip():
                continue
            if any(not isinstance(child, Text) for child in node.content):
                continue  # holds an image or a break
        elif isinstance(node, (ListItem, ListBlock, Quote, Section, Container)):
            if node.text.strip() or any(
                isinstance(d, keep_types) for d in node.walk(include_self=False)
            ):
                continue
        else:
            continue
        try:
            node.detach()
            removed += 1
        except (EditError, ValueError):  # pragma: no cover
            continue
    return removed


def _depth(node: Node) -> int:
    return sum(1 for _ in node.ancestors())


def merge_adjacent_paragraphs(root: Node, separator: str = " ") -> int:
    """Join consecutive paragraphs that share styling. Returns merges performed.

    PDF extraction can over-split a paragraph when a line ends unusually. This repairs
    that, but only for neighbours whose block styling agrees, so a centred caption is
    never absorbed into the body text above it.
    """
    merges = 0
    for node in list(root.walk()):
        if not is_dataclass(node):
            continue
        for spec in fields(node):
            value = getattr(node, spec.name, None)
            if not isinstance(value, list) or len(value) < 2:
                continue
            if not any(isinstance(item, Paragraph) for item in value):
                continue

            index = 0
            while index < len(value) - 1:
                current, following = value[index], value[index + 1]
                if (
                    isinstance(current, Paragraph)
                    and isinstance(following, Paragraph)
                    and current.style == following.style
                    and not current.attrs
                    and not following.attrs
                    and current.text.strip()
                    and following.text.strip()
                ):
                    combined: List[Inline] = list(current.content)
                    if separator:
                        combined.append(Text(separator))
                    combined.extend(following.content)
                    current.content = merge_runs(combined)
                    current.adopt(*current.content)
                    if current.bbox is not None and following.bbox is not None:
                        current.bbox = current.bbox.union(following.bbox)
                    value.pop(index + 1)
                    merges += 1
                    continue
                index += 1
    return merges


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class Pipeline:
    """An ordered, reusable sequence of document transforms.

    Each step is a callable taking a document. A step may mutate it and return ``None``,
    or return a replacement document.

    >>> from polydoc.model import Document, Paragraph
    >>> pipe = Pipeline().then(strip_empty).replace("draft", "final")
    >>> doc = pipe(Document([Paragraph.of("a draft"), Paragraph.of("")]))
    >>> doc.text, len(doc.body)
    ('a final', 1)
    """

    __slots__ = ("_steps", "name")

    def __init__(self, *steps: Callable[[Document], Any], name: str = "pipeline") -> None:
        self._steps: List[Callable[[Document], Any]] = list(steps)
        self.name = name

    def then(self, step: Callable[[Document], Any]) -> "Pipeline":
        """Append an arbitrary callable. Returns ``self`` for chaining."""
        self._steps.append(step)
        return self

    def replace(self, pattern: str, replacement: str, **kwargs: Any) -> "Pipeline":
        """Append a formatting-preserving text replacement."""
        from .text import replace_text

        self._steps.append(lambda doc: replace_text(doc, pattern, replacement, **kwargs))
        return self

    def style(self, pattern: str, **style: Any) -> "Pipeline":
        """Append a text restyling step."""
        from .text import style_text

        self._steps.append(lambda doc: style_text(doc, pattern, **style))
        return self

    def remove(self, selector: str) -> "Pipeline":
        """Append a removal by selector."""
        self._steps.append(lambda doc: remove_all(doc, selector))
        return self

    def restyle(self, selector: str, **style: Any) -> "Pipeline":
        """Append a block restyling step."""
        self._steps.append(lambda doc: restyle(doc, selector, **style))
        return self

    def map(self, function: Callable[[Node], Any], selector: Optional[str] = None) -> "Pipeline":
        """Append a per-node transform."""
        self._steps.append(lambda doc: map_blocks(doc, function, selector))
        return self

    def shift_headings(self, delta: int) -> "Pipeline":
        """Append a heading promotion/demotion."""
        self._steps.append(lambda doc: shift_heading_levels(doc, delta))
        return self

    def __call__(self, document: Document) -> Document:
        current = document
        for step in self._steps:
            result = step(current)
            if isinstance(result, Document):
                current = result
        return current

    def __len__(self) -> int:
        return len(self._steps)

    def __repr__(self) -> str:
        return f"Pipeline({self.name!r}, {len(self._steps)} steps)"
