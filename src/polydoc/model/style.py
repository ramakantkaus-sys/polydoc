"""Style primitives shared by every format.

Styles are deliberately *sparse*: every field defaults to ``None`` meaning
"inherit / unspecified". This matters for fidelity. When we read a DOCX run that
only sets ``bold``, we record exactly that, rather than inventing a font size that
would then be baked into the output of another format.

:meth:`TextStyle.merge` implements the inheritance chain used by nested markup
(HTML ``<b><i>``, DOCX style hierarchies, and so on).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields, replace
from enum import Enum
from typing import Any, Dict, Optional, Tuple

__all__ = [
    "Alignment",
    "Color",
    "ListStyle",
    "ParagraphStyle",
    "TextStyle",
    "VerticalAlign",
]

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

_NAMED_COLORS = {
    "black": "#000000",
    "white": "#ffffff",
    "red": "#ff0000",
    "green": "#008000",
    "lime": "#00ff00",
    "blue": "#0000ff",
    "yellow": "#ffff00",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "gray": "#808080",
    "grey": "#808080",
    "silver": "#c0c0c0",
    "maroon": "#800000",
    "olive": "#808000",
    "navy": "#000080",
    "purple": "#800080",
    "teal": "#008080",
    "orange": "#ffa500",
}


class Alignment(str, Enum):
    """Horizontal alignment of a block."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"

    @classmethod
    def coerce(cls, value: Any) -> Optional["Alignment"]:
        """Best-effort conversion from arbitrary backend values."""
        if value is None or isinstance(value, cls):
            return value
        text = str(value).strip().lower()
        aliases = {
            "start": cls.LEFT,
            "end": cls.RIGHT,
            "centre": cls.CENTER,
            "middle": cls.CENTER,
            "both": cls.JUSTIFY,
            "justified": cls.JUSTIFY,
            "justify_low": cls.JUSTIFY,
            "distribute": cls.JUSTIFY,
        }
        if text in aliases:
            return aliases[text]
        try:
            return cls(text)
        except ValueError:
            return None


class VerticalAlign(str, Enum):
    """Vertical alignment, used by table cells and text boxes."""

    TOP = "top"
    MIDDLE = "middle"
    BOTTOM = "bottom"

    @classmethod
    def coerce(cls, value: Any) -> Optional["VerticalAlign"]:
        if value is None or isinstance(value, cls):
            return value
        text = str(value).strip().lower()
        aliases = {"center": cls.MIDDLE, "centre": cls.MIDDLE, "baseline": cls.BOTTOM}
        if text in aliases:
            return aliases[text]
        try:
            return cls(text)
        except ValueError:
            return None


class ListStyle(str, Enum):
    """Marker style for a list block."""

    BULLET = "bullet"
    ORDERED = "ordered"
    LOWER_ALPHA = "lower-alpha"
    UPPER_ALPHA = "upper-alpha"
    LOWER_ROMAN = "lower-roman"
    UPPER_ROMAN = "upper-roman"
    NONE = "none"

    @property
    def is_ordered(self) -> bool:
        return self is not ListStyle.BULLET and self is not ListStyle.NONE


class Color(str):
    """An RGB colour stored as a lowercase ``#rrggbb`` string.

    Subclasses :class:`str` so it serialises transparently to JSON and compares
    cleanly against plain strings.

    >>> Color.parse("Red")
    '#ff0000'
    >>> Color.from_rgb(255, 128, 0)
    '#ff8000'
    """

    __slots__ = ()

    def __new__(cls, value: str) -> "Color":
        parsed = cls.parse(value)
        if parsed is None:
            raise ValueError(f"Not a valid colour: {value!r}")
        return super().__new__(cls, parsed)

    @classmethod
    def parse(cls, value: Any) -> Optional[str]:
        """Return a normalised ``#rrggbb`` string, or ``None`` if unparseable."""
        if value is None:
            return None
        if isinstance(value, (tuple, list)) and len(value) >= 3:
            return cls.from_rgb(*value[:3])
        text = str(value).strip().lower()
        if not text or text in {"auto", "none", "inherit", "transparent"}:
            return None
        if text in _NAMED_COLORS:
            return _NAMED_COLORS[text]
        rgb_match = re.match(
            r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", text
        )
        if rgb_match:
            return cls.from_rgb(*(int(g) for g in rgb_match.groups()))
        match = _HEX_RE.match(text)
        if not match:
            return None
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(ch * 2 for ch in digits)
        return f"#{digits.lower()}"

    @classmethod
    def from_rgb(cls, r: int, g: int, b: int) -> str:
        """Build a colour string from 0-255 components (values are clamped)."""
        clamp = lambda v: max(0, min(255, int(round(float(v)))))  # noqa: E731
        return f"#{clamp(r):02x}{clamp(g):02x}{clamp(b):02x}"

    @classmethod
    def from_int(cls, value: int) -> str:
        """Build a colour from a packed ``0xRRGGBB`` integer (as PyMuPDF reports)."""
        value = int(value) & 0xFFFFFF
        return f"#{value:06x}"

    @property
    def rgb(self) -> Tuple[int, int, int]:
        """The colour as a ``(r, g, b)`` tuple of 0-255 ints."""
        return (int(self[1:3], 16), int(self[3:5], 16), int(self[5:7], 16))

    @property
    def luminance(self) -> float:
        """Relative luminance in ``0.0..1.0``, useful for contrast decisions."""
        r, g, b = (c / 255.0 for c in self.rgb)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b


