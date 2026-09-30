import io

import pdfplumber
import pytest
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.services.field_detector import FieldDetector
from app.services.geometry import BBox
from app.services.pdf_builder import DatePlacement, PdfBuilder, SignaturePlacement


@pytest.fixture
def signature_png() -> bytes:
    image = Image.new("RGBA", (240, 60), (0, 0, 0, 0))
    for x in range(10, 230):
        image.putpixel((x, 30 + (x % 7)), (0, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_date_is_rendered_us_style_not_iso(blank_template_bytes):
    """The date input submits 2026-08-29; the contract prints 08/29/2026."""
    result = PdfBuilder().build_final_contract(
        blank_template_bytes,
        signatures=[],
        dates=[DatePlacement(page=1, bbox=BBox(440, 590, 100, 14), value="2026-08-29")],
    )
    with pdfplumber.open(io.BytesIO(result.pdf_bytes)) as pdf:
        text = pdf.pages[0].extract_text()
    assert "08/29/2026" in text
    assert "2026-08-29" not in text


def test_overlay_lands_at_the_requested_position(blank_template_bytes):
    """A box 590pt from the top of a 792pt page must draw near y=202 in
    reportlab's bottom-left space -- not at y=590, which would mirror it."""
    result = PdfBuilder().build_final_contract(
        blank_template_bytes,
        signatures=[],
        dates=[DatePlacement(page=1, bbox=BBox(440, 590, 100, 14), value="08/29/2026")],
    )
    with pdfplumber.open(io.BytesIO(result.pdf_bytes)) as pdf:
        words = pdf.pages[0].extract_words()
    drawn = next(w for w in words if "08/29/2026" in w["text"])
    assert abs(float(drawn["top"]) - 590) < 12
    assert abs(float(drawn["x0"]) - 440) < 12


def test_signature_image_is_embedded_on_the_right_page(blank_template_bytes, signature_png):
    result = PdfBuilder().build_final_contract(
        blank_template_bytes,
        signatures=[
            SignaturePlacement(
                page=1, bbox=BBox(200, 580, 150, 34), image_bytes=signature_png, field_id="p1_sig"
            )
        ],
        dates=[],
    )
    with pdfplumber.open(io.BytesIO(result.pdf_bytes)) as pdf:
        images = pdf.pages[0].images
    assert images, "the signature image should be present on page 1"
    placed = images[0]
    assert abs(float(placed["top"]) - 580) < 20


def test_page_size_comes_from_the_page_not_a_hardcoded_letter():
    """An A4 page overlaid with letter geometry shifts every mark."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.drawString(72, 700, "A4 PAGE")
    pdf.showPage()
    pdf.save()

    result = PdfBuilder().build_final_contract(
        buffer.getvalue(),
        signatures=[],
        dates=[DatePlacement(page=1, bbox=BBox(100, 500, 100, 14), value="08/29/2026")],
    )
    with pdfplumber.open(io.BytesIO(result.pdf_bytes)) as pdf_out:
        page = pdf_out.pages[0]
        assert round(page.height) == round(A4[1])
        drawn = next(w for w in page.extract_words() if "08/29/2026" in w["text"])
        assert abs(float(drawn["top"]) - 500) < 12


def test_result_is_hashed_for_the_audit_trail(blank_template_bytes):
    builder = PdfBuilder()
    result = builder.build_final_contract(blank_template_bytes, [], [])
    assert len(result.sha256) == 64
    assert result.page_count == 1


def test_owner_password_locks_the_signed_document(blank_template_bytes):
    result = PdfBuilder().build_final_contract(
        blank_template_bytes, [], [], owner_password="secret"
    )
    reader = PdfReader(io.BytesIO(result.pdf_bytes))
    assert reader.is_encrypted


# ---------------------------------------------------------------------------
# Sitting the mark ON the fill line.
#
# The complaint these cover: the signature hung visibly above the line. Two
# independent causes, both measured here in points against the line's own ink.
# ---------------------------------------------------------------------------

PAGE_WIDTH, PAGE_HEIGHT = letter
RULE_BASELINE = 200.0  # reportlab y of the "____" run drawn below
RULE_FONT_SIZE = 11.0
# Adobe Helvetica AFM, glyph 'underscore': B -19 -125 537 -75 (1/1000 em). The
# visible ink therefore starts 0.075em BELOW the baseline.
RULE_INK_TOP = PAGE_HEIGHT - (RULE_BASELINE - 0.075 * RULE_FONT_SIZE)  # top-left y


def _ruled_template() -> bytes:
    """A page whose signature and date rules sit at a known baseline."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Helvetica", RULE_FONT_SIZE)
    pdf.drawString(72, 720, "INDEPENDENT CONTRACTOR AGREEMENT")
    pdf.drawString(72, RULE_BASELINE, "Contractor Signature:")
    pdf.drawString(200, RULE_BASELINE, "_" * 25)
    pdf.drawString(400, RULE_BASELINE, "Date:")
    pdf.drawString(440, RULE_BASELINE, "_" * 14)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


@pytest.fixture
def ruled_template_bytes() -> bytes:
    return _ruled_template()


@pytest.fixture
def ruled_fields(tmp_path, ruled_template_bytes) -> dict:
    """The detector's own boxes for that page, keyed by field type."""
    path = tmp_path / "ruled.pdf"
    path.write_bytes(ruled_template_bytes)
    fields = FieldDetector().analyze_template(str(path))
    return {f.field_type: BBox.from_dict(f.bbox) for f in fields}


def _signature_png(width, height, ink_box, colour=(10, 10, 60, 255)) -> bytes:
    """An RGBA capture whose strokes occupy exactly `ink_box` (l, t, r, b)."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    left, top, right, bottom = ink_box
    for x in range(left, right):
        image.putpixel((x, top), colour)
        image.putpixel((x, bottom - 1), colour)
    for y in range(top, bottom):
        image.putpixel((left, y), colour)
        image.putpixel((right - 1, y), colour)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _placed(pdf_bytes: bytes) -> dict:
    """The drawn image rect, in canonical top-left points."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        images = pdf.pages[0].images
    assert images, "the signature image should be present"
    image = images[0]
    return {
        "x0": float(image["x0"]),
        "top": float(image["top"]),
        "x1": float(image["x1"]),
        "bottom": float(image["bottom"]),
    }


def _ink_bounds(rect: dict, png: bytes, ink_box) -> tuple[float, float]:
    """Where the strokes themselves landed, given the placed rect."""
    with Image.open(io.BytesIO(png)) as image:
        pixel_width, pixel_height = image.size
    scale_y = (rect["bottom"] - rect["top"]) / pixel_height
    return (rect["top"] + ink_box[1] * scale_y, rect["top"] + ink_box[3] * scale_y)


def _sign(template: bytes, bbox: BBox, png: bytes) -> bytes:
    return PdfBuilder().build_final_contract(
        template,
        signatures=[
            SignaturePlacement(page=1, bbox=bbox, image_bytes=png, field_id="p1_contractor_signature")
        ],
        dates=[],
    ).pdf_bytes


def test_signature_ink_rests_on_the_fill_line(ruled_template_bytes, ruled_fields):
    """The field box ends a whole font body ABOVE the visible rule, because it
    is anchored on the underscore's glyph box. Drawing on the box edge left the
    signature hanging ~9.5pt in the air; it must land on the ink instead."""
    ink_box = (20, 15, 280, 70)
    png = _signature_png(300, 100, ink_box)
    bbox = ruled_fields["signature"]
    # The premise: the box's own lower edge is nowhere near the line.
    assert RULE_INK_TOP - bbox.y1 > 8.0

    rect = _placed(_sign(ruled_template_bytes, bbox, png))
    _, ink_bottom = _ink_bounds(rect, png, ink_box)
    gap = RULE_INK_TOP - ink_bottom
    assert 0.0 <= gap <= 3.0, f"signature ink sits {gap:.2f}pt off the rule"


def test_transparent_padding_does_not_lift_the_signature(ruled_template_bytes, ruled_fields):
    """A capture is mostly empty canvas. It is the STROKES that must meet the
    line -- padding below them used to push the whole signature up."""
    ink_box = (60, 10, 240, 30)  # 70 of 100 rows are padding, all of it below
    png = _signature_png(300, 100, ink_box)
    bbox = ruled_fields["signature"]

    rect = _placed(_sign(ruled_template_bytes, bbox, png))
    _, ink_bottom = _ink_bounds(rect, png, ink_box)
    gap = RULE_INK_TOP - ink_bottom
    assert 0.0 <= gap <= 3.0, f"padded signature sits {gap:.2f}pt off the rule"
    # The image rect itself may hang below the line -- that part is transparent.
    assert rect["bottom"] > RULE_INK_TOP


def test_a_very_wide_signature_is_fitted_to_the_box_width(ruled_template_bytes, ruled_fields):
    ink_box = (5, 5, 995, 95)
    png = _signature_png(1000, 100, ink_box)
    bbox = ruled_fields["signature"]

    rect = _placed(_sign(ruled_template_bytes, bbox, png))
    drawn_width = rect["x1"] - rect["x0"]
    drawn_height = rect["bottom"] - rect["top"]
    assert drawn_width <= bbox.width + 0.5
    assert drawn_height <= bbox.height + 0.5
    assert abs(drawn_width / drawn_height - 10.0) < 0.01, "aspect ratio must survive"
    assert abs(drawn_width - bbox.width) < 0.5, "the wide image should fill the width"


def test_a_very_tall_signature_is_fitted_to_the_box_height(ruled_template_bytes, ruled_fields):
    ink_box = (5, 5, 95, 495)
    png = _signature_png(100, 500, ink_box)
    bbox = ruled_fields["signature"]

    rect = _placed(_sign(ruled_template_bytes, bbox, png))
    drawn_width = rect["x1"] - rect["x0"]
    drawn_height = rect["bottom"] - rect["top"]
    assert drawn_width <= bbox.width + 0.5
    assert drawn_height <= bbox.height + 0.5
    assert abs(drawn_height / drawn_width - 5.0) < 0.01, "aspect ratio must survive"
    assert abs(drawn_height - bbox.height) < 0.5, "the tall image should fill the height"


def test_a_blank_capture_is_still_placed_safely(ruled_template_bytes, ruled_fields):
    """Nothing to measure must not mean nothing drawn, or a crash."""
    image = Image.new("RGBA", (200, 60), (0, 0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    rect = _placed(_sign(ruled_template_bytes, ruled_fields["signature"], buffer.getvalue()))
    assert rect["x1"] > rect["x0"] and rect["bottom"] > rect["top"]
    assert abs(rect["bottom"] - (RULE_INK_TOP)) < 3.0


def test_the_date_sits_on_its_own_rule(ruled_template_bytes, ruled_fields):
    """Same fault, same fix, for the date beside the signature."""
    result = PdfBuilder().build_final_contract(
        ruled_template_bytes,
        signatures=[],
        dates=[DatePlacement(page=1, bbox=ruled_fields["date"], value="2026-09-10",
                             field_id="p1_date")],
    )
    with pdfplumber.open(io.BytesIO(result.pdf_bytes)) as pdf:
        digits = [
            char for char in pdf.pages[0].chars
            if char["text"] in "0123456789/" and float(char["x0"]) >= 439.0
        ]
    assert "".join(c["text"] for c in sorted(digits, key=lambda c: float(c["x0"]))) == "09/10/2026"
    # The text matrix carries the baseline exactly; step down from `top` by the
    # same amount to stay in top-left space.
    first = digits[0]
    baseline = float(first["top"]) + (float(first["y1"]) - float(first["matrix"][5]))
    above = RULE_INK_TOP - baseline
    assert 0.0 <= above <= 3.0, f"date baseline sits {above:.2f}pt off the rule"


def test_a_box_with_no_rule_under_it_keeps_its_own_edge(blank_template_bytes):
    """Free placement: nothing to snap to, so the box is obeyed as given."""
    ink_box = (0, 0, 240, 60)
    png = _signature_png(240, 60, ink_box)
    bbox = BBox(120, 300, 150, 34)

    rect = _placed(_sign(blank_template_bytes, bbox, png))
    assert abs(rect["x0"] - bbox.x) < 0.5
    assert abs(rect["bottom"] - bbox.y1) < 1.5
