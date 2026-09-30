"""«Detect form fields» (EDITOR-V2-PLAN §6 `POST .../detect-fields`; B3).

The template field detector (`field_detector.FieldDetector`) run on pages of
a document version, its regions turned into `field.add`-ready suggestions:

    text_line, date      -> text
    signature            -> signature
    checkbox             -> checkbox
    checkbox_group       -> one checkbox per option
    table                -> (none: a table is not one field)

Geometry: the detector reports pdfplumber boxes -- top-left of the DISPLAYED
MediaBox; a suggestion's `rect` is in field geometry (unrotated, top-left of
the CropBox), like every op. Regions already covered by a visible widget of
the page are left out. Nothing is stored; `name` is a free, valid field name.
"""
from __future__ import annotations

import hashlib
import re

from app.services.field_detector import FieldDetector
from app.services.geometry import displayed_to_unrotated

_TYPES = {"text_line": "text", "date": "text", "signature": "signature", "checkbox": "checkbox"}
_CONFIDENCE = {
    ("signature", "text"): 0.85,
    ("date", "text"): 0.8,
    ("text_line", "text"): 0.75,
    ("checkbox", "checkbox_glyph"): 0.9,
    ("checkbox", "checkbox_rect"): 0.6,
    ("checkbox_group", "checkbox_glyph"): 0.8,
}
_NAME_SAFE = re.compile(r"[^A-Za-z0-9_.\-]+")
OVERLAP_SHARE = 0.3


def _to_field_rect(box: dict, geometry: dict) -> tuple[float, float, float, float] | None:
    """pdfplumber box (displayed MediaBox, top-left) -> (x0, y0, x1, y1) in
    field geometry, clipped to the page; None when nothing is left."""
    corners = [
        displayed_to_unrotated(x, y, geometry["media_w"], geometry["media_h"], geometry["rotation"])
        for x, y in ((box["x"], box["y"]), (box["x"] + box["width"], box["y"] + box["height"]))
    ]
    (ax, ay), (bx, by) = corners
    x0, x1 = sorted((ax, bx))
    y0, y1 = sorted((ay, by))
    x0, x1 = x0 - geometry["crop_x0"], x1 - geometry["crop_x0"]
    y0, y1 = y0 - geometry["crop_y0"], y1 - geometry["crop_y0"]
    width, height = geometry["crop_w"], geometry["crop_h"]
    x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(width, x1), min(height, y1)
    if x1 - x0 < 2.0 or y1 - y0 < 2.0:
        return None
    return x0, y0, x1, y1


def _overlaps(rect, widgets) -> bool:
    area = (rect[2] - rect[0]) * (rect[3] - rect[1])
    for w in widgets:
        ix = min(rect[2], w[2]) - max(rect[0], w[0])
        iy = min(rect[3], w[3]) - max(rect[1], w[1])
        if ix > 0 and iy > 0 and ix * iy >= OVERLAP_SHARE * min(area, (w[2] - w[0]) * (w[3] - w[1])):
            return True
    return False


def _name(label: str, field_id: str, taken: set[str]) -> str:
    base = _NAME_SAFE.sub("_", (label or "").strip().lower()).strip("_.-")[:48]
    if not base:
        base = _NAME_SAFE.sub("_", re.sub(r"^p\d+_", "", field_id or "")).strip("_.-")[:48] or "field"
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}_{n}"
    taken.add(name)
    return name


def _rect_out(rect) -> dict:
    x0, y0, x1, y1 = rect
    return {"x_pt": round(x0, 3), "y_pt": round(y0, 3), "w_pt": round(x1 - x0, 3), "h_pt": round(y1 - y0, 3)}


def suggest(pdf_bytes: bytes, pages: list[tuple[int, str]]) -> list[dict]:
    """`pages`: (1-based position, page_uid) of the pages to scan."""
    from app.services.pdf_engine import adapter
    from app.services.pdf_engine.forms import read_widgets

    geometry: dict[int, dict] = {}
    existing: dict[int, list] = {}
    with adapter.Doc(pdf_bytes) as doc:
        for position, _uid in pages:
            geo = doc.page_geometry(position - 1)
            geo["crop_w"], geo["crop_h"] = doc.page_size(position - 1)
            geometry[position] = geo
        taken = set()
        for widget in read_widgets(doc):
            taken.add(widget.name)
            if not widget.hidden:
                existing.setdefault(widget.index + 1, []).append(widget.rect)
    uid_of = dict(pages)
    regions = FieldDetector().analyze_pages(pdf_bytes, [position for position, _uid in pages])
    out: list[dict] = []
    seen: set[str] = set()
    for region in regions:
        items = []
        if region.field_type == "checkbox_group":
            for option in region.options or []:
                label = f"{region.label} {option.get('label') or ''}".strip()
                items.append(("checkbox", option["bbox"], label, option.get("id") or ""))
        elif region.field_type in _TYPES:
            items.append((_TYPES[region.field_type], region.bbox, region.label, region.field_id))
        for kind, box, label, field_id in items:
            rect = _to_field_rect(box, geometry[region.page])
            if rect is None or _overlaps(rect, existing.get(region.page, [])):
                continue
            page_uid = uid_of[region.page]
            key = f"{page_uid}|{kind}|" + ",".join(f"{v:.1f}" for v in rect)
            suggestion_id = "sg_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
            if suggestion_id in seen:
                continue
            seen.add(suggestion_id)
            out.append(
                {
                    "suggestion_id": suggestion_id,
                    "page_uid": page_uid,
                    "type": kind,
                    "rect": _rect_out(rect),
                    "label": (label or "")[:128],
                    "name": _name(label, field_id, taken),
                    "confidence": _CONFIDENCE.get((region.field_type, region.detector), 0.7),
                }
            )
    return out
