"""Locating the visible fill line under a slot (services.rule_locator).

Every expected value is derived from what is drawn, in points, never from the
locator's own constants: a stroked line's ink top is centre + width/2, a filled
rect's is its top, and Helvetica's underscore ink starts 0.075em below the
baseline (Adobe AFM: underscore B -19 -125 537 -75).
"""
import copy
import io

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.services.geometry import BBox
from app.services.rule_locator import (
    RULE_Y_KEY,
    RuleLocator,
    needs_rule_y,
    rule_summary,
    with_rule_y,
)

WIDTH, HEIGHT = letter
TOLERANCE = 0.3


def _pdf(draw) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    draw(pdf)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _box_above(line_top: float, x: float = 200.0, width: float = 150.0) -> BBox:
    """A field box ending a few points above the line, as a text detector's does."""
    return BBox(x, line_top - 3.0 - 20.0, width, 20.0)


def test_a_stroked_vector_line_is_found_at_its_ink_top():
    y, stroke = 300.0, 0.5

    def draw(pdf):
        pdf.setLineWidth(stroke)
        pdf.line(200, y, 350, y)

    expected = HEIGHT - (y + stroke / 2)
    with RuleLocator(_pdf(draw)) as locator:
        found = locator.locate(1, _box_above(expected))
    assert found is not None
    assert abs(found - expected) <= TOLERANCE, (found, expected)


def test_a_thin_filled_rect_is_found_at_its_top():
    y, thickness = 400.0, 0.6

    def draw(pdf):
        pdf.rect(200, y, 150, thickness, stroke=0, fill=1)

    expected = HEIGHT - (y + thickness)
    with RuleLocator(_pdf(draw)) as locator:
        found = locator.locate(1, _box_above(expected))
    assert found is not None
    assert abs(found - expected) <= TOLERANCE, (found, expected)


def rendered_ink_top(pdf_bytes: bytes, x0: float, x1: float, y_from: float, y_to: float) -> float:
    """Ground truth independent of the locator: the WHOLE page rendered at a
    different scale (16 px/pt, no crop), first row in [y_from, y_to) where more
    than a quarter of the sampled columns in [x0, x1) are dark.

    Needed because a non-embedded base-14 font is drawn with a substitute: for
    reportlab's Helvetica pdfium puts the underscore ink ~0.6pt lower than the
    Adobe AFM predicts. Metrics are exactly what the locator must not trust.
    """
    import pypdfium2 as pdfium

    scale = 16
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        page = doc[0]
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil().convert("L")
        bitmap.close()
        page.close()
    finally:
        doc.close()
    pixels = image.load()
    columns = range(int(x0 * scale), int(x1 * scale), 2)
    for row in range(int(y_from * scale), int(y_to * scale)):
        if sum(1 for x in columns if pixels[x, row] < 128) > len(columns) / 4:
            return row / scale
    raise AssertionError("no rendered rule in the probe window")


def test_an_underscore_glyph_run_is_found_below_its_baseline():
    """Docusign-style rule: a run of '_' characters. The ink top is NOT the
    baseline (nor where font metrics say); it is where the glyphs render."""
    baseline, size = 200.0, 11.0

    def draw(pdf):
        pdf.setFont("Helvetica", size)
        pdf.drawString(72, baseline, "Contractor Signature:")
        pdf.drawString(200, baseline, "_" * 25)

    data = _pdf(draw)
    canonical_baseline = HEIGHT - baseline
    expected = rendered_ink_top(data, 205, 330, canonical_baseline - 3, canonical_baseline + 8)
    assert expected > canonical_baseline, "premise: the ink starts below the baseline"
    # The detector's text box ends at the baseline-ish, above the ink.
    bbox = BBox(200, canonical_baseline - 30, 140, 30)
    with RuleLocator(data) as locator:
        found = locator.locate(1, bbox)
    assert found is not None
    assert abs(found - expected) <= TOLERANCE, (found, expected)
    assert found > canonical_baseline, "the baseline itself must never be returned"


def test_no_line_under_the_box_is_null():
    def draw(pdf):
        pdf.setFont("Helvetica", 11)
        # Text under the box never covers half its width on one pixel row.
        pdf.drawString(200, 480, "Exp 04/03/2031")

    with RuleLocator(_pdf(draw)) as locator:
        assert locator.locate(1, BBox(200, HEIGHT - 480 - 20, 250, 18)) is None


def test_a_short_rule_covering_less_than_half_the_box_is_null():
    def draw(pdf):
        pdf.setLineWidth(0.5)
        pdf.line(200, 300, 250, 300)  # 50 of a 150pt box

    with RuleLocator(_pdf(draw)) as locator:
        assert locator.locate(1, _box_above(HEIGHT - 300.25)) is None


