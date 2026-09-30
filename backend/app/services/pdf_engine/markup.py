"""Markup v7 drawing (EDITOR-V2-PLAN §3 "Markup", §4 `markup.py`; B2).

The v7 kinds -- `textbox`, `stamp`, `image2`, `shape2`, `ink2`,
`square_mark` -- are drawn with the engine (PyMuPDF, through `adapter`), on
top of whatever the page already carries. The legacy v4-v6 kinds stay with the
reportlab overlay (`pdf_builder`), byte for byte.

Geometry: PDF points, TOP-LEFT origin, UNROTATED page space relative to the
CropBox (the text model's space, §0.2). `rotation_deg` turns an element about
its own box centre, clockwise as displayed: the element is drawn upright on a
scratch page of its own size and placed with `show_pdf_page(rotate=...)`.

Layers (§4 "Nima qayerda"): `UNDER_KINDS` are drawn below signatures (none of
the v7 kinds is -- a covering element is still only the legacy `whiteout`);
every other v7 kind is drawn above them.

Colours: the closed palette of contract v5 (`ANNOTATION_PALETTE_HEX`, no
white). Opacity 0.05..1 (schema-checked).
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass

from PIL import Image

from app.services import fonts as app_fonts
from app.services.pdf_engine import adapter

UNDER_KINDS: frozenset[str] = frozenset()
V7_KINDS = frozenset({"textbox", "stamp", "image2", "shape2", "ink2", "square_mark"})

# The standard 14 as PyMuPDF names them.
_BASE14 = {
    "Helvetica": "helv", "Helvetica-Bold": "hebo", "Helvetica-Oblique": "heit", "Helvetica-BoldOblique": "hebi",
    "Times-Roman": "tiro", "Times-Bold": "tibo", "Times-Italic": "tiit", "Times-BoldItalic": "tibi",
    "Courier": "cour", "Courier-Bold": "cobo", "Courier-Oblique": "coit", "Courier-BoldOblique": "cobi",
}
STAMP_LABELS = {preset: preset.upper() for preset in (
    "approved", "rejected", "draft", "confidential", "final", "received", "void", "paid", "reviewed",
)}
ARROW_HEAD_LENGTH_RATIO = 6.0
ARROW_HEAD_HALF_WIDTH_RATIO = 3.0


def palette_rgb(name: str | None, default: str = "black") -> tuple[float, float, float]:
    from app.services.pdf_builder import ANNOTATION_PALETTE

    return ANNOTATION_PALETTE.get(name or default, ANNOTATION_PALETTE[default])


def font_ref(family: str, bold: bool = False, italic: bool = False) -> str:
    face = app_fonts.resolve_font(family, bold, italic)
    if app_fonts.is_embedded(face):
        return str(app_fonts.EMBEDDED_FONT_FILES[face])
    return adapter.BASE14_PREFIX + _BASE14[face]


def _ascender(ref: str) -> float:
    ascender, _descender = adapter.font_metrics(ref)
    return ascender if ascender > 0 else 0.8


@dataclass
class Placement:
    """A v7 element with its 0-based page index (and, for `image2`, the PNG)."""

    element: dict
    page_index: int
    image_bytes: bytes | None = None


def draw_markup(pdf_bytes: bytes, placements: list[Placement]) -> bytes:
    """Draw the elements in list order onto a copy of `pdf_bytes`."""
    if not placements:
        return pdf_bytes
    with adapter.Doc(pdf_bytes) as doc:
        for placement in placements:
            draw_element(doc, placement.page_index, placement.element, placement.image_bytes)
        return doc.save(subset_fonts=True)


def draw_element(doc: adapter.Doc, index: int, element: dict, image_bytes: bytes | None = None) -> None:
    kind = element["kind"]
    rotation = float(element.get("rotation_deg") or 0.0) % 360.0
    if kind in ("textbox", "stamp", "image2") and rotation:
        _draw_rotated(doc, index, element, image_bytes, rotation)
        return
    _DRAWERS[kind](doc, index, element, image_bytes, (0.0, 0.0))


def _draw_rotated(doc, index, element, image_bytes, rotation: float) -> None:
    x, y, w, h = (float(element[k]) for k in ("x_pt", "y_pt", "w_pt", "h_pt"))
    local = {**element, "x_pt": 0.0, "y_pt": 0.0, "rotation_deg": 0.0}
    with adapter.Doc.blank(w, h) as scratch:
        _DRAWERS[element["kind"]](scratch, 0, local, image_bytes, (0.0, 0.0))
        scratch_bytes = scratch.save(subset_fonts=True)
    # The rotated box's bounding box, centred on the element's centre.
    rad = math.radians(rotation)
    bw = abs(w * math.cos(rad)) + abs(h * math.sin(rad))
    bh = abs(w * math.sin(rad)) + abs(h * math.cos(rad))
    cx, cy = x + w / 2.0, y + h / 2.0
    with adapter.Doc(scratch_bytes) as source:
        # show_pdf_page turns counter-clockwise for a positive angle.
        doc.show_page_rotated(index, (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2), source, -rotation)


# -- textbox --------------------------------------------------------------------------


@dataclass
class _Piece:
    text: str
    ref: str
    size: float
    color: tuple[float, float, float]
    width: float


def _words(runs: list[dict]) -> list[list[_Piece]]:
    """Paragraphs (split at `\\n`) of pieces; a piece = a word or a space run
    in one style."""
    paragraphs: list[list[_Piece]] = [[]]
    for run in runs:
        ref = font_ref(run.get("font", "LiberationSans"), bool(run.get("bold")), bool(run.get("italic")))
        size = float(run.get("size_pt", 11.0))
        color = palette_rgb(run.get("color"))
        for number, chunk in enumerate(str(run["text"]).split("\n")):
            if number:
                paragraphs.append([])
            token = ""
            for char in chunk:
                if token and (char == " ") != (token[-1] == " "):
                    paragraphs[-1].append(_Piece(token, ref, size, color, adapter.text_length(ref, token, size)))
                    token = ""
                token += char
            if token:
                paragraphs[-1].append(_Piece(token, ref, size, color, adapter.text_length(ref, token, size)))
    return paragraphs


def layout_textbox(element: dict) -> list[tuple[float, float, list[_Piece]]]:
    """[(baseline_y, line_width, pieces)] relative to the box's top-left:
    greedy word wrap at the box width; `line_height` x the line's largest size."""
    width = float(element["w_pt"])
    line_height = float(element.get("line_height", 1.2))
    lines: list[tuple[float, float, list[_Piece]]] = []
    y = 0.0
    for paragraph in _words(element["runs"]):
        current: list[_Piece] = []
        current_width = 0.0
        rows: list[list[_Piece]] = []
        for piece in paragraph:
            is_space = piece.text.startswith(" ")
            if current and not is_space and current_width + piece.width > width + 1e-6:
                rows.append(current)
                current, current_width = [], 0.0
            if not current and is_space:
                continue  # no leading space on a wrapped line
            current.append(piece)
            current_width += piece.width
        rows.append(current)
        for row in rows:
            while row and row[-1].text.startswith(" "):
                row = row[:-1]
            size = max((p.size for p in row), default=float(element["runs"][0].get("size_pt", 11.0)))
            ascent = max((_ascender(p.ref) * p.size for p in row), default=0.8 * size)
            baseline = y + (ascent if not lines else size * line_height)
            if lines:
                baseline = lines[-1][0] + size * line_height
            lines.append((baseline, sum(p.width for p in row), row))
            y = baseline
    return lines


