"""A CSS-like selector engine for the document tree.

Editing a document means first *finding* the part you want to change. Rather than
inventing a bespoke query API, this borrows CSS -- a syntax most people already know::

    doc.find_all("heading[level=2]")            # every second-level heading
    doc.find_all("table td:contains(Overdue)")  # cells mentioning a word
    doc.find("section:has(table) > paragraph")  # first paragraph of a section with a table
    doc.find_all("list_item[checked=false]")    # unfinished task items

Supported grammar:

``type``
    A node type name (``heading``, ``paragraph``, ``table_cell``, ...), or ``*``.
    HTML-ish aliases work too: ``p``, ``h2``, ``ul``, ``li``, ``td``, ``pre``, ``img``.
``[attr]``, ``[attr=value]``
    Attribute presence or comparison. Operators: ``=``, ``!=``, ``^=`` (starts with),
    ``$=`` (ends with), ``*=`` (contains), ``>``, ``<``, ``>=``, ``<=``.
    Dotted paths reach into styles and ``attrs``: ``[style.alignment=center]``.
``:contains(text)``, ``:matches(regex)``
    Text tests, case-insensitive for ``contains``.
``:first``, ``:last``, ``:nth(n)``
    Position among matching siblings (``nth`` is 1-based).
``:empty``, ``:not(sel)``, ``:has(sel)``
    Structural tests.
``A B``, ``A > B``, ``A, B``
    Descendant, direct child, and union.

The engine is intentionally small: it walks the tree and tests nodes, with no indexing
or optimisation, because document trees are thousands of nodes rather than millions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

from ..exceptions import SelectorError
from ..model.base import Node

__all__ = ["Selector", "compile_selector", "matches", "select", "select_one"]

#: Familiar HTML names mapped onto model type names (with implied attributes).
_ALIASES: Dict[str, Tuple[str, Dict[str, Any]]] = {
    "p": ("paragraph", {}),
    "para": ("paragraph", {}),
    "h": ("heading", {}),
    "h1": ("heading", {"level": 1}),
    "h2": ("heading", {"level": 2}),
    "h3": ("heading", {"level": 3}),
    "h4": ("heading", {"level": 4}),
    "h5": ("heading", {"level": 5}),
    "h6": ("heading", {"level": 6}),
    "ul": ("list_block", {"ordered": False}),
    "ol": ("list_block", {"ordered": True}),
    "list": ("list_block", {}),
    "li": ("list_item", {}),
    "item": ("list_item", {}),
    "tr": ("table_row", {}),
    "td": ("table_cell", {}),
    "th": ("table_cell", {}),
    "cell": ("table_cell", {}),
    "row": ("table_row", {}),
    "pre": ("code_block", {}),
    "code": ("code_block", {}),
    "img": ("image", {}),
    "hr": ("horizontal_rule", {}),
    "br": ("line_break", {}),
    "a": ("link", {}),
    "blockquote": ("quote", {}),
    "run": ("text", {}),
}

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<comma>,)
  | (?P<child>>)
  | (?P<star>\*)
  | (?P<hash>\#[\w-]+)
  | (?P<attr>\[[^\]]*\])
  | (?P<pseudo>:[a-zA-Z-]+)
  | (?P<ident>[A-Za-z_][\w-]*)
    """,
    re.VERBOSE,
)

_ATTR_RE = re.compile(
    r"^\[\s*(?P<name>[\w.\-]+)\s*(?:(?P<op>[!^$*]?=|>=|<=|>|<)\s*(?P<value>.*?)\s*)?\]$"
)

#: Pseudo-classes that take an argument.
_ARG_PSEUDOS = {"contains", "matches", "nth", "not", "has", "icontains"}
_BARE_PSEUDOS = {"first", "last", "empty", "root", "only"}


# ---------------------------------------------------------------------------
# Parsed representation
# ---------------------------------------------------------------------------


