"""The fill rule on a page carrying /Rotate 90/180/270 (services.rule_locator).

Before: a rotated page was searched for VECTOR rules only, so a Docusign-style
'____' glyph run was never found -> `rule_y` None -> the signature sat on the
field box's bottom (11.25 pt above the line here) and the date ~3 pt high.
Now the same render-based measurement runs on the page AS DISPLAYED.

The pages are built the way a scanner/Docusign rotated page looks: content
written in the unrotated user space through the displayed->unrotated matrix,
then /Rotate set, so a reader sees it upright. Ground truth is always the
whole page rendered by pdfium (which applies /Rotate), never the locator.
"""
import io
import os
import tempfile

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, FloatObject, NameObject
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.services.field_detector import FieldDetector
from app.services.geometry import BBox
from app.services.pdf_builder import (
    SIGNATURE_INK_GAP,
    DatePlacement,
    PdfBuilder,
    SignaturePlacement,
)
from app.services.rule_locator import RULE_Y_KEY, RuleLocator, with_rule_y

W, H = letter
SCALE = 8  # measurement render, px/pt
ROW = 1.0 / SCALE
# Displayed (bottom-left origin) -> unrotated user space, as PdfBuilder._merge_overlay.
MATRIX = {
    0: (1, 0, 0, 1, 0, 0),
    90: (0, 1, -1, 0, W, 0),
    180: (-1, 0, 0, -1, W, H),
    270: (0, -1, 1, 0, 0, H),
}
LINE_Y_UP = 300.0  # displayed, from the displayed bottom


def _displayed_size(rotation):
    return (H, W) if rotation in (90, 270) else (W, H)


def _page(rotation: int, cropbox_inset: float = 0.0, vector: bool = False) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(W, H))
    pdf.transform(*MATRIX[rotation])
    _, displayed_height = _displayed_size(rotation)
    y = displayed_height - LINE_Y_UP
    pdf.setFont("Helvetica", 11)
    if vector:
        pdf.drawString(72, y + 2, "Contractor Signature:")
        pdf.setLineWidth(0.5)
        pdf.line(180, y, 360, y)
    else:
        pdf.drawString(72, y, "Contractor Signature: " + "_" * 30 + "   Date: " + "_" * 18)
    pdf.showPage()
    pdf.save()
    writer = PdfWriter(clone_from=io.BytesIO(buffer.getvalue()))
    page = writer.pages[0]
    if rotation:
        page.rotate(rotation)
    if cropbox_inset:
        i = cropbox_inset
        page[NameObject("/CropBox")] = ArrayObject(
            [FloatObject(i), FloatObject(i), FloatObject(W - i), FloatObject(H - i)]
        )
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _render(pdf_bytes: bytes) -> Image.Image:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        page = doc[0]
        bitmap = page.render(scale=SCALE)
        image = bitmap.to_pil().convert("RGB")
        bitmap.close()
        page.close()
    finally:
        doc.close()
    return image


def _dark(p):
    return sum(p) < 3 * 128


def _blue(p):
    return p[2] > 150 and p[0] < 100 and p[1] < 100


def _rendered_line_top(image: Image.Image, x0: float, x1: float, y_from: float, y_to: float) -> float:
    pixels = image.load()
    columns = range(int(x0 * SCALE), int(x1 * SCALE))
    for row in range(int(y_from * SCALE), int(y_to * SCALE)):
        if sum(1 for x in columns if _dark(pixels[x, row])) > 0.5 * len(columns):
            return row / SCALE
    raise AssertionError("no rendered rule in the probe window")


def _schema(pdf_bytes: bytes) -> dict:
    """The template pipeline exactly: detector, then `with_rule_y` (tasks.py)."""
    handle, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(pdf_bytes)
        return with_rule_y(path, FieldDetector().build_schema(path))
    finally:
        os.unlink(path)


def _slots(schema: dict) -> tuple[dict, dict]:
    fields = [f for page in schema["pages"] for f in page["fields"]]
    return (
        next(f for f in fields if f["field_type"] == "signature"),
        next(f for f in fields if f["field_type"] == "date"),
    )


