"""Placement math shared with the frontend (contract v2).

    line = rule_y ?? bbox.y + bbox.height
    signature ink bottom = line - SIGNATURE_INK_GAP - (signature_offset_pt + slot_offset)
    date baseline        = line - DATE_INK_GAP      - (date_offset_pt + slot_offset)
    signature left / date x = bbox.x + slot_offsets_x_pt[field_id]  (slots only)

Measured in the produced PDF, in canonical (top-left) points. Positive vertical
offsets move marks UP (smaller canonical y); positive dx moves them RIGHT.
"""
import io

import pdfplumber
import pytest
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.services.geometry import BBox
from app.services.pdf_builder import (
    DATE_INK_GAP,
    SIGNATURE_INK_GAP,
    DatePlacement,
    Layout,
    PageOutOfRangeError,
    PdfBuilder,
    SignaturePlacement,
    _ink_metrics,
)

WIDTH, HEIGHT = letter
TOL = 0.3
LINE = 500.0  # canonical y handed in as the stored rule_y
SIG_BOX = BBox(200, 460, 150, 34)
DATE_BOX = BBox(400, 480, 100, 14)


def _blank_pdf(pages: int = 1) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    for _ in range(pages):
        pdf.drawString(72, 720, "page")
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _png(width: int, height: int, ink_box=None) -> bytes:
    """RGBA; with no ink_box the ink fills the image edge to edge (= a B2-cropped
    capture: image bottom IS ink bottom)."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    left, top, right, bottom = ink_box or (0, 0, width, height)
    for x in range(left, right):
        for y in (top, bottom - 1):
            image.putpixel((x, y), (10, 10, 60, 255))
    for y in range(top, bottom):
        for x in (left, right - 1):
            image.putpixel((x, y), (10, 10, 60, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _image_rect(pdf_bytes: bytes) -> dict:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        images = pdf.pages[0].images
    assert len(images) == 1
    return {k: float(images[0][k]) for k in ("x0", "top", "x1", "bottom")}


def _sign(png: bytes, layout: Layout | None = None, *, rule_y=LINE, field_id="p1_sig", bbox=SIG_BOX):
    placement = SignaturePlacement(page=1, bbox=bbox, image_bytes=png, field_id=field_id, rule_y=rule_y)
    return PdfBuilder().build_final_contract(_blank_pdf(), [placement], [], layout=layout).pdf_bytes


def test_default_layout_puts_cropped_ink_one_gap_above_the_line():
    rect = _image_rect(_sign(_png(300, 80)))
    assert abs(rect["bottom"] - (LINE - SIGNATURE_INK_GAP)) <= TOL, rect
    assert abs(rect["x0"] - SIG_BOX.x) <= TOL


@pytest.mark.parametrize("offset", [5.0, -7.5, 20.0, -20.0])
def test_signature_offset_is_positive_up(offset):
    rect = _image_rect(_sign(_png(300, 80), Layout(signature_offset_pt=offset)))
    assert abs(rect["bottom"] - (LINE - SIGNATURE_INK_GAP - offset)) <= TOL, rect


def test_slot_offset_adds_to_the_global_offset_for_its_own_slot_only():
    layout = Layout(signature_offset_pt=5.0, slot_offsets_pt={"p1_sig": 2.5, "other": 9.0})
    rect = _image_rect(_sign(_png(300, 80), layout))
    assert abs(rect["bottom"] - (LINE - SIGNATURE_INK_GAP - 7.5)) <= TOL, rect

    untouched = _image_rect(_sign(_png(300, 80), layout, field_id="p1_other_sig"))
    assert abs(untouched["bottom"] - (LINE - SIGNATURE_INK_GAP - 5.0)) <= TOL


@pytest.mark.parametrize("dx", [0.0, 12.5, -37.25, 200.0, -200.0])
def test_horizontal_slot_offset_moves_the_signature_left_edge_positive_right(dx):
    layout = Layout(slot_offsets_x_pt={"p1_sig": dx, "other": 99.0})
    rect = _image_rect(_sign(_png(300, 80), layout))
    assert abs(rect["x0"] - (SIG_BOX.x + dx)) <= TOL, rect
    # Vertical placement is untouched by dx.
    assert abs(rect["bottom"] - (LINE - SIGNATURE_INK_GAP)) <= TOL, rect


def test_horizontal_slot_offset_applies_to_its_own_slot_only():
    layout = Layout(slot_offsets_x_pt={"p1_sig": 30.0})
    untouched = _image_rect(_sign(_png(300, 80), layout, field_id="p1_other_sig"))
    assert abs(untouched["x0"] - SIG_BOX.x) <= TOL


@pytest.mark.parametrize("scale", [0.6, 1.5])
def test_dx_and_dy_combine_on_a_slot_signature(scale):
    layout = Layout(signature_offset_pt=4.0, signature_scale=scale,
                    slot_offsets_pt={"p1_sig": -60.0}, slot_offsets_x_pt={"p1_sig": -45.0})
    rect = _image_rect(_sign(_png(300, 80), layout))
    assert abs(rect["x0"] - (SIG_BOX.x - 45.0)) <= TOL, rect
    assert abs(rect["bottom"] - (LINE - SIGNATURE_INK_GAP - (4.0 - 60.0))) <= TOL, rect


def test_dx_shifts_a_padded_capture_by_the_image_left_edge():
    """dx moves the drawn image's left edge (bbox.x + dx), padding included."""
    ink = (40, 10, 260, 50)
    rect = _image_rect(_sign(_png(300, 80, ink), Layout(slot_offsets_x_pt={"p1_sig": 15.0})))
    assert abs(rect["x0"] - (SIG_BOX.x + 15.0)) <= TOL, rect