def _draw_textbox(doc, index, element, _image, _offset) -> None:
    x0, y0, width = float(element["x_pt"]), float(element["y_pt"]), float(element["w_pt"])
    align = element.get("align", "left")
    opacity = float(element.get("opacity", 1.0))
    for baseline, line_width, pieces in layout_textbox(element):
        if align == "center":
            x = x0 + (width - line_width) / 2.0
        elif align == "right":
            x = x0 + width - line_width
        else:  # left; justify is set ragged-right (documented)
            x = x0
        for piece in pieces:
            if piece.text.strip():
                doc.draw_text(index, x, y0 + baseline, piece.text, piece.ref, piece.size, piece.color, opacity)
            x += piece.width


# -- stamp ------------------------------------------------------------------------------


def _fit_size(ref: str, text: str, max_width: float, max_size: float) -> float:
    unit = adapter.text_length(ref, text, 1.0) or 1.0
    return max(4.0, min(max_size, max_width / unit))


def _draw_stamp(doc, index, element, _image, _offset) -> None:
    x, y, w, h = (float(element[k]) for k in ("x_pt", "y_pt", "w_pt", "h_pt"))
    color = palette_rgb(element.get("color"), "red")
    opacity = float(element.get("opacity", 1.0))
    border = max(1.0, min(w, h) * 0.06)
    if element.get("border", True):
        inset = border / 2.0
        doc.draw_rect(index, (x + inset, y + inset, x + w - inset, y + h - inset), stroke=color,
                      width=border, opacity=opacity, radius=0.12)
    label = element.get("text") or STAMP_LABELS.get(element.get("preset") or "", "")
    pad = border * 2.5
    bold = adapter.BASE14_PREFIX + "hebo"
    regular = adapter.BASE14_PREFIX + "helv"
    date_text = element.get("date_text")
    main_h = h * (0.62 if date_text else 1.0)
    size = _fit_size(bold, label, w - 2 * pad, main_h * 0.55)
    text_w = adapter.text_length(bold, label, size)
    ascent = _ascender(bold) * size
    baseline = y + (main_h + ascent * 0.72) / 2.0 if not date_text else y + main_h * 0.5 + ascent * 0.36 + pad * 0.3
    doc.draw_text(index, x + (w - text_w) / 2.0, baseline, label, bold, size, color, opacity)
    if date_text:
        date_size = _fit_size(regular, date_text, w - 2 * pad, (h - main_h) * 0.6)
        date_w = adapter.text_length(regular, date_text, date_size)
        date_baseline = y + h - pad * 0.6 - (h - main_h - date_size) / 2.0
        doc.draw_text(index, x + (w - date_w) / 2.0, date_baseline, date_text, regular, date_size, color, opacity)