@dataclass
class _Attr:
    name: str
    op: Optional[str] = None
    value: Optional[str] = None

    def test(self, node: Node) -> bool:
        actual = _resolve(node, self.name)
        if self.op is None:
            return actual is not None and actual is not False
        if actual is None:
            return False
        return _compare(actual, self.op, self.value or "")


@dataclass
class _Pseudo:
    name: str
    argument: Optional[str] = None
    #: Populated for :not() / :has(), which nest a selector.
    nested: Optional["Selector"] = None


@dataclass
class _Compound:
    """One element in a selector chain: a type plus its filters."""

    type_name: Optional[str] = None
    nid: Optional[str] = None
    attrs: List[_Attr] = field(default_factory=list)
    pseudos: List[_Pseudo] = field(default_factory=list)

    def matches(self, node: Node) -> bool:
        if self.type_name is not None and node.type != self.type_name:
            return False
        if self.nid is not None and node.nid != self.nid:
            return False
        for attr in self.attrs:
            if not attr.test(node):
                return False
        for pseudo in self.pseudos:
            if not _test_pseudo(node, pseudo):
                return False
        return True


@dataclass
class _Step:
    compound: _Compound
    #: How this step relates to the previous one: ``" "`` descendant, ``">"`` child.
    combinator: str = " "


class Selector:
    """A compiled selector. Reusable across documents and cheap to apply."""

    __slots__ = ("source", "_chains")

    def __init__(self, source: str, chains: Sequence[Sequence[_Step]]) -> None:
        self.source = source
        self._chains = [list(chain) for chain in chains]

    def matches(self, node: Node) -> bool:
        """True when ``node`` satisfies any of the comma-separated alternatives."""
        return any(self._match_chain(node, chain) for chain in self._chains)

    def select(self, root: Node) -> List[Node]:
        """Every matching node in ``root``'s subtree, in document order."""
        return [node for node in root.walk() if self.matches(node)]

    def select_one(self, root: Node) -> Optional[Node]:
        for node in root.walk():
            if self.matches(node):
                return node
        return None

    def __iter__(self) -> Iterator[List[_Step]]:
        return iter(self._chains)

    def __repr__(self) -> str:
        return f"Selector({self.source!r})"

    # -- matching -------------------------------------------------------------
    def _match_chain(self, node: Node, chain: Sequence[_Step]) -> bool:
        """Match right to left, which lets us fail fast on the cheapest test."""
        if not chain:
            return False
        if not chain[-1].compound.matches(node):
            return False
        return self._match_ancestors(node, chain[:-1], chain[-1].combinator)

    def _match_ancestors(
        self,
        node: Node,
        remaining: Sequence[_Step],
        combinator: str,
    ) -> bool:
        if not remaining:
            return True
        step = remaining[-1]

        if combinator == ">":
            parent = node.parent
            if parent is None or not step.compound.matches(parent):
                return False
            return self._match_ancestors(parent, remaining[:-1], step.combinator)

        # Descendant: try every ancestor.
        for ancestor in node.ancestors():
            if step.compound.matches(ancestor) and self._match_ancestors(
                ancestor, remaining[:-1], step.combinator
            ):
                return True
        return False


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_CACHE: Dict[str, Selector] = {}


def compile_selector(source: Union[str, Selector]) -> Selector:
    """Parse a selector string, with caching.

    >>> compile_selector("h2, paragraph").source
    'h2, paragraph'
    """
    if isinstance(source, Selector):
        return source
    if not isinstance(source, str):
        raise SelectorError(f"A selector must be a string, got {type(source).__name__}")
    text = source.strip()
    if not text:
        raise SelectorError("Empty selector")
    cached = _CACHE.get(text)
    if cached is not None:
        return cached
    selector = Selector(text, _parse(text))
    _CACHE[text] = selector
    return selector


def _parse(source: str) -> List[List[_Step]]:
    chains: List[List[_Step]] = []
    for part in _split_top_level(source):
        chain = _parse_chain(part)
        if chain:
            chains.append(chain)
    if not chains:
        raise SelectorError(f"Could not parse selector: {source!r}")
    return chains


