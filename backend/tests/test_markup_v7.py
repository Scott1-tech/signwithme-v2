"""Markup v7 (editor v2 B2): schema, drawing (pdfium 8 px/pt), review layers."""
from __future__ import annotations

import io

import pytest
from PIL import Image
from pydantic import ValidationError

from app.schemas.contract import ANNOTATION_MODELS
from app.services.pdf_engine.markup import Placement, draw_markup, layout_textbox
from tests.editor_v2_helpers import all_extractors, compact, reportlab_pdf

SCALE = 8.0
PALETTE = {"black": (0, 0, 0), "blue": (29, 78, 216), "red": (220, 38, 38), "green": (22, 163, 74)}


def _blank() -> bytes:
    return reportlab_pdf(lambda pdf, _i: None)


def _render(pdf_bytes: bytes, scale: float = SCALE):
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_bytes)
    try:
        page = document[0]
        bitmap = page.render(scale=scale)
        try:
            return bitmap.to_pil().convert("RGB")
        finally:
            bitmap.close()
            page.close()
    finally:
        document.close()


def _bbox(image, ranges, scale: float = SCALE):
    """(x0, y0, x1, y1) in points of the pixels whose R, G, B each fall in
    `ranges` ((lo, hi) per channel) -- a C-speed mask, then `getbbox`."""
    from PIL import ImageChops

    mask = None
    for channel, (lo, hi) in zip(image.split(), ranges):
        part = channel.point(lambda v, lo=lo, hi=hi: 255 if lo <= v <= hi else 0)
        mask = part if mask is None else ImageChops.multiply(mask, part)
    box = mask.getbbox()
    if box is None:
        return None
    return (box[0] / scale, box[1] / scale, box[2] / scale, box[3] / scale)


def _near(rgb, tolerance=40):
    return tuple((max(0, c - tolerance), min(255, c + tolerance)) for c in rgb)


DARK = ((0, 99), (0, 99), (0, 99))


def _close(pixel, rgb, tolerance=40):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, rgb))


def _v(kind: str, **fields) -> dict:
    return ANNOTATION_MODELS[kind].model_validate({"id": "e1", "kind": kind, "page": 1, **fields}).model_dump(
        mode="json"
    )


def _draw(*elements, images=None) -> bytes:
    return draw_markup(_blank(), [Placement(e, 0, (images or {}).get(e["id"])) for e in elements])


# -- schema ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind, fields",
    [
        ("textbox", {"x_pt": 1, "y_pt": 1, "w_pt": 10, "h_pt": 10, "runs": []}),
        ("textbox", {"x_pt": 1, "y_pt": 1, "w_pt": 10, "h_pt": 10, "runs": [{"text": "x", "color": "white"}]}),
        ("textbox", {"x_pt": 1, "y_pt": 1, "w_pt": 10, "h_pt": 10, "runs": [{"text": "x"}], "opacity": 0.01}),
        ("textbox", {"x_pt": 1, "y_pt": 1, "w_pt": 10, "h_pt": 10, "runs": [{"text": "😀"}]}),
        ("textbox", {"x_pt": 1, "y_pt": 1, "w_pt": 10, "h_pt": 10, "runs": [{"text": "Привет", "font": "Helvetica"}]}),
        ("textbox", {"x_pt": 1, "y_pt": 1, "w_pt": 10, "h_pt": 10, "runs": [{"text": "x", "font": "Arial"}]}),
        ("stamp", {"x_pt": 1, "y_pt": 1, "w_pt": 10, "h_pt": 10}),
        ("stamp", {"x_pt": 1, "y_pt": 1, "w_pt": 10, "h_pt": 10, "preset": "approved", "text": "X"}),
        ("stamp", {"x_pt": 1, "y_pt": 1, "w_pt": 10, "h_pt": 10, "preset": "nope"}),
        ("stamp", {"x_pt": 1, "y_pt": 1, "w_pt": 10, "h_pt": 10, "preset": "paid", "date_text": "  "}),
        ("image2", {"x_pt": 1, "y_pt": 1, "w_pt": 10, "h_pt": 10, "asset_id": "nothex"}),
        ("shape2", {"shape": "rect", "x1_pt": 1, "y1_pt": 1, "x2_pt": 5, "y2_pt": 5}),
        ("shape2", {"shape": "line", "x1_pt": 1, "y1_pt": 1, "x2_pt": 1.2, "y2_pt": 1}),
        ("shape2", {"shape": "line", "x1_pt": 1, "y1_pt": 1, "x2_pt": 9, "y2_pt": 1, "fill_color": "red"}),
        ("shape2", {"shape": "rect", "x_pt": 1, "y_pt": 1, "w_pt": 5, "h_pt": 5, "heads": "end"}),
        ("shape2", {"shape": "rect", "x_pt": 1, "y_pt": 1, "w_pt": 5, "h_pt": 5, "dash": "dashdot"}),
        ("ink2", {"paths": [[{"x_pt": 1, "y_pt": 1}]]}),
        ("square_mark", {"x_pt": 1, "y_pt": 1, "size_pt": 100}),
    ],
)
def test_v7_schema_refuses(kind, fields):
    with pytest.raises(ValidationError):
        ANNOTATION_MODELS[kind].model_validate({"id": "e1", "kind": kind, "page": 1, **fields})


