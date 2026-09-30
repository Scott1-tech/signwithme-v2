"""Canonical geometry for the whole system.

There are three coordinate conventions in play:

* pdfplumber  -- points, origin top-left, boxes as (x0, top, x1, bottom)
* reportlab   -- points, origin bottom-left, boxes as (x, y, width, height)
* the browser -- CSS pixels, origin top-left, scaled by the viewer's zoom

Everything crossing an API or database boundary uses the canonical form below:
points, origin TOP-LEFT, {"x", "y", "width", "height"}. Conversion happens only
at the two edges -- `to_reportlab` when drawing overlays, and `scale` when the
frontend renders highlights.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BBox:
    x: float
    y: float
    width: float
    height: float

    @property
    def x1(self) -> float:
        return self.x + self.width

    @property
    def y1(self) -> float:
        return self.y + self.height

    @classmethod
    def from_pdfplumber(cls, x0: float, top: float, x1: float, bottom: float) -> "BBox":
        return cls(x=float(x0), y=float(top), width=float(x1 - x0), height=float(bottom - top))

    @classmethod
    def from_dict(cls, data: dict) -> "BBox":
        if data is None:
            raise ValueError("bbox is required")
        if "width" in data and "height" in data:
            return cls(
                x=float(data["x"]),
                y=float(data["y"]),
                width=float(data["width"]),
                height=float(data["height"]),
            )
        # Tolerate the (x0, y0, x1, y1) form emitted by older detector output.
        return cls.from_pdfplumber(data["x0"], data["y0"], data["x1"], data["y1"])

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    def to_reportlab(self, page_height: float) -> tuple[float, float, float, float]:
        """Return (x, y, width, height) with a bottom-left origin, as reportlab draws."""
        return (self.x, page_height - self.y - self.height, self.width, self.height)

    def scale(self, factor: float) -> "BBox":
        return BBox(
            x=self.x * factor,
            y=self.y * factor,
            width=self.width * factor,
            height=self.height * factor,
        )

    def union(self, other: "BBox") -> "BBox":
        x0 = min(self.x, other.x)
        y0 = min(self.y, other.y)
        return BBox(x=x0, y=y0, width=max(self.x1, other.x1) - x0, height=max(self.y1, other.y1) - y0)

    def expand_right(self, amount: float) -> "BBox":
        return BBox(x=self.x, y=self.y, width=self.width + amount, height=self.height)

    def overlaps(self, other: "BBox") -> bool:
        return not (
            self.x1 <= other.x or other.x1 <= self.x or self.y1 <= other.y or other.y1 <= self.y
        )


def unrotated_to_displayed(x: float, y: float, width: float, height: float, rotation: int) -> tuple[float, float]:
    """A point of the UNROTATED page (top-left origin; `width`/`height` the
    unrotated size) -> the same point as DISPLAYED with /Rotate `rotation`
    (clockwise), top-left origin of the displayed page."""
    rotation %= 360
    if rotation == 0:
        return x, y
    if rotation == 90:
        return height - y, x
    if rotation == 180:
        return width - x, height - y
    if rotation == 270:
        return y, width - x
    raise ValueError(f"Unsupported page rotation: {rotation}")


def displayed_to_unrotated(x: float, y: float, width: float, height: float, rotation: int) -> tuple[float, float]:
    """Inverse of `unrotated_to_displayed`."""
    rotation %= 360
    if rotation == 0:
        return x, y
    if rotation == 90:
        return y, height - x
    if rotation == 180:
        return width - x, height - y
    if rotation == 270:
        return width - y, x
    raise ValueError(f"Unsupported page rotation: {rotation}")


def remap_displayed_box(box: BBox, width: float, height: float, from_rotation: int, to_rotation: int) -> BBox:
    """A box drawn on the page displayed at `from_rotation` -> the box on the
    same content when the page is displayed at `to_rotation` (editor v2
    `page.rotate`). `width`/`height`: the UNROTATED page size."""
    if from_rotation % 360 == to_rotation % 360:
        return box
    corners = [
        unrotated_to_displayed(*displayed_to_unrotated(px, py, width, height, from_rotation), width, height, to_rotation)
        for px, py in ((box.x, box.y), (box.x1, box.y1))
    ]
    (ax, ay), (bx, by) = corners
    return BBox(min(ax, bx), min(ay, by), abs(bx - ax), abs(by - ay))


def rotate_for_page(bbox: BBox, page_width: float, page_height: float, rotation: int) -> BBox:
    """Map a canonical (unrotated, top-left origin) box onto a page with /Rotate set.

    Returns a box in the rotated page's own coordinate space, still top-left origin.
    """
    rotation = rotation % 360
    if rotation == 0:
        return bbox
    if rotation == 90:
        return BBox(x=page_height - bbox.y - bbox.height, y=bbox.x, width=bbox.height, height=bbox.width)
    if rotation == 180:
        return BBox(
            x=page_width - bbox.x - bbox.width,
            y=page_height - bbox.y - bbox.height,
            width=bbox.width,
            height=bbox.height,
        )
    if rotation == 270:
        return BBox(x=bbox.y, y=page_width - bbox.x - bbox.width, width=bbox.height, height=bbox.width)
    raise ValueError(f"Unsupported page rotation: {rotation}")