def _signature_png() -> bytes:
    image = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    ImageDraw.Draw(image).line(
        [(20, 80), (120, 20), (200, 80), (280, 30)], fill=(0, 0, 255, 255), width=6
    )
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _measure_final(pdf_bytes: bytes, sig: dict, date: dict, rule) -> dict:
    """Build the final and measure (on the displayed render) the signature's
    ink bottom and the date's ink bottom against the rule's ink top."""
    extra = {} if rule == "live" else None

    def kw(field):
        if extra is not None:
            return {}
        return {"rule_y": rule(field)}

    result = PdfBuilder().build_final_contract(
        pdf_bytes,
        [SignaturePlacement(page=1, bbox=BBox.from_dict(sig["bbox"]), image_bytes=_signature_png(),
                            field_id=sig["field_id"], **kw(sig))],
        [DatePlacement(page=1, bbox=BBox.from_dict(date["bbox"]), value="2026-09-30",
                       field_id=date["field_id"], **kw(date))],
    )
    image = _render(result.pdf_bytes)
    pixels = image.load()
    sb, db = sig["bbox"], date["bbox"]
    line_y = sb["y"] + sb["height"]
    sig_line = _rendered_line_top(image, sb["x"] + 25, sb["x"] + 150, line_y - 6, line_y + 16)
    date_line = _rendered_line_top(image, db["x"] + 5, db["x"] + 90, line_y - 6, line_y + 16)

    sig_columns = range(int(sb["x"] * SCALE), int((sb["x"] + sb["width"]) * SCALE))
    sig_rows = [
        row for row in range(int((sb["y"] - 20) * SCALE), int(sig_line * SCALE))
        if any(_blue(pixels[x, row]) for x in sig_columns)
    ]
    date_columns = range(int(db["x"] * SCALE), int((db["x"] + 60) * SCALE))
    row = int(date_line * SCALE)
    while any(_dark(pixels[x, row]) for x in date_columns):
        row -= 1
    while not any(_dark(pixels[x, row]) for x in date_columns):
        row -= 1
    return {
        "signature_gap": sig_line - (max(sig_rows) + 1) / SCALE,
        "date_gap": date_line - (row + 1) / SCALE,
        "line_top": sig_line,
    }


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_an_underscore_run_on_a_rotated_page_is_found_where_it_renders(rotation):
    data = _page(rotation)
    sig, date = _slots(_schema(data))
    truth = _rendered_line_top(
        _render(data), sig["bbox"]["x"] + 25, sig["bbox"]["x"] + 150,
        sig["bbox"]["y"] + sig["bbox"]["height"] - 6, sig["bbox"]["y"] + sig["bbox"]["height"] + 16,
    )
    assert sig[RULE_Y_KEY] is not None and date[RULE_Y_KEY] is not None, "the glyph rule must be found"
    assert abs(sig[RULE_Y_KEY] - truth) <= ROW, (sig[RULE_Y_KEY], truth)
    assert abs(date[RULE_Y_KEY] - truth) <= ROW, (date[RULE_Y_KEY], truth)


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_a_rotated_page_gives_the_same_rule_as_the_unrotated_one(rotation):
    """Same displayed content -> same canonical boxes -> same rule (the rot-0
    path keeps its own sub-row window rounding, hence one row)."""
    upright_sig, _ = _slots(_schema(_page(0)))
    sig, _ = _slots(_schema(_page(rotation)))
    assert sig["bbox"] == upright_sig["bbox"]
    assert abs(sig[RULE_Y_KEY] - upright_sig[RULE_Y_KEY]) <= ROW


@pytest.mark.parametrize("rotation", [90, 270])
def test_a_vector_rule_on_a_rotated_page_is_still_found(rotation):
    data = _page(rotation, vector=True)
    # Drawn at displayed_height - LINE_Y_UP (y up) -> canonical centre
    # LINE_Y_UP (y down); the ink top is half the 0.5 pt stroke above it.
    expected = LINE_Y_UP - 0.25
    with RuleLocator(data) as locator:
        found = locator.locate(1, BBox(180, expected - 3 - 20, 180, 20))
    assert found is not None and abs(found - expected) <= 0.3, (found, expected)


@pytest.mark.parametrize("rotation", [90, 270])
def test_a_cropbox_on_a_rotated_page_does_not_move_the_rule(rotation):
    """Canonical coordinates are relative to the displayed MEDIABOX; pdfium
    renders the displayed CROPBOX. A crop that leaves the line visible must
    not change the answer."""
    plain = _page(rotation)
    cropped = _page(rotation, cropbox_inset=20.0)
    sig, _ = _slots(_schema(plain))
    bbox = BBox.from_dict(sig["bbox"])
    with RuleLocator(plain) as a, RuleLocator(cropped) as b:
        assert a.locate(1, bbox) is not None
        assert abs(a.locate(1, bbox) - b.locate(1, bbox)) <= 1e-6


@pytest.mark.parametrize("rotation", [90, 270])
@pytest.mark.parametrize("rule_source", ["stored", "live"])
def test_signature_and_date_sit_on_the_line_of_a_rotated_page(rotation, rule_source):
    """Pixel measurement on the final: signature ink bottom exactly
    SIGNATURE_INK_GAP above the rule (<= 1.5 pt required), the date on the
    rule exactly as on the unrotated page."""
    upright = _page(0)
    up_sig, up_date = _slots(_schema(upright))
    reference = _measure_final(upright, up_sig, up_date, lambda f: f[RULE_Y_KEY])

    data = _page(rotation)
    sig, date = _slots(_schema(data))
    rule = "live" if rule_source == "live" else (lambda f: f[RULE_Y_KEY])
    measured = _measure_final(data, sig, date, rule)

    assert measured["signature_gap"] <= 1.5
    assert abs(measured["signature_gap"] - SIGNATURE_INK_GAP) <= ROW + 1e-9, measured
    assert abs(measured["signature_gap"] - reference["signature_gap"]) <= ROW + 1e-9, (measured, reference)
    assert abs(measured["date_gap"] - reference["date_gap"]) <= ROW + 1e-9, (measured, reference)


def test_the_old_fallback_is_what_went_wrong():
    """Premise: with no rule (the old outcome on a rotated page) the signature
    floats a whole text line above the rule -- what the fix removes."""
    data = _page(90)
    sig, date = _slots(_schema(data))
    measured = _measure_final(data, sig, date, lambda f: None)
    assert measured["signature_gap"] > 5.0