def _split_top_level(source: str) -> List[str]:
    """Split on commas that are not inside brackets or parentheses."""
    parts: List[str] = []
    depth = 0
    current: List[str] = []
    for char in source:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _parse_chain(source: str) -> List[_Step]:
    steps: List[_Step] = []
    current = _Compound()
    started = False
    pending_combinator = " "
    index = 0
    length = len(source)

    def commit(combinator: str) -> None:
        nonlocal current, started
        if started:
            steps.append(_Step(current, combinator))
        current = _Compound()
        started = False

    while index < length:
        char = source[index]

        if char.isspace():
            index += 1
            while index < length and source[index].isspace():
                index += 1
            # Whitespace is a descendant combinator only when it separates two
            # compounds. The `started` guard matters for "a > b": the space after ">"
            # must not overwrite the pending ">" with a descendant combinator.
            if started and index < length and source[index] not in ">,":
                commit(pending_combinator)
                pending_combinator = " "
            continue

        if char == ">":
            commit(pending_combinator)
            pending_combinator = ">"
            index += 1
            continue

        if char == "*":
            started = True
            index += 1
            continue

        if char == "#":
            match = re.match(r"#([\w-]+)", source[index:])
            if not match:
                raise SelectorError(f"Bad id in selector: {source!r}")
            current.nid = match.group(1)
            started = True
            index += match.end()
            continue

        if char == "[":
            close = source.find("]", index)
            if close == -1:
                raise SelectorError(f"Unclosed '[' in selector: {source!r}")
            current.attrs.append(_parse_attr(source[index : close + 1]))
            started = True
            index = close + 1
            continue

        if char == ":":
            pseudo, index = _parse_pseudo(source, index)
            current.pseudos.append(pseudo)
            started = True
            continue

        match = re.match(r"[A-Za-z_][\w-]*", source[index:])
        if not match:
            raise SelectorError(f"Unexpected {char!r} in selector: {source!r}")
        raw = match.group(0)
        name, implied = _ALIASES.get(raw.lower(), (raw.lower().replace("-", "_"), {}))
        current.type_name = name
        for key, value in implied.items():
            current.attrs.append(_Attr(key, "=", str(value).lower()))
        started = True
        index += match.end()

    commit(pending_combinator)
    return steps


def _parse_attr(source: str) -> _Attr:
    match = _ATTR_RE.match(source)
    if not match:
        raise SelectorError(f"Bad attribute selector: {source!r}")
    value = match.group("value")
    if value is not None:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
    return _Attr(match.group("name"), match.group("op"), value)


def _parse_pseudo(source: str, index: int) -> Tuple[_Pseudo, int]:
    match = re.match(r":([a-zA-Z-]+)", source[index:])
    if not match:
        raise SelectorError(f"Bad pseudo-class in selector: {source!r}")
    name = match.group(1).lower().replace("-", "_")
    cursor = index + match.end()

    argument: Optional[str] = None
    if cursor < len(source) and source[cursor] == "(":
        depth = 0
        end = cursor
        for position in range(cursor, len(source)):
            if source[position] == "(":
                depth += 1
            elif source[position] == ")":
                depth -= 1
                if depth == 0:
                    end = position
                    break
        else:
            raise SelectorError(f"Unclosed '(' in selector: {source!r}")
        argument = source[cursor + 1 : end].strip()
        if len(argument) >= 2 and argument[0] == argument[-1] and argument[0] in "\"'":
            argument = argument[1:-1]
        cursor = end + 1

    if name in _ARG_PSEUDOS and argument is None:
        raise SelectorError(f":{name} requires an argument")
    if name in _BARE_PSEUDOS and argument is not None:
        raise SelectorError(f":{name} takes no argument")
    if name not in _ARG_PSEUDOS and name not in _BARE_PSEUDOS:
        raise SelectorError(
            f"Unknown pseudo-class :{name}. Supported: "
            f"{', '.join(sorted(_ARG_PSEUDOS | _BARE_PSEUDOS))}"
        )

    pseudo = _Pseudo(name, argument)
    if name in ("not", "has"):
        pseudo.nested = compile_selector(argument or "*")
    return pseudo, cursor


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------