def test_v7_defaults():
    stamp = _v("stamp", x_pt=1, y_pt=1, w_pt=10, h_pt=10, preset="approved")
    assert stamp["color"] == "red" and stamp["border"] is True and stamp["date_text"] is None
    ink = ANNOTATION_MODELS["ink2"].model_validate(
        {"id": "i", "kind": "ink2", "page": 1, "paths": [[{"x_pt": 1, "y_pt": 1}, {"x_pt": 2, "y_pt": 2}]],
         "freehand_highlight": True})
    assert ink.effective_opacity == 0.35
    arrow = ANNOTATION_MODELS["shape2"].model_validate(
        {"id": "a", "kind": "shape2", "page": 1, "shape": "arrow", "x1_pt": 1, "y1_pt": 1, "x2_pt": 50, "y2_pt": 1})
    assert arrow.effective_heads == "end"


# -- drawing ----------------------------------------------------------------------------------


def test_textbox_wraps_inside_its_box_and_is_real_text():
    element = _v("textbox", x_pt=100, y_pt=100, w_pt=150, h_pt=80, line_height=1.2,
                 runs=[{"text": "Contractor agrees to the terms written here", "size_pt": 12,
                        "font": "LiberationSans", "color": "blue"}])
    lines = layout_textbox(element)
    assert len(lines) >= 2 and all(width <= 150 + 1e-6 for _b, width, _p in lines)
    pdf = _draw(element)
    for text in all_extractors(pdf):
        assert "Contractoragreestothetermswrittenhere" in compact(text)
    ink = _bbox(_render(pdf), _near(PALETTE["blue"], 60))
    assert ink is not None
    assert ink[0] >= 99.5 and ink[2] <= 250.5 and ink[1] >= 100.0


def test_textbox_center_and_right_alignment():
    left = _bbox(_render(_draw(_v("textbox", x_pt=100, y_pt=100, w_pt=300, h_pt=40,
                                     runs=[{"text": "Mid", "size_pt": 20}]))), DARK)
    center = _bbox(_render(_draw(_v("textbox", x_pt=100, y_pt=100, w_pt=300, h_pt=40, align="center",
                                       runs=[{"text": "Mid", "size_pt": 20}]))), DARK)
    right = _bbox(_render(_draw(_v("textbox", x_pt=100, y_pt=100, w_pt=300, h_pt=40, align="right",
                                      runs=[{"text": "Mid", "size_pt": 20}]))), DARK)
    width = left[2] - left[0]
    assert left[0] == pytest.approx(100, abs=2.5)
    assert (center[0] + center[2]) / 2 == pytest.approx(250, abs=2.5)
    assert right[2] == pytest.approx(400, abs=2.5)
    assert (right[2] - right[0]) == pytest.approx(width, abs=0.5)


def test_textbox_rotated_90_turns_about_the_box_centre():
    element = _v("textbox", x_pt=200, y_pt=300, w_pt=200, h_pt=40, rotation_deg=90,
                 runs=[{"text": "ROTATEDROTATED", "size_pt": 18}])
    ink = _bbox(_render(_draw(element)), DARK)
    # Box centre (300, 320); a 200 x 40 box turned 90 deg is 40 x 200 there.
    assert 280 - 1 <= ink[0] and ink[2] <= 340 + 1
    assert 220 - 1 <= ink[1] and ink[3] <= 420 + 1
    assert (ink[3] - ink[1]) > (ink[2] - ink[0])


