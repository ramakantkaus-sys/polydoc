"""Text editing that preserves character formatting.

This is the module that justifies the library. Consider a Word paragraph reading
"Report for **FY2024** quarter". Word stores that as three runs, and the phrase you want
to replace may straddle them -- so the naive approach (``run.text = run.text.replace(...)``)
silently misses it. That is a well-known footgun with ``python-docx``, and every
workaround people reach for either flattens the paragraph to plain text (destroying the
bold) or only handles matches that happen to sit inside a single run.

The approach here:

1. Flatten a block's :class:`~polydoc.model.Text` leaves into one string, keeping an
   index map back to each leaf and offset.
2. Match against that continuous string, so run boundaries are invisible to the search.
3. Apply matches right to left, so earlier offsets stay valid.
4. Write the whole replacement into the *first* leaf the match touched -- inheriting that
   leaf's style -- and delete the matched characters from the rest.
5. Normalise with :func:`~polydoc.model.merge_runs`, collapsing any runs the edit left
   adjacent and identical.

The result: the replacement inherits the formatting of where the match started, the
surrounding formatting is untouched, and matches spanning any number of runs work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, is_dataclass, replace as _replace
from typing import Any, Callable, Dict, Iterator, List, Optional, Pattern, Sequence, Tuple, Union

from ..exceptions import EditError
from ..model.base import Node
from ..model.blocks import CodeBlock, Image, Slide, Table
from ..model.inline import Inline, Link, Text, iter_text_nodes, merge_runs
from ..model.style import TextStyle
from .selector import Selector, compile_selector

__all__ = [
    "TextMatch",
    "find_text",
    "iter_inline_containers",
    "replace_text",
    "set_text",
    "style_text",
]

#: A replacement is either literal text or a callable given the regex match.
Replacement = Union[str, Callable[[re.Match], str]]

#: Plain-string fields that also carry user-visible text.
_PLAIN_FIELDS: Dict[type, Tuple[str, ...]] = {
    CodeBlock: ("code",),
    Image: ("alt", "caption"),
    Table: ("caption",),
    Slide: ("title", "notes"),
}

#: Metadata fields holding a single user-visible string.
_METADATA_TEXT_FIELDS = ("title", "subject", "description", "category")
#: Metadata fields holding a list of user-visible strings.
_METADATA_LIST_FIELDS = ("authors", "keywords")


@dataclass
class TextMatch:
    """One located occurrence of a pattern."""

    #: The block whose inline content contains the match.
    block: Node
    #: Character offsets within the block's flattened text.
    start: int
    end: int
    #: The matched text itself.
    text: str
    #: Surrounding text, for display.
    context: str = ""
    #: The style of the run the match starts in.
    style: Optional[TextStyle] = None

    @property
    def type(self) -> str:
        return self.block.type

    def __repr__(self) -> str:
        return f"TextMatch({self.text!r} in {self.block.type} at {self.start})"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def iter_inline_containers(root: Node) -> Iterator[Tuple[Node, str]]:
    """Yield ``(node, field_name)`` for every list-of-inlines in the tree.

    Discovered from dataclass fields at runtime rather than from a hardcoded list, so
    new block types with inline content are picked up without changes here.
    ``Link`` is skipped because its content is reached through its owning paragraph --
    processing it separately would edit the same characters twice.
    """
    for node in root.walk():
        if isinstance(node, Link) or not is_dataclass(node):
            continue
        for spec in fields(node):
            value = getattr(node, spec.name, None)
            if not isinstance(value, list) or not value:
                continue
            if all(isinstance(item, Inline) for item in value):
                yield (node, spec.name)


def _flatten(
    content: Sequence[Inline],
) -> Tuple[str, List[Tuple[Text, int, int]]]:
    """Build the concatenated text plus an index map back to each run.

    Each map entry is ``(run, start, end)`` giving that run's span in the flat string.
    """
    leaves = iter_text_nodes(content)
    pieces: List[str] = []
    spans: List[Tuple[Text, int, int]] = []
    cursor = 0
    for leaf in leaves:
        length = len(leaf.text)
        spans.append((leaf, cursor, cursor + length))
        pieces.append(leaf.text)
        cursor += length
    return ("".join(pieces), spans)


def _build_pattern(
    pattern: str,
    regex: bool,
    ignore_case: bool,
    whole_word: bool,
) -> Pattern[str]:
    source = pattern if regex else re.escape(pattern)
    if whole_word:
        source = rf"\b(?:{source})\b"
    flags = re.IGNORECASE if ignore_case else 0
    try:
        return re.compile(source, flags)
    except re.error as exc:
        raise EditError(f"Invalid pattern {pattern!r}: {exc}") from exc


def _targets(root: Node, selector: Optional[Union[str, Selector]]) -> List[Node]:
    """Resolve the roots to search: the whole tree, or the selector's matches."""
    if selector is None:
        return [root]
    compiled = compile_selector(selector)
    return compiled.select(root) or []


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def find_text(
    root: Node,
    pattern: str,
    regex: bool = False,
    ignore_case: bool = False,
    whole_word: bool = False,
    selector: Optional[Union[str, Selector]] = None,
    context: int = 30,
) -> List[TextMatch]:
    """Locate every occurrence of ``pattern``.

    Matching runs against each block's flattened text, so a phrase split across
    formatting boundaries is still found.

    >>> from polydoc.model import Document, Paragraph, Text, TextStyle
    >>> doc = Document([Paragraph([Text("Total: "), Text("42", TextStyle(bold=True))])])
    >>> [m.text for m in find_text(doc, "Total: 42")]
    ['Total: 42']
    """
    compiled = _build_pattern(pattern, regex, ignore_case, whole_word)
    found: List[TextMatch] = []
    seen: set = set()

    for target in _targets(root, selector):
        for node, field_name in iter_inline_containers(target):
            if id(node) in seen:
                continue
            seen.add(id(node))
            content = getattr(node, field_name)
            flat, spans = _flatten(content)
            if not flat:
                continue
            for match in compiled.finditer(flat):
                start, end = match.span()
                style = None
                for leaf, leaf_start, leaf_end in spans:
                    if leaf_start <= start < leaf_end:
                        style = leaf.style
                        break
                found.append(
                    TextMatch(
                        block=node,
                        start=start,
                        end=end,
                        text=match.group(0),
                        context=flat[max(0, start - context) : end + context],
                        style=style,
                    )
                )
    return found


