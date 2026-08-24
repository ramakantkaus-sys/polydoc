"""The :class:`Node` base class and the generic serialisation codec.

Every element in a polydoc document -- inline runs, paragraphs, tables, slides --
is a :class:`Node`. Nodes are mutable dataclasses so editing is direct and obvious
(``para.style = ParagraphStyle(alignment=Alignment.CENTER)``), and they carry two
pieces of bookkeeping that are *not* dataclass fields:

``nid``
    A lazily-generated stable identity, so an edit can reference a node found by an
    earlier query without relying on list positions.

``parent``
    Set automatically by container nodes. Enables upward traversal
    (:meth:`Node.ancestors`) which the selector engine needs for ``>`` matching.

Neither participates in ``==`` or ``repr``, so two structurally identical documents
compare equal -- which is exactly what you want when testing a round trip.

The codec below serialises any node tree to plain JSON-safe dicts and back, driven
by dataclass field annotations. New node types get serialisation for free.
"""

from __future__ import annotations

import typing
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional, Type, TypeVar
from uuid import uuid4

from .style import ParagraphStyle, TextStyle

__all__ = ["Node", "NODE_REGISTRY", "decode_value", "encode_value", "register_node"]

#: Maps the ``type`` discriminator to the concrete class, for deserialisation.
NODE_REGISTRY: Dict[str, Type["Node"]] = {}

T = TypeVar("T", bound="Node")

#: Sparse styles that serialise to ``{}`` when nothing is set; ``to_dict`` drops those.
_STYLE_TYPES = (TextStyle, ParagraphStyle)