def test_a_thick_filled_block_is_not_a_rule():
    def draw(pdf):
        pdf.rect(200, 300, 150, 6, stroke=0, fill=1)

    with RuleLocator(_pdf(draw)) as locator:
        assert locator.locate(1, _box_above(HEIGHT - 306)) is None


def test_a_line_far_below_the_box_is_not_its_rule():
    """p14_cdl_expiration: the nearest underscore run is 36.8pt below."""

    def draw(pdf):
        pdf.setLineWidth(0.5)
        pdf.line(200, 300, 350, 300)

    with RuleLocator(_pdf(draw)) as locator:
        assert locator.locate(1, _box_above(HEIGHT - 300.25 - 36.8)) is None


def test_the_nearest_of_two_lines_wins():
    def draw(pdf):
        pdf.setLineWidth(0.5)
        pdf.line(200, 300, 350, 300)
        pdf.line(200, 290, 350, 290)

    near = HEIGHT - 300.25
    with RuleLocator(_pdf(draw)) as locator:
        found = locator.locate(1, _box_above(near))
    assert abs(found - near) <= TOLERANCE


def test_a_page_past_the_end_or_an_unreadable_pdf_is_null_not_an_error():
    with RuleLocator(_pdf(lambda pdf: None)) as locator:
        assert locator.locate(5, BBox(10, 10, 100, 10)) is None
    with RuleLocator(b"%PDF-not really") as locator:
        assert locator.locate(1, BBox(10, 10, 100, 10)) is None


# -- schema: additive only -----------------------------------------------------


def _schema(line_top: float) -> dict:
    return {
        "field_count": 3,
        "pages": [
            {
                "page_number": 1,
                "fields": [
                    {
                        "field_id": "p1_signature",
                        "field_type": "signature",
                        "page": 1,
                        "bbox": _box_above(line_top).to_dict(),
                        "required": True,
                        "paired_date_field_id": "p1_when",
                        "template_baseline": {"image_hashes": [], "strokes": 0,
                                              "text_tokens": [], "prefilled": False},
                    },
                    {
                        # A paired date typed as text: still a slot, still ruled.
                        "field_id": "p1_when",
                        "field_type": "text_line",
                        "page": 1,
                        "bbox": _box_above(line_top, x=400, width=100).to_dict(),
                        "required": True,
                    },
                    {
                        "field_id": "p1_full_name",
                        "field_type": "text_line",
                        "page": 1,
                        "bbox": {"x": 72, "y": 100, "width": 200, "height": 14},
                        "required": True,
                    },
                ],
            }
        ],
    }


def test_rule_y_is_added_without_changing_any_existing_key():
    y = 300.0

    def draw(pdf):
        pdf.setLineWidth(0.5)
        pdf.line(200, y, 350, y)
        pdf.line(400, y, 500, y)

    line_top = HEIGHT - (y + 0.25)
    original = _schema(line_top)
    frozen = copy.deepcopy(original)
    assert needs_rule_y(original)

    upgraded = with_rule_y(_pdf(draw), original)

    assert original == frozen, "the input schema must not be mutated"
    assert upgraded["field_count"] == frozen["field_count"]
    before = frozen["pages"][0]["fields"]
    after = upgraded["pages"][0]["fields"]
    assert [f["field_id"] for f in after] == [f["field_id"] for f in before]
    for old, new in zip(before, after):
        extra = set(new) - set(old)
        assert {k: new[k] for k in old} == old, "no existing key may change"
        if old["field_id"] == "p1_full_name":
            assert extra == set(), "non-slot fields are untouched"
        else:
            assert extra == {RULE_Y_KEY}
            assert abs(new[RULE_Y_KEY] - line_top) <= TOLERANCE
    assert not needs_rule_y(upgraded)
    assert rule_summary(upgraded) == {"rules_found": 2, "rules_missing": 0}


def test_a_slot_with_no_line_is_stored_as_null_and_not_recomputed():
    upgraded = with_rule_y(_pdf(lambda pdf: None), _schema(300.0))
    slot = upgraded["pages"][0]["fields"][0]
    assert RULE_Y_KEY in slot and slot[RULE_Y_KEY] is None
    assert not needs_rule_y(upgraded), "null is an answer, not a missing key"


@pytest.mark.parametrize("source_kind", ["bytes", "path"])
def test_with_rule_y_accepts_bytes_and_a_path(tmp_path, source_kind):
    data = _pdf(lambda pdf: (pdf.setLineWidth(0.5), pdf.line(200, 300, 350, 300)))
    source = data
    if source_kind == "path":
        path = tmp_path / "t.pdf"
        path.write_bytes(data)
        source = str(path)
    upgraded = with_rule_y(source, _schema(HEIGHT - 300.25))
    assert upgraded["pages"][0]["fields"][0][RULE_Y_KEY] is not None
    if source_kind == "path":
        # The document is closed again: Windows cannot unlink an open file.
        (tmp_path / "t.pdf").unlink()