# ---------------------------------------------------------------------------
# Replace
# ---------------------------------------------------------------------------


def replace_text(
    root: Node,
    pattern: str,
    replacement: Replacement,
    regex: bool = False,
    count: int = 0,
    selector: Optional[Union[str, Selector]] = None,
    ignore_case: bool = False,
    whole_word: bool = False,
    include_plain: bool = True,
) -> int:
    """Replace text throughout ``root``, preserving character formatting.

    Returns the number of replacements made.

    ``pattern``
        Literal text, or a regular expression when ``regex=True``. With ``regex=True``
        the replacement supports the usual ``\\1`` group references.
    ``replacement``
        A string, or a callable receiving the :class:`re.Match` and returning a string.
    ``count``
        Maximum replacements; ``0`` means no limit.
    ``selector``
        Restrict the edit to matching parts of the tree, e.g. ``"table"`` or
        ``"heading[level=1]"``.
    ``include_plain``
        Also rewrite plain-string fields that hold visible text: code bodies, image alt
        text and captions, table captions, slide titles and notes.

    >>> from polydoc.model import Document, Paragraph, Text, TextStyle
    >>> doc = Document([Paragraph([Text("Q1 "), Text("FY", TextStyle(bold=True)), Text("2024")])])
    >>> replace_text(doc, "FY2024", "FY2025")   # spans two runs
    1
    >>> doc.text
    'Q1 FY2025'
    >>> doc.body[0].content[1].style.bold      # bold preserved
    True
    """
    compiled = _build_pattern(pattern, regex, ignore_case, whole_word)
    remaining = count if count > 0 else None
    total = 0
    seen: set = set()

    for target in _targets(root, selector):
        for node, field_name in iter_inline_containers(target):
            if remaining is not None and remaining <= 0:
                break
            if id(node) in seen:
                continue
            seen.add(id(node))
            content = getattr(node, field_name)
            made = _replace_in_content(content, compiled, replacement, remaining)
            if made:
                total += made
                if remaining is not None:
                    remaining -= made
                merged = merge_runs(content)
                setattr(node, field_name, merged)
                node.adopt(*merged)

        if include_plain and (remaining is None or remaining > 0):
            for node in target.walk():
                if remaining is not None and remaining <= 0:
                    break
                made = _replace_plain(node, compiled, replacement, remaining, seen)
                total += made
                if remaining is not None:
                    remaining -= made

            # Metadata is user-visible text too. A template whose title reads
            # "Offer for {{client}}" should end up titled "Offer for Acme Ltd",
            # not carry the placeholder into the output file's properties.
            metadata = getattr(target, "metadata", None)
            if metadata is not None and (remaining is None or remaining > 0):
                made = _replace_metadata(metadata, compiled, replacement, remaining)
                total += made
                if remaining is not None:
                    remaining -= made

    return total