# -- image2 -------------------------------------------------------------------------------


def with_opacity(png: bytes, opacity: float) -> bytes:
    if opacity >= 1.0:
        return png
    with Image.open(io.BytesIO(png)) as image:
        rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A").point(lambda v: int(round(v * opacity)))
    rgba.putalpha(alpha)
    buffer = io.BytesIO()
    rgba.save(buffer, format="PNG")
    return buffer.getvalue()


def _draw_image2(doc, index, element, image_bytes, _offset) -> None:
    if not image_bytes:  # pragma: no cover - callers resolve every asset first
        raise ValueError("image2 without image bytes")
    x, y, w, h = (float(element[k]) for k in ("x_pt", "y_pt", "w_pt", "h_pt"))
    doc.draw_image(index, (x, y, x + w, y + h), with_opacity(image_bytes, float(element.get("opacity", 1.0))))


# -- shape2 / ink2 / square_mark ---------------------------------------------------------


def _dashes(dash: str, width: float) -> tuple[str | None, int]:
    if dash == "dash":
        return f"[{3 * width:g} {2 * width:g}] 0", 0
    if dash == "dot":
        return f"[0 {2 * width:g}] 0", 1
    return None, 0


def _head(tip, tail, stroke: float):
    """(base, left, right) of an arrow head with its tip at `tip`."""
    dx, dy = tip[0] - tail[0], tip[1] - tail[1]
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    head_length = min(ARROW_HEAD_LENGTH_RATIO * stroke, length)
    half = ARROW_HEAD_HALF_WIDTH_RATIO * stroke * (head_length / (ARROW_HEAD_LENGTH_RATIO * stroke))
    base = (tip[0] - ux * head_length, tip[1] - uy * head_length)
    return base, (base[0] - uy * half, base[1] + ux * half), (base[0] + uy * half, base[1] - ux * half)