def _resolve(node: Node, path: str) -> Any:
    """Follow a dotted attribute path, also looking inside ``attrs`` dicts."""
    current: Any = node
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
            continue
        nxt = getattr(current, part, None)
        if nxt is None and hasattr(current, "attrs"):
            attrs = getattr(current, "attrs")
            if isinstance(attrs, dict) and part in attrs:
                nxt = attrs[part]
        current = nxt
    return current


def _compare(actual: Any, op: str, expected: str) -> bool:
    from enum import Enum

    if isinstance(actual, Enum):
        actual = actual.value

    if op in (">", "<", ">=", "<="):
        try:
            left, right = float(actual), float(expected)
        except (TypeError, ValueError):
            return False
        return {
            ">": left > right,
            "<": left < right,
            ">=": left >= right,
            "<=": left <= right,
        }[op]

    if isinstance(actual, bool):
        wanted = expected.strip().lower() in ("true", "1", "yes")
        return (actual == wanted) if op == "=" else (actual != wanted)

    if isinstance(actual, (int, float)) and op in ("=", "!="):
        try:
            equal = float(actual) == float(expected)
        except (TypeError, ValueError):
            equal = str(actual) == expected
        return equal if op == "=" else not equal

    text = str(actual)
    if op == "=":
        return text == expected
    if op == "!=":
        return text != expected
    if op == "^=":
        return text.startswith(expected)
    if op == "$=":
        return text.endswith(expected)
    if op == "*=":
        return expected in text
    return False


def _test_pseudo(node: Node, pseudo: _Pseudo) -> bool:
    name = pseudo.name

    if name == "contains":
        return (pseudo.argument or "").lower() in node.text.lower()
    if name == "icontains":
        return (pseudo.argument or "").lower() in node.text.lower()
    if name == "matches":
        try:
            return re.search(pseudo.argument or "", node.text) is not None
        except re.error as exc:
            raise SelectorError(f"Bad regex in :matches(): {exc}") from exc
    if name == "empty":
        return not node.text.strip()
    if name == "root":
        return node.parent is None
    if name == "not":
        return pseudo.nested is not None and not pseudo.nested.matches(node)
    if name == "has":
        if pseudo.nested is None:
            return False
        return any(
            pseudo.nested.matches(descendant)
            for descendant in node.walk(include_self=False)
        )

    siblings = _siblings_of_type(node)
    if name == "first":
        return bool(siblings) and siblings[0] is node
    if name == "last":
        return bool(siblings) and siblings[-1] is node
    if name == "only":
        return len(siblings) == 1
    if name == "nth":
        try:
            wanted = int(pseudo.argument or "0")
        except ValueError:
            raise SelectorError(f":nth() needs an integer, got {pseudo.argument!r}") from None
        if wanted < 0:
            wanted = len(siblings) + wanted + 1
        return 1 <= wanted <= len(siblings) and siblings[wanted - 1] is node
    return False


def _siblings_of_type(node: Node) -> List[Node]:
    """Siblings sharing this node's type, used by the positional pseudo-classes."""
    parent = node.parent
    if parent is None:
        return [node]
    return [child for child in parent.children() if child.type == node.type]


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def select(root: Node, selector: Union[str, Selector]) -> List[Node]:
    """Every node in ``root`` matching ``selector``, in document order."""
    return compile_selector(selector).select(root)


def select_one(root: Node, selector: Union[str, Selector]) -> Optional[Node]:
    """The first matching node, or ``None``."""
    return compile_selector(selector).select_one(root)


def matches(node: Node, selector: Union[str, Selector]) -> bool:
    """Test a single node against a selector."""
    return compile_selector(selector).matches(node)