def test_stamp_preset_border_colour_and_manual_date():
    element = _v("stamp", x_pt=100, y_pt=100, w_pt=180, h_pt=70, preset="approved", color="green",
                 date_text="09/29/2026")
    pdf = _draw(element)
    image = _render(pdf)
    ink = _bbox(image, _near(PALETTE["green"], 60))
    assert ink == pytest.approx((100, 100, 280, 170), abs=1.0)
    texts = compact(all_extractors(pdf)[0])
    assert "APPROVED" in texts and "09/29/2026" in texts


def test_stamp_has_no_date_unless_typed():
    pdf = _draw(_v("stamp", x_pt=100, y_pt=100, w_pt=180, h_pt=60, text="RECEIVED"))
    text = all_extractors(pdf)[0]
    assert "RECEIVED" in text and not any(ch.isdigit() for ch in text)


def _png(color, size=(40, 20)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_image2_fills_its_box_with_opacity():
    element = _v("image2", x_pt=100, y_pt=100, w_pt=120, h_pt=60, asset_id="a" * 32, opacity=0.5)
    image = _render(_draw(element, images={"e1": _png((255, 0, 0, 255))}))
    box = _bbox(image, ((201, 255), (0, 199), (0, 255)))
    assert box == pytest.approx((100, 100, 220, 160), abs=0.5)
    pixel = image.getpixel((int(160 * SCALE), int(130 * SCALE)))
    assert 110 <= pixel[1] <= 145 and pixel[0] > 245  # 50 % red over white


def test_shape2_rect_fill_dash_and_arrow_heads():
    rect = _v("shape2", shape="rect", x_pt=100, y_pt=100, w_pt=100, h_pt=50, stroke_color="blue",
              fill_color="green", opacity=0.5, stroke_pt=4, dash="dash")
    image = _render(_draw(rect))
    inner = image.getpixel((int(150 * SCALE), int(125 * SCALE)))
    assert inner[1] > inner[0] and inner[1] > inner[2] and min(inner) > 60  # translucent green
    top_edge = [image.getpixel((int(x * SCALE), int(98.8 * SCALE))) for x in range(100, 200)]
    gaps = sum(1 for p in top_edge if min(p) > 200)
    assert gaps > 10, "dashed"
    arrow = _v("shape2", shape="arrow", x1_pt=100, y1_pt=300, x2_pt=300, y2_pt=300, stroke_pt=2, heads="both",
               stroke_color="red")
    ink = _bbox(_render(_draw(arrow)), _near(PALETTE["red"], 60))
    assert ink[0] == pytest.approx(100, abs=0.5) and ink[2] == pytest.approx(300, abs=0.5)
    assert ink[3] - ink[1] == pytest.approx(12, abs=1.0)  # head half-width 3 x stroke on both sides


def test_ink2_freehand_highlight_is_translucent_and_square_mark_is_filled():
    ink = _v("ink2", paths=[[{"x_pt": 100, "y_pt": 100}, {"x_pt": 300, "y_pt": 100}]], stroke_pt=10,
             color="blue", freehand_highlight=True)
    square = _v("square_mark", x_pt=400, y_pt=400, size_pt=12, color="black")
    square["id"] = "sq"
    image = _render(_draw(ink, square))
    pixel = image.getpixel((int(200 * SCALE), int(100 * SCALE)))
    assert pixel[2] > 200 and pixel[0] > 120  # 35 % blue over white
    box = _bbox(image, ((0, 39), (0, 39), (0, 39)))
    assert box == pytest.approx((394, 394, 406, 406), abs=0.3)


def test_embedded_family_text_in_a_textbox():
    element = _v("textbox", x_pt=72, y_pt=72, w_pt=400, h_pt=40,
                 runs=[{"text": "Oʻzbekiston Привет", "font": "Carlito", "size_pt": 14}])
    pdf = _draw(element)
    assert "Oʻzbekiston" in all_extractors(pdf)[0]
    from pypdf import PdfReader

    fonts = PdfReader(io.BytesIO(pdf)).pages[0]["/Resources"]["/Font"]
    names = [str(fonts[k].get_object()["/BaseFont"]) for k in fonts]
    assert any("Carlito" in name for name in names)