def test_a_free_signature_ignores_the_horizontal_slot_offset():
    layout = Layout(slot_offsets_x_pt={"p1_sig": 50.0, "": 50.0})
    rect = _image_rect(_sign(_png(300, 80), layout, rule_y=None, field_id=None))
    assert abs(rect["x0"] - SIG_BOX.x) <= TOL, rect


def test_slot_offset_x_accessor():
    layout = Layout(slot_offsets_x_pt={"a": 3.5})
    assert layout.slot_offset_x("a") == 3.5
    assert layout.slot_offset_x("b") == 0.0
    assert layout.slot_offset_x(None) == 0.0
    assert layout.slot_offset_x("") == 0.0
    assert Layout.from_mapping({"slot_offsets_x_pt": {"a": 1}}).slot_offset_x("a") == 1.0


@pytest.mark.parametrize("scale", [0.6, 1.0, 1.5])
def test_signature_scale_contain_fits_the_scaled_box(scale):
    wide = _image_rect(_sign(_png(1000, 100), Layout(signature_scale=scale)))
    assert abs((wide["x1"] - wide["x0"]) - SIG_BOX.width * scale) <= TOL
    assert abs((wide["x1"] - wide["x0"]) / (wide["bottom"] - wide["top"]) - 10.0) < 0.02

    tall = _image_rect(_sign(_png(100, 500), Layout(signature_scale=scale)))
    assert abs((tall["bottom"] - tall["top"]) - SIG_BOX.height * scale) <= TOL
    # Scaling never moves the anchor: ink bottom stays on line - gap.
    assert abs(tall["bottom"] - (LINE - SIGNATURE_INK_GAP)) <= TOL
    assert abs(tall["x0"] - SIG_BOX.x) <= TOL


# -- per-slot scale (contract v3) ---------------------------------------------------
#
# `slot_scales[field_id]` REPLACES `signature_scale` for that slot instead of
# multiplying it, so the size a corner drag asks for is the size drawn. Only the
# fit box changes: left edge and ink bottom are untouched, so the mark grows UP
# and RIGHT from a fixed bottom-left anchor.


def test_slot_scale_replaces_the_global_scale_instead_of_multiplying_it():
    layout = Layout(signature_scale=1.5, slot_scales={"p1_sig": 0.5})
    rect = _image_rect(_sign(_png(1000, 100), layout))
    width = rect["x1"] - rect["x0"]
    assert abs(width - SIG_BOX.width * 0.5) <= TOL, rect
    # The product would be 1.5 * 0.5 = 0.75 of the box. It must not be that.
    assert abs(width - SIG_BOX.width * 0.75) > 1.0, rect