def _replace_metadata(
    metadata: Any,
    compiled: Pattern[str],
    replacement: Replacement,
    limit: Optional[int],
) -> int:
    """Apply replacements to a document's metadata strings."""
    total = 0

    def rewrite(value: str) -> Tuple[str, int]:
        found = list(compiled.finditer(value))
        if not found:
            return (value, 0)
        allowed = found if limit is None else found[: max(0, limit - total)]
        if not allowed:
            return (value, 0)
        out = value
        for match in reversed(allowed):
            start, end = match.span()
            out = out[:start] + _expand(match, replacement) + out[end:]
        return (out, len(allowed))

    for name in _METADATA_TEXT_FIELDS:
        value = getattr(metadata, name, None)
        if isinstance(value, str) and value:
            updated, made = rewrite(value)
            if made:
                setattr(metadata, name, updated)
                total += made

    for name in _METADATA_LIST_FIELDS:
        values = getattr(metadata, name, None)
        if not isinstance(values, list):
            continue
        changed = False
        rebuilt: List[str] = []
        for value in values:
            if isinstance(value, str) and value:
                updated, made = rewrite(value)
                if made:
                    total += made
                    changed = True
                rebuilt.append(updated)
            else:
                rebuilt.append(value)
        if changed:
            setattr(metadata, name, rebuilt)

    return total


def _expand(match: "re.Match", replacement: Replacement) -> str:
    """Resolve a replacement for one match, supporting callables and backreferences."""
    if callable(replacement):
        return str(replacement(match))
    try:
        return match.expand(replacement)
    except re.error:
        # A literal replacement containing a stray backslash is not a template.
        return replacement


def _replace_in_content(
    content: List[Inline],
    compiled: Pattern[str],
    replacement: Replacement,
    limit: Optional[int],
) -> int:
    """Rewrite matches across run boundaries. Returns how many were applied."""
    flat, spans = _flatten(content)
    if not flat or not spans:
        return 0

    found = list(compiled.finditer(flat))
    if not found:
        return 0
    if limit is not None:
        found = found[:limit]
    if not found:
        return 0

    # Right to left: earlier offsets stay valid as we mutate.
    for match in reversed(found):
        start, end = match.span()
        new_text = _expand(match, replacement)
        _splice(spans, start, end, new_text)

    return len(found)


def _splice(
    spans: Sequence[Tuple[Text, int, int]],
    start: int,
    end: int,
    new_text: str,
) -> None:
    """Replace the flat range ``[start, end)`` with ``new_text`` across runs.

    The replacement lands entirely in the first touched run so it inherits a single
    coherent style; the tail of the match is deleted from the runs that follow.
    """
    first = True
    for leaf, leaf_start, leaf_end in spans:
        if leaf_end <= start or leaf_start >= end:
            continue  # untouched by this match

        # Portion of this run covered by the match, in run-local offsets.
        local_start = max(0, start - leaf_start)
        local_end = min(len(leaf.text), end - leaf_start)

        if first:
            leaf.text = leaf.text[:local_start] + new_text + leaf.text[local_end:]
            first = False
        else:
            leaf.text = leaf.text[:local_start] + leaf.text[local_end:]


def _replace_plain(
    node: Node,
    compiled: Pattern[str],
    replacement: Replacement,
    limit: Optional[int],
    seen: set,
) -> int:
    """Apply replacements to plain-string text fields on a node."""
    names = None
    for node_type, field_names in _PLAIN_FIELDS.items():
        if isinstance(node, node_type):
            names = field_names
            break
    if names is None:
        return 0

    total = 0
    for name in names:
        key = (id(node), name)
        if key in seen:
            continue
        seen.add(key)
        value = getattr(node, name, None)
        if not isinstance(value, str) or not value:
            continue
        found = list(compiled.finditer(value))
        if not found:
            continue
        if limit is not None:
            found = found[: max(0, limit - total)]
        if not found:
            continue
        out = value
        for match in reversed(found):
            start, end = match.span()
            out = out[:start] + _expand(match, replacement) + out[end:]
        setattr(node, name, out)
        total += len(found)
    return total