def _snake_case(name: str) -> str:
    out: List[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index and not name[index - 1].isupper():
            out.append("_")
        out.append(char.lower())
    return "".join(out).strip("_")


def register_node(cls: Type[T]) -> Type[T]:
    """Register a node class under its ``type`` discriminator.

    Applied automatically via ``__init_subclass__``; exposed publicly so
    third-party node types can opt in explicitly.
    """
    NODE_REGISTRY[cls.type_name()] = cls
    return cls


class Node:
    """Base class for every document element."""

    #: Overridable discriminator. When ``None`` the snake-cased class name is used.
    TYPE: Optional[str] = None

    # Class-level fallbacks; assignment creates the instance attribute.
    _nid: Optional[str] = None
    _parent: Optional["Node"] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Note this runs *before* @dataclass has been applied, so we cannot filter
        # on is_dataclass here. Abstract intermediates (Block, Inline) get registered
        # too; they are simply never named by a discriminator, and :meth:`from_dict`
        # rejects them explicitly.
        register_node(cls)

    # -- identity -------------------------------------------------------------
    @classmethod
    def type_name(cls) -> str:
        """The discriminator written into serialised output."""
        return cls.TYPE or _snake_case(cls.__name__)

    @property
    def type(self) -> str:
        return self.type_name()

    @property
    def nid(self) -> str:
        """A stable per-instance id, generated on first access."""
        if self._nid is None:
            self._nid = uuid4().hex[:12]
        return self._nid

    @nid.setter
    def nid(self, value: str) -> None:
        self._nid = value

    # -- tree links -----------------------------------------------------------
    @property
    def parent(self) -> Optional["Node"]:
        """The containing node, or ``None`` for a root."""
        return self._parent

    @parent.setter
    def parent(self, value: Optional["Node"]) -> None:
        self._parent = value

    def children(self) -> List["Node"]:
        """Direct child nodes, in document order.

        The default implementation discovers children from dataclass fields, so
        most node types never override it.
        """
        found: List[Node] = []
        if not is_dataclass(self):
            return found
        for spec in fields(self):
            value = getattr(self, spec.name, None)
            if isinstance(value, Node):
                found.append(value)
            elif isinstance(value, list):
                found.extend(item for item in value if isinstance(item, Node))
        return found

    def adopt(self, *nodes: Optional["Node"]) -> None:
        """Claim parenthood of ``nodes``. Containers call this after mutation."""
        for node in nodes:
            if isinstance(node, Node):
                node.parent = self

    def reparent(self) -> "Node":
        """Recursively fix up ``parent`` links for this whole subtree.

        Readers build trees bottom-up, so links are stitched in one pass at the end.
        """
        for child in self.children():
            child.parent = self
            child.reparent()
        return self

    def walk(self, include_self: bool = True) -> Iterator["Node"]:
        """Depth-first pre-order traversal of this subtree."""
        if include_self:
            yield self
        for child in self.children():
            yield from child.walk(include_self=True)

    def ancestors(self) -> Iterator["Node"]:
        """Yield parent, grandparent, ... up to the root."""
        node = self.parent
        seen = set()
        while node is not None and id(node) not in seen:
            seen.add(id(node))
            yield node
            node = node.parent

    def find_by_id(self, nid: str) -> Optional["Node"]:
        """Locate a node in this subtree by its :attr:`nid`."""
        for node in self.walk():
            if node._nid == nid:
                return node
        return None

    def detach(self) -> "Node":
        """Remove this node from its parent's child collections."""
        parent = self.parent
        if parent is None or not is_dataclass(parent):
            return self
        for spec in fields(parent):
            value = getattr(parent, spec.name, None)
            if value is self:
                setattr(parent, spec.name, None)
            elif isinstance(value, list) and any(item is self for item in value):
                setattr(parent, spec.name, [item for item in value if item is not self])
        self.parent = None
        return self

    def replace_with(self, *nodes: "Node") -> None:
        """Swap this node for ``nodes`` in its parent, preserving position."""
        parent = self.parent
        if parent is None or not is_dataclass(parent):
            raise ValueError("Cannot replace a node that has no parent")
        for spec in fields(parent):
            value = getattr(parent, spec.name, None)
            if value is self:
                if len(nodes) != 1:
                    raise ValueError(
                        f"Field {spec.name!r} holds a single node; got {len(nodes)} replacements"
                    )
                setattr(parent, spec.name, nodes[0])
                parent.adopt(nodes[0])
                self.parent = None
                return
            if isinstance(value, list):
                for index, item in enumerate(value):
                    if item is self:
                        value[index : index + 1] = list(nodes)
                        parent.adopt(*nodes)
                        self.parent = None
                        return
        raise ValueError("Node is not present in its parent's children")

    # -- text ----------------------------------------------------------------
    @property
    def text(self) -> str:
        """Plain-text rendering of this subtree. Overridden by leaf types."""
        return "".join(child.text for child in self.children())

    # -- serialisation --------------------------------------------------------
    def to_dict(self, include_ids: bool = False) -> Dict[str, Any]:
        """Serialise to a JSON-safe dict, omitting empty/default values."""
        data: Dict[str, Any] = {"type": self.type_name()}
        if include_ids:
            data["nid"] = self.nid
        if is_dataclass(self):
            for spec in fields(self):
                value = getattr(self, spec.name, None)
                if value is None or value == [] or value == {}:
                    continue
                encoded = encode_value(value, include_ids=include_ids)
                # A sparse style with nothing set carries no information.
                if encoded == {} and isinstance(value, _STYLE_TYPES):
                    continue
                data[spec.name] = encoded
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Node":
        """Rebuild a node (and its subtree) from :meth:`to_dict` output.

        Dispatches on the ``type`` discriminator, so calling
        ``Node.from_dict(payload)`` works for any registered node type.
        """
        if not isinstance(data, dict):
            raise TypeError(f"Expected a dict to decode a node, got {type(data).__name__}")
        type_name = data.get("type")
        target: Type[Node]
        if type_name is None:
            if cls is Node:
                raise ValueError("Cannot decode a node without a 'type' key")
            target = cls
        else:
            resolved = NODE_REGISTRY.get(type_name)
            if resolved is None:
                raise ValueError(f"Unknown node type {type_name!r}")
            target = resolved

        if not is_dataclass(target):
            raise ValueError(
                f"Node type {target.type_name()!r} is abstract and cannot be instantiated"
            )

        hints = _resolved_hints(target)
        kwargs: Dict[str, Any] = {}
        for spec in fields(target):  # type: ignore[arg-type]
            if spec.name not in data:
                continue
            kwargs[spec.name] = decode_value(data[spec.name], hints.get(spec.name, Any))
        node = target(**kwargs)  # type: ignore[call-arg]
        if "nid" in data:
            node.nid = data["nid"]
        return node.reparent()

    # -- convenience ----------------------------------------------------------
    def copy(self: T) -> T:
        """A deep, independent copy with fresh node ids."""
        return type(self).from_dict(self.to_dict())  # type: ignore[return-value]

    def __repr__(self) -> str:
        if not is_dataclass(self):
            return f"{type(self).__name__}()"
        parts = []
        for spec in fields(self):
            value = getattr(self, spec.name, None)
            if value is None or value == [] or value == {}:
                continue
            if isinstance(value, list):
                parts.append(f"{spec.name}=[{len(value)} item{'s' if len(value) != 1 else ''}]")
            elif isinstance(value, str) and len(value) > 40:
                parts.append(f"{spec.name}={value[:37]!r}...")
            else:
                parts.append(f"{spec.name}={value!r}")
        return f"{type(self).__name__}({', '.join(parts)})"


# ---------------------------------------------------------------------------
# Generic codec
# ---------------------------------------------------------------------------

_HINT_CACHE: Dict[type, Dict[str, Any]] = {}


def _resolved_hints(cls: type) -> Dict[str, Any]:
    """Resolve (and cache) a dataclass's type hints, tolerating forward refs."""
    cached = _HINT_CACHE.get(cls)
    if cached is not None:
        return cached
    try:
        hints = typing.get_type_hints(cls)
    except Exception:  # pragma: no cover - unresolvable annotations
        hints = {}
    _HINT_CACHE[cls] = hints
    return hints


def encode_value(value: Any, include_ids: bool = False) -> Any:
    """Convert a model value into JSON-safe primitives."""
    if isinstance(value, Node):
        return value.to_dict(include_ids=include_ids)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [encode_value(item, include_ids=include_ids) for item in value]
    if isinstance(value, dict):
        return {str(k): encode_value(v, include_ids=include_ids) for k, v in value.items()}
    # Any non-Node dataclass that knows how to serialise itself: styles, geometry,
    # metadata, and anything a downstream project adds.
    if is_dataclass(value) and hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, bytes):
        import base64

        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    return value