def test_a_slot_without_an_entry_keeps_the_global_scale():
    layout = Layout(signature_scale=1.5, slot_scales={"p1_sig": 0.5})
    rect = _image_rect(_sign(_png(1000, 100), layout, field_id="p1_other_sig"))
    assert abs((rect["x1"] - rect["x0"]) - SIG_BOX.width * 1.5) <= TOL, rect


@pytest.mark.parametrize("slot_scale", [0.3, 0.5, 1.0, 2.0, 3.0])
def test_the_bottom_left_corner_is_fixed_while_a_slot_is_resized(slot_scale):
    """The lock the corner-drag UI depends on: whatever the scale, the drawn
    image keeps its left edge on `bbox.x + dx` and its ink bottom one gap above
    the line. Resizing therefore only ever adds area up and to the right."""
    layout = Layout(
        signature_offset_pt=4.0,
        signature_scale=1.5,
        slot_offsets_pt={"p1_sig": -6.0},
        slot_offsets_x_pt={"p1_sig": 12.0},
        slot_scales={"p1_sig": slot_scale},
    )
    rect = _image_rect(_sign(_png(1000, 100), layout))
    assert abs(rect["x0"] - (SIG_BOX.x + 12.0)) <= TOL, rect
    assert abs(rect["bottom"] - (LINE - SIGNATURE_INK_GAP - (4.0 - 6.0))) <= TOL, rect
    assert abs((rect["x1"] - rect["x0"]) - SIG_BOX.width * slot_scale) <= TOL, rect


def test_resizing_a_slot_grows_the_mark_up_and_right_only():
    small = _image_rect(_sign(_png(1000, 100), Layout(slot_scales={"p1_sig": 0.5})))
    large = _image_rect(_sign(_png(1000, 100), Layout(slot_scales={"p1_sig": 3.0})))
    assert abs(small["x0"] - large["x0"]) <= TOL
    assert abs(small["bottom"] - large["bottom"]) <= TOL
    assert large["x1"] > small["x1"] + 1.0
    assert large["top"] < small["top"] - 1.0


def test_a_padded_capture_keeps_its_ink_bottom_and_image_left_at_any_slot_scale():
    """With transparent padding under the strokes the anchor is still exact in
    y (the INK bottom lands on the target, padding hangs below) and the drawn
    image's left edge is still `bbox.x`; the ink's own left edge sits
    `ink_left_px * scale` inside it, so it moves with the scale by design."""
    ink = (40, 10, 260, 50)
    png = _png(300, 80, ink)
    for slot_scale in (0.3, 1.0, 3.0):
        rect = _image_rect(_sign(png, Layout(slot_scales={"p1_sig": slot_scale})))
        scale_y = (rect["bottom"] - rect["top"]) / 80
        ink_bottom = rect["top"] + ink[3] * scale_y
        assert abs(ink_bottom - (LINE - SIGNATURE_INK_GAP)) <= TOL, (slot_scale, rect)
        assert abs(rect["x0"] - SIG_BOX.x) <= TOL, (slot_scale, rect)


def test_a_free_signature_ignores_slot_scales():
    layout = Layout(signature_scale=0.6, slot_scales={"p1_sig": 3.0, "": 3.0})
    rect = _image_rect(_sign(_png(100, 500), layout, rule_y=None, field_id=None))
    assert abs((rect["bottom"] - rect["top"]) - SIG_BOX.height * 0.6) <= TOL, rect


def test_an_oversized_slot_scale_draws_past_the_page_edge_unclamped():
    """Documented behaviour: the builder validates the RANGE only, never the
    page. A slot near the right margin at scale 3.0 is drawn past the mediabox
    -- the bytes carry it, viewers clip it at the page edge. Keeping a mark
    inside the page is the frontend's job (it clamps the drag handle)."""
    near_edge = BBox(500, 460, 100, 34)
    rect = _image_rect(
        _sign(_png(1000, 100), Layout(slot_scales={"p1_sig": 3.0}), bbox=near_edge)
    )
    assert abs(rect["x0"] - near_edge.x) <= TOL, rect
    assert abs((rect["x1"] - rect["x0"]) - near_edge.width * 3.0) <= TOL, rect
    assert rect["x1"] > WIDTH, ("expected to overflow the 612pt page", rect)


