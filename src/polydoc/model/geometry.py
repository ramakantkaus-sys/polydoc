"""Geometry primitives.

These carry the spatial information that only page-based formats (PDF, PPTX) have.
The intelligence layer leans on them heavily: column detection, reading order, and
table reconstruction are all coordinate problems.

Coordinate convention: **y grows downward** (top-left origin), matching PDF text
extraction output from PyMuPDF and pdfplumber, plus HTML/CSS. Readers normalise
into this convention so downstream code never has to ask.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

__all__ = ["BBox", "PageGeometry", "Point", "Size"]


@dataclass(frozen=True)
class Point:
    """A 2D point in points (1/72 inch)."""

    x: float
    y: float

    def to_dict(self) -> Dict[str, float]:
        return {"x": self.x, "y": self.y}


@dataclass(frozen=True)
class Size:
    """A width/height pair in points."""

    width: float
    height: float

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height if self.height else 0.0

    @property
    def is_landscape(self) -> bool:
        return self.width > self.height

    def to_dict(self) -> Dict[str, float]:
        return {"width": self.width, "height": self.height}


@dataclass(frozen=True)
class BBox:
    """An axis-aligned bounding box, ``(x0, y0)`` top-left to ``(x1, y1)`` bottom-right.

    >>> a = BBox(0, 0, 10, 10)
    >>> b = BBox(5, 5, 15, 15)
    >>> a.intersects(b)
    True
    >>> a.union(b)
    BBox(x0=0.0, y0=0.0, x1=15.0, y1=15.0)
    """

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        # Normalise inverted boxes so downstream maths can assume x0<=x1, y0<=y1.
        x0, x1 = sorted((float(self.x0), float(self.x1)))
        y0, y1 = sorted((float(self.y0), float(self.y1)))
        object.__setattr__(self, "x0", x0)
        object.__setattr__(self, "x1", x1)
        object.__setattr__(self, "y0", y0)
        object.__setattr__(self, "y1", y1)

    # -- derived measurements -------------------------------------------------
    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> Point:
        return Point((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    @property
    def as_tuple(self) -> Tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    # -- relationships --------------------------------------------------------
    def union(self, other: "BBox") -> "BBox":
        """Smallest box containing both."""
        return BBox(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def intersection(self, other: "BBox") -> Optional["BBox"]:
        """Overlapping region, or ``None`` when disjoint."""
        x0, y0 = max(self.x0, other.x0), max(self.y0, other.y0)
        x1, y1 = min(self.x1, other.x1), min(self.y1, other.y1)
        if x0 >= x1 or y0 >= y1:
            return None
        return BBox(x0, y0, x1, y1)

    def intersects(self, other: "BBox") -> bool:
        return self.intersection(other) is not None

    def contains(self, other: "BBox", tolerance: float = 0.0) -> bool:
        return (
            self.x0 - tolerance <= other.x0
            and self.y0 - tolerance <= other.y0
            and self.x1 + tolerance >= other.x1
            and self.y1 + tolerance >= other.y1
        )

    def vertical_overlap(self, other: "BBox") -> float:
        """Fraction of the *shorter* box's height that overlaps vertically.

        This is the key test for "are these two text spans on the same line?".
        """
        overlap = min(self.y1, other.y1) - max(self.y0, other.y0)
        if overlap <= 0:
            return 0.0
        shorter = min(self.height, other.height)
        return overlap / shorter if shorter else 0.0

    def horizontal_overlap(self, other: "BBox") -> float:
        """Fraction of the *narrower* box's width that overlaps horizontally.

        Used to decide whether two lines belong to the same column.
        """
        overlap = min(self.x1, other.x1) - max(self.x0, other.x0)
        if overlap <= 0:
            return 0.0
        narrower = min(self.width, other.width)
        return overlap / narrower if narrower else 0.0

    def iou(self, other: "BBox") -> float:
        """Intersection over union, in ``0.0..1.0``."""
        inter = self.intersection(other)
        if inter is None:
            return 0.0
        denom = self.area + other.area - inter.area
        return inter.area / denom if denom else 0.0

    def expand(self, amount: float) -> "BBox":
        """Grow (or shrink, with a negative amount) the box on all sides."""
        return BBox(self.x0 - amount, self.y0 - amount, self.x1 + amount, self.y1 + amount)

    def scale(self, factor: float) -> "BBox":
        return BBox(self.x0 * factor, self.y0 * factor, self.x1 * factor, self.y1 * factor)

    def translate(self, dx: float = 0.0, dy: float = 0.0) -> "BBox":
        return BBox(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)

    # -- construction ---------------------------------------------------------
    @classmethod
    def from_tuple(cls, values: Sequence[float]) -> "BBox":
        x0, y0, x1, y1 = values
        return cls(x0, y0, x1, y1)

    @classmethod
    def bounding(cls, boxes: Iterable["BBox"]) -> Optional["BBox"]:
        """Union of many boxes, or ``None`` for an empty iterable."""
        result: Optional[BBox] = None
        for box in boxes:
            result = box if result is None else result.union(box)
        return result

    def to_dict(self) -> Dict[str, float]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["BBox"]:
        if not data:
            return None
        return cls(data["x0"], data["y0"], data["x1"], data["y1"])


@dataclass(frozen=True)
class PageGeometry:
    """Physical description of one page or slide."""

    size: Size
    margin_left: float = 72.0
    margin_right: float = 72.0
    margin_top: float = 72.0
    margin_bottom: float = 72.0
    rotation: int = 0

    #: Common page sizes in points, for writers that need a default.
    A4 = Size(595.28, 841.89)
    LETTER = Size(612.0, 792.0)
    LEGAL = Size(612.0, 1008.0)
    SLIDE_16_9 = Size(960.0, 540.0)
    SLIDE_4_3 = Size(720.0, 540.0)

    @property
    def content_box(self) -> BBox:
        """The printable region inside the margins."""
        return BBox(
            self.margin_left,
            self.margin_top,
            self.size.width - self.margin_right,
            self.size.height - self.margin_bottom,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "size": self.size.to_dict(),
            "margin_left": self.margin_left,
            "margin_right": self.margin_right,
            "margin_top": self.margin_top,
            "margin_bottom": self.margin_bottom,
            "rotation": self.rotation,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["PageGeometry"]:
        if not data:
            return None
        size = data.get("size") or {}
        return cls(
            size=Size(size.get("width", 612.0), size.get("height", 792.0)),
            margin_left=data.get("margin_left", 72.0),
            margin_right=data.get("margin_right", 72.0),
            margin_top=data.get("margin_top", 72.0),
            margin_bottom=data.get("margin_bottom", 72.0),
            rotation=data.get("rotation", 0),
        )