# ---------------------------------------------------------------------------
# Restyling and wholesale text changes
# ---------------------------------------------------------------------------


def style_text(
    root: Node,
    pattern: str,
    regex: bool = False,
    ignore_case: bool = False,
    whole_word: bool = False,
    selector: Optional[Union[str, Selector]] = None,
    **style: Any,
) -> int:
    """Apply character styling to every occurrence of ``pattern``.

    The matched span is split out of its surrounding runs so only the match is
    restyled. Returns the number of occurrences styled.

    >>> from polydoc.model import Document, Paragraph
    >>> doc = Document([Paragraph.of("Status: OVERDUE today")])
    >>> style_text(doc, "OVERDUE", bold=True, color="#cc0000")
    1
    >>> run = [r for r in doc.body[0].content if r.text == "OVERDUE"][0]
    >>> run.style.bold, run.style.color
    (True, '#cc0000')
    """
    if not style:
        raise EditError("style_text() needs at least one style attribute")
    overlay = TextStyle(**style)
    compiled = _build_pattern(pattern, regex, ignore_case, whole_word)
    total = 0
    seen: set = set()

    for target in _targets(root, selector):
        for node, field_name in iter_inline_containers(target):
            if id(node) in seen:
                continue
            seen.add(id(node))
            content = getattr(node, field_name)
            made, rebuilt = _restyle_content(content, compiled, overlay)
            if made:
                total += made
                merged = merge_runs(rebuilt)
                setattr(node, field_name, merged)
                node.adopt(*merged)
    return total


def _restyle_content(
    content: Sequence[Inline],
    compiled: Pattern[str],
    overlay: TextStyle,
) -> Tuple[int, List[Inline]]:
    """Split matched spans into their own runs carrying ``overlay``.

    Rebuilds the top level of the sequence; content nested in a :class:`Link` is
    restyled in place so the link itself survives.
    """
    flat, spans = _flatten(content)
    if not flat:
        return (0, list(content))
    found = list(compiled.finditer(flat))
    if not found:
        return (0, list(content))

    # Map each run to the match ranges that intersect it, in run-local offsets.
    cuts: Dict[int, List[Tuple[int, int]]] = {}
    for match in found:
        start, end = match.span()
        for leaf, leaf_start, leaf_end in spans:
            if leaf_end <= start or leaf_start >= end:
                continue
            cuts.setdefault(id(leaf), []).append(
                (max(0, start - leaf_start), min(len(leaf.text), end - leaf_start))
            )

    def rebuild(nodes: Sequence[Inline]) -> List[Inline]:
        out: List[Inline] = []
        for node in nodes:
            if isinstance(node, Link):
                node.content = rebuild(node.content)
                node.adopt(*node.content)
                out.append(node)
                continue
            if not isinstance(node, Text):
                out.append(node)
                continue
            ranges = cuts.get(id(node))
            if not ranges:
                out.append(node)
                continue
            styled = _replace(node.style, **{
                key: value
                for key, value in overlay.to_dict().items()
            })
            cursor = 0
            for range_start, range_end in sorted(ranges):
                if range_start > cursor:
                    out.append(Text(node.text[cursor:range_start], node.style))
                if range_end > range_start:
                    out.append(Text(node.text[range_start:range_end], styled))
                cursor = max(cursor, range_end)
            if cursor < len(node.text):
                out.append(Text(node.text[cursor:], node.style))
        return out

    return (len(found), rebuild(content))


def set_text(node: Node, text: str) -> Node:
    """Replace a block's entire text, keeping its first run's style.

    Useful for template filling where the whole value changes::

        doc.find("heading[level=1]") and set_text(doc.find("heading[level=1]"), "New Title")

    >>> from polydoc.model import Heading, TextStyle
    >>> h = Heading.of("Old")
    >>> set_text(h, "New").text
    'New'
    """
    for owner, field_name in iter_inline_containers(node):
        if owner is not node:
            continue
        content = getattr(owner, field_name)
        leaves = iter_text_nodes(content)
        style = leaves[0].style if leaves else TextStyle()
        replacement = [Text(text, style)] if text else []
        setattr(owner, field_name, replacement)
        owner.adopt(*replacement)
        return node

    # No inline content: fall back to the plain-string fields.
    for node_type, names in _PLAIN_FIELDS.items():
        if isinstance(node, node_type) and names:
            setattr(node, names[0], text)
            return node
    raise EditError(f"{node.type} has no editable text")