def test_signature_scale_for_accessor():
    layout = Layout(signature_scale=1.2, slot_scales={"a": 0.3})
    assert layout.signature_scale_for("a") == 0.3
    assert layout.signature_scale_for("b") == 1.2
    assert layout.signature_scale_for(None) == 1.2
    assert layout.signature_scale_for("") == 1.2
    assert Layout.from_mapping({"slot_scales": {"a": 3}}).signature_scale_for("a") == 3.0
    assert Layout.from_mapping({}).signature_scale_for("a") == 1.0


def test_a_legacy_padded_capture_still_seats_its_ink_not_its_padding():
    ink = (40, 10, 260, 50)  # 30 of 80 rows are transparent padding below the ink
    png = _png(300, 80, ink)
    rect = _image_rect(_sign(png))
    scale_y = (rect["bottom"] - rect["top"]) / 80
    ink_bottom = rect["top"] + ink[3] * scale_y
    assert abs(ink_bottom - (LINE - SIGNATURE_INK_GAP)) <= TOL, ink_bottom


def test_null_rule_y_falls_back_to_the_box_bottom():
    rect = _image_rect(_sign(_png(300, 80), rule_y=None))
    assert abs(rect["bottom"] - (SIG_BOX.y1 - SIGNATURE_INK_GAP)) <= TOL


@pytest.mark.parametrize("scale", [0.6, 1.0, 1.5])
def test_a_free_signature_uses_the_box_bottom_and_only_the_global_offset(scale):
    """Contract v1.1: field_id None -> line = bbox bottom, no slot offset."""
    layout = Layout(signature_offset_pt=4.0, signature_scale=scale,
                    slot_offsets_pt={"p1_sig": 9.0, "": 9.0})
    rect = _image_rect(_sign(_png(100, 500), layout, rule_y=None, field_id=None))
    assert abs(rect["bottom"] - (SIG_BOX.y1 - SIGNATURE_INK_GAP - 4.0)) <= TOL, rect
    assert abs((rect["bottom"] - rect["top"]) - SIG_BOX.height * scale) <= TOL
    assert abs(rect["x0"] - SIG_BOX.x) <= TOL


def test_an_unset_rule_is_measured_live_from_the_source():
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setLineWidth(0.5)
    pdf.line(200, HEIGHT - 500.25, 350, HEIGHT - 500.25)  # ink top at canonical 500.0
    pdf.showPage()
    pdf.save()
    placement = SignaturePlacement(page=1, bbox=SIG_BOX, image_bytes=_png(300, 80), field_id="x")
    out = PdfBuilder().build_final_contract(buffer.getvalue(), [placement], []).pdf_bytes
    assert abs(_image_rect(out)["bottom"] - (500.0 - SIGNATURE_INK_GAP)) <= TOL


# -- dates ------------------------------------------------------------------------


def _date_chars(pdf_bytes: bytes) -> list[dict]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return [c for c in pdf.pages[0].chars if float(c["x0"]) >= DATE_BOX.x - 1 and c["text"] in "0123456789/"]


def _baseline(char: dict) -> float:
    return float(char["top"]) + (float(char["y1"]) - float(char["matrix"][5]))


def _date(layout: Layout | None = None, *, rule_y=LINE, field_id="p1_date") -> bytes:
    placement = DatePlacement(page=1, bbox=DATE_BOX, value="2026-09-14", field_id=field_id, rule_y=rule_y)
    return PdfBuilder().build_final_contract(_blank_pdf(), [], [placement], layout=layout).pdf_bytes


def test_date_baseline_x_font_and_size_follow_the_layout():
    layout = Layout(date_offset_pt=3.0, slot_offsets_pt={"p1_date": -1.0},
                    date_font="Times-Roman", date_font_size=14.0)
    chars = sorted(_date_chars(_date(layout)), key=lambda c: float(c["x0"]))
    assert "".join(c["text"] for c in chars) == "09/14/2026"
    first = chars[0]
    assert "Times-Roman" in first["fontname"]
    assert abs(float(first["size"]) - 14.0) < 0.01
    assert abs(float(first["x0"]) - DATE_BOX.x) <= TOL
    assert abs(_baseline(first) - (LINE - DATE_INK_GAP - 2.0)) <= TOL