def _draw_shape2(doc, index, element, _image, _offset) -> None:
    stroke = palette_rgb(element.get("stroke_color"))
    fill = palette_rgb(element["fill_color"]) if element.get("fill_color") else None
    width = float(element.get("stroke_pt", 2.0))
    opacity = float(element.get("opacity", 1.0))
    dashes, cap = _dashes(element.get("dash", "solid"), width)
    shape = element["shape"]
    if shape in ("rect", "ellipse"):
        x, y, w, h = (float(element[k]) for k in ("x_pt", "y_pt", "w_pt", "h_pt"))
        draw = doc.draw_rect if shape == "rect" else doc.draw_oval
        draw(index, (x, y, x + w, y + h), stroke=stroke, fill=fill, width=width, opacity=opacity,
             dashes=dashes, line_cap=cap)
        return
    p1 = (float(element["x1_pt"]), float(element["y1_pt"]))
    p2 = (float(element["x2_pt"]), float(element["y2_pt"]))
    heads = element.get("heads") or ("end" if shape == "arrow" else "none")
    start, end = p1, p2
    triangles = []
    if heads in ("end", "both"):
        base, left, right = _head(p2, p1, width)
        end = base
        triangles.append((p2, left, right))
    if heads in ("start", "both"):
        base, left, right = _head(p1, p2, width)
        start = base
        triangles.append((p1, left, right))
    doc.draw_polyline(index, [start, end], stroke=stroke, width=width, opacity=opacity, dashes=dashes,
                      line_cap=cap)
    for triangle in triangles:
        doc.draw_polyline(index, list(triangle), fill=stroke, opacity=opacity, closed=True)


def _draw_ink2(doc, index, element, _image, _offset) -> None:
    from app.schemas.contract import FREEHAND_HIGHLIGHT_OPACITY

    color = palette_rgb(element.get("color"))
    width = float(element.get("stroke_pt", 2.0))
    opacity = element.get("opacity")
    if opacity is None:
        opacity = FREEHAND_HIGHLIGHT_OPACITY if element.get("freehand_highlight") else 1.0
    for path in element["paths"]:
        points = [(float(p["x_pt"]), float(p["y_pt"])) for p in path]
        doc.draw_polyline(index, points, stroke=color, width=width, opacity=float(opacity))


def _draw_square_mark(doc, index, element, _image, _offset) -> None:
    size = float(element.get("size_pt", 10.0))
    x, y = float(element["x_pt"]), float(element["y_pt"])
    half = size / 2.0
    doc.draw_rect(index, (x - half, y - half, x + half, y + half), fill=palette_rgb(element.get("color")))


_DRAWERS = {
    "textbox": _draw_textbox,
    "stamp": _draw_stamp,
    "image2": _draw_image2,
    "shape2": _draw_shape2,
    "ink2": _draw_ink2,
    "square_mark": _draw_square_mark,
}


ROTATED_KINDS = frozenset({"textbox", "stamp", "image2"})  # `draw_element` turns these


def rotated_corners(x: float, y: float, w: float, h: float, rotation: float) -> list[tuple[float, float]]:
    """The four corners of the box turned `rotation` degrees about its centre,
    clockwise as displayed (top-left origin, y down) -- the footprint
    `_draw_rotated` draws into."""
    rad = math.radians(rotation)
    cos, sin = math.cos(rad), math.sin(rad)
    cx, cy = x + w / 2.0, y + h / 2.0
    out = []
    for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        out.append((cx + dx * cos - dy * sin, cy + dx * sin + dy * cos))
    return out


def element_points(element: dict) -> list[tuple[float, float]]:
    """Every point that must lie on the page (the same rule as the legacy
    kinds: boxes whole, lines both ends, ink every point, a mark its centre).
    A turned textbox / stamp / image2 is checked by its TURNED corners (what
    is drawn), not by its upright box (2026-09-30: a box near the edge of a
    /Rotate page was refused although everything drawn lay on the page)."""
    kind = element["kind"]
    if kind in ("textbox", "stamp", "image2") or (kind == "shape2" and element["shape"] in ("rect", "ellipse")):
        x, y, w, h = (float(element[k]) for k in ("x_pt", "y_pt", "w_pt", "h_pt"))
        rotation = float(element.get("rotation_deg") or 0.0) % 360.0
        if kind in ROTATED_KINDS and rotation:
            return rotated_corners(x, y, w, h, rotation)
        return [(x, y), (x + w, y + h)]
    if kind == "shape2":
        return [(float(element["x1_pt"]), float(element["y1_pt"])), (float(element["x2_pt"]), float(element["y2_pt"]))]
    if kind == "ink2":
        return [(float(p["x_pt"]), float(p["y_pt"])) for path in element["paths"] for p in path]
    return [(float(element["x_pt"]), float(element["y_pt"]))]