def _unwrap_optional(hint: Any) -> Any:
    """Reduce ``Optional[X]`` to ``X``; leave genuine unions alone."""
    origin = typing.get_origin(hint)
    if origin is typing.Union:
        args = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(args) == 1:
            return args[0]
        return typing.Union[tuple(args)]  # type: ignore[return-value]
    return hint


def decode_value(value: Any, hint: Any) -> Any:
    """Rebuild a model value from primitives, guided by a type annotation."""
    if value is None:
        return None

    if isinstance(value, dict) and "__bytes__" in value:
        import base64

        return base64.b64decode(value["__bytes__"])

    hint = _unwrap_optional(hint)
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)

    if origin in (list, typing.List) or origin is list:
        item_hint = args[0] if args else Any
        return [decode_value(item, item_hint) for item in value]

    if origin in (dict, typing.Dict) or origin is dict:
        value_hint = args[1] if len(args) > 1 else Any
        return {k: decode_value(v, value_hint) for k, v in value.items()}

    # A union of node types (e.g. Union[Text, Link]) is resolved by discriminator.
    if origin is typing.Union:
        if isinstance(value, dict) and "type" in value:
            return Node.from_dict(value)
        return value

    if isinstance(hint, type):
        if issubclass(hint, Node):
            return Node.from_dict(value) if isinstance(value, dict) else value
        if issubclass(hint, Enum):
            try:
                return hint(value)
            except ValueError:
                coerce = getattr(hint, "coerce", None)
                return coerce(value) if coerce else None
        loader = getattr(hint, "from_dict", None)
        if callable(loader) and isinstance(value, dict):
            return loader(value)

    # Unannotated / Any: recover nested nodes opportunistically.
    if isinstance(value, dict) and "type" in value and value["type"] in NODE_REGISTRY:
        return Node.from_dict(value)
    if isinstance(value, list):
        return [decode_value(item, Any) for item in value]
    return value