@pytest.mark.parametrize("dx", [25.0, -80.0, 200.0])
def test_horizontal_slot_offset_moves_the_date_x_with_dy(dx):
    layout = Layout(date_offset_pt=2.0, slot_offsets_pt={"p1_date": 10.0},
                    slot_offsets_x_pt={"p1_date": dx})
    with pdfplumber.open(io.BytesIO(_date(layout))) as pdf:
        chars = [c for c in pdf.pages[0].chars if c["text"] in "0123456789/"]
    chars.sort(key=lambda c: float(c["x0"]))
    assert "".join(c["text"] for c in chars) == "09/14/2026"
    assert abs(float(chars[0]["x0"]) - (DATE_BOX.x + dx)) <= TOL, chars[0]
    assert abs(_baseline(chars[0]) - (LINE - DATE_INK_GAP - 12.0)) <= TOL


def test_a_free_date_ignores_the_horizontal_slot_offset():
    layout = Layout(slot_offsets_x_pt={"p1_date": 60.0, "": 60.0})
    chars = sorted(_date_chars(_date(layout, field_id=None)), key=lambda c: float(c["x0"]))
    assert abs(float(chars[0]["x0"]) - DATE_BOX.x) <= TOL


@pytest.mark.parametrize("font", ["Helvetica", "Times-Roman", "Courier"])
def test_each_allowed_date_font_is_used(font):
    first = _date_chars(_date(Layout(date_font=font, date_font_size=8.0)))[0]
    assert font in first["fontname"]
    assert abs(float(first["size"]) - 8.0) < 0.01
    assert abs(_baseline(first) - (LINE - DATE_INK_GAP)) <= TOL


def test_date_with_null_rule_sits_on_the_box_bottom():
    first = _date_chars(_date(rule_y=None))[0]
    assert abs(_baseline(first) - (DATE_BOX.y1 - DATE_INK_GAP)) <= TOL


def test_slot_scales_leave_the_date_alone():
    """Contract v3: date text is sized by `date_font_size` only. The builder
    never consults a scale for a date, so even a (server-refused) date key in
    `slot_scales` changes nothing here."""
    layout = Layout(slot_scales={"p1_date": 3.0, "p1_sig": 3.0}, signature_scale=0.6)
    first = _date_chars(_date(layout))[0]
    assert abs(float(first["size"]) - 11.0) < 0.01
    assert abs(_baseline(first) - (LINE - DATE_INK_GAP)) <= TOL
    assert abs(float(first["x0"]) - DATE_BOX.x) <= TOL


# -- pages and ink measurement -----------------------------------------------------


def test_a_placement_past_the_last_page_is_refused_not_dropped():
    placement = SignaturePlacement(page=3, bbox=SIG_BOX, image_bytes=_png(30, 8), field_id="p3_sig")
    with pytest.raises(PageOutOfRangeError) as caught:
        PdfBuilder().build_final_contract(_blank_pdf(pages=2), [placement], [])
    assert caught.value.pages == [3] and caught.value.page_count == 2


def test_a_lone_speck_does_not_stretch_the_ink_box():
    image = Image.new("RGBA", (400, 100), (0, 0, 0, 0))
    for x in range(20, 380):
        for y in range(30, 36):
            image.putpixel((x, y), (0, 0, 0, 255))
    image.putpixel((5, 97), (0, 0, 0, 255))  # background dust near the bottom
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    _, _, left, top, right, bottom = _ink_metrics(buffer.getvalue())
    assert (top, bottom) == (30, 36)
    assert (left, right) == (20, 380)


def test_a_single_strong_stroke_row_is_still_ink():
    image = Image.new("RGBA", (240, 60), (0, 0, 0, 0))
    for x in range(10, 230):
        image.putpixel((x, 30), (0, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    _, _, left, top, right, bottom = _ink_metrics(buffer.getvalue())
    assert (left, top, right, bottom) == (10, 30, 230, 31)


def test_an_opaque_scan_with_noise_uses_luma_projection():
    image = Image.new("RGB", (400, 100), (255, 255, 255))
    for x in range(50, 350):
        for y in range(40, 60):
            image.putpixel((x, y), (20, 20, 20))
    image.putpixel((390, 95), (200, 200, 200))  # JPEG-ish speck
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    _, _, left, top, right, bottom = _ink_metrics(buffer.getvalue())
    assert (left, top, right, bottom) == (50, 40, 350, 60)