class _SparseStyle:
    """Mixin giving sparse dataclass styles their merge/serialise behaviour."""

    def merge(self, other: Optional["_SparseStyle"]) -> Any:
        """Overlay ``other`` on top of ``self``; ``other``'s set fields win.

        Used for style inheritance, where ``self`` is the outer/parent style.
        """
        if other is None:
            return self
        overrides = {
            f.name: getattr(other, f.name)
            for f in fields(other)  # type: ignore[arg-type]
            if getattr(other, f.name) is not None
        }
        return replace(self, **overrides)  # type: ignore[type-var]

    def is_empty(self) -> bool:
        """True when no field is set (i.e. fully inherited)."""
        return all(getattr(self, f.name) is None for f in fields(self))  # type: ignore[arg-type]

    def to_dict(self) -> Dict[str, Any]:
        """Serialise, omitting unset fields to keep output compact."""
        out: Dict[str, Any] = {}
        for key, value in asdict(self).items():  # type: ignore[call-overload]
            if value is None:
                continue
            out[key] = value.value if isinstance(value, Enum) else value
        return out

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Any:
        if not data:
            return cls()  # type: ignore[call-arg]
        known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        return cls(**{k: v for k, v in data.items() if k in known})  # type: ignore[call-arg]

    def __repr__(self) -> str:
        """Show only the fields that are actually set -- sparse styles are mostly
        ``None``, and printing all of them buries the signal."""
        parts = [
            f"{f.name}={getattr(self, f.name)!r}"
            for f in fields(self)  # type: ignore[arg-type]
            if getattr(self, f.name) is not None
        ]
        return f"{type(self).__name__}({', '.join(parts)})"


@dataclass(frozen=True, repr=False)
class TextStyle(_SparseStyle):
    """Character-level formatting.

    ``repr=False`` so the sparse ``__repr__`` from :class:`_SparseStyle` is used; the
    generated one prints all thirteen fields, most of them ``None``.

    >>> TextStyle(bold=True).merge(TextStyle(italic=True))
    TextStyle(bold=True, italic=True, ...)
    """

    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    strike: Optional[bool] = None
    code: Optional[bool] = None
    superscript: Optional[bool] = None
    subscript: Optional[bool] = None
    small_caps: Optional[bool] = None
    highlight: Optional[bool] = None
    font_family: Optional[str] = None
    font_size: Optional[float] = None
    color: Optional[str] = None
    background: Optional[str] = None

    def __post_init__(self) -> None:
        # Normalise colours without breaking frozen-ness.
        for key in ("color", "background"):
            raw = getattr(self, key)
            if raw is not None:
                object.__setattr__(self, key, Color.parse(raw))
        if self.font_size is not None:
            object.__setattr__(self, "font_size", round(float(self.font_size), 2))

    @property
    def is_monospace(self) -> bool:
        """Heuristic detection of monospaced fonts, used to infer code spans."""
        if self.code:
            return True
        if not self.font_family:
            return False
        name = self.font_family.lower()
        return any(
            token in name
            for token in ("mono", "courier", "consolas", "menlo", "hack", "typewriter")
        )


@dataclass(frozen=True, repr=False)
class ParagraphStyle(_SparseStyle):
    """Block-level formatting. Measurements are in points."""

    alignment: Optional[Alignment] = None
    space_before: Optional[float] = None
    space_after: Optional[float] = None
    line_spacing: Optional[float] = None
    indent_left: Optional[float] = None
    indent_right: Optional[float] = None
    first_line_indent: Optional[float] = None
    background: Optional[str] = None
    style_name: Optional[str] = None
    direction: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "alignment", Alignment.coerce(self.alignment))
        if self.background is not None:
            object.__setattr__(self, "background", Color.parse(self.background))
