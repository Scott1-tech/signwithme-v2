"""
Moving marks up/down as well as left/right -- one at a time and all at once.

Reported: "after signing, the placed signatures/dates can only be moved
left-right (all at once), not up-down". These checks pin the four directions
for every way a mark can be moved on the signing page:

* the mouse, on the slot box, on the placed signature image and on the date
  text (screen px -> PDF pt, screen UP = positive dy);
* the per-slot ▲▼ steppers of a date slot;
* the "Move together" pads (signatures / dates / everything), which shift
  every slot's own `slot_offsets_x_pt` / `slot_offsets_pt` entry -- nothing
  new is sent to the server and the global ±20 pt offsets are left alone;
* the page-edge clamp of every slot on its own page, including a document
  that mixes 792 pt and 817 pt tall pages;
* what `POST /finalize` carries afterwards.

Run alone:      python tests/ui/test_move_all.py
Run one check:  python tests/ui/test_move_all.py --only together
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (  # noqa: E402
    MockApi,
    Registry,
    apply_dates_to_all,
    approx,
    by_field,
    cli_main,
    default_slots,
    drag_slot,
    ensure_target_is_up,
    equals,
    open_sign,
    panel_number,
    seed_layout,
    slot,
    slot_stepper,
    snapshot,
    stored_layout,
    truthy,
)

test = Registry("move all ways")

SIGNATURE_INK_GAP = 1.0
DATE_INK_GAP = 1.0
BASE_LAYOUT = {
    "signature_offset_pt": 0, "date_offset_pt": 0,
    "slot_offsets_pt": {}, "slot_offsets_x_pt": {},
    "signature_scale": 1, "date_font": "Helvetica", "date_font_size": 11,
}


def together(page, group: str, direction: str):
    """`group` is Signatures / Dates / Everything."""
    return page.get_by_role("button", name=f"{group} together: move {direction}", exact=True)


def drag_locator(page, locator, dx_px: float, dy_px: float, steps: int = 12) -> None:
    """Same gesture as `_harness.drag_slot`, on any element (image, date text)."""
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    if box is None:
        raise AssertionError("element has no box")
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + (4 if dx_px else 0), cy + (4 if dy_px > 0 else -4 if dy_px < 0 else 0))
    page.mouse.move(cx + dx_px, cy + dy_px, steps=steps)
    page.mouse.up()
    page.wait_for_timeout(150)


def offsets(page, mock: MockApi) -> tuple[dict, dict]:
    stored = stored_layout(page, mock) or {}
    return stored.get("slot_offsets_x_pt", {}), stored.get("slot_offsets_pt", {})


def mixed_height_pdf(heights: list[float]) -> bytes:
    """Blank pages, 612 pt wide, each with its own height (e.g. 792 and 817)."""
    objs: list[str] = ["", ""]
    kids: list[str] = []
    for index, height in enumerate(heights):
        page_obj = 3 + index * 2
        kids.append(f"{page_obj} 0 R")
        content = "0 0 0 RG 1 w 72 100 m 300 100 l S"
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 {height:.0f}] "
            f"/Contents {page_obj + 1} 0 R /Resources << >> >>"
        )
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    objs[0] = "<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(heights)} >>"
    pdf = "%PDF-1.4\n"
    positions: list[int] = []
    for index, obj in enumerate(objs):
        positions.append(len(pdf))
        pdf += f"{index + 1} 0 obj\n{obj}\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    for position in positions:
        pdf += f"{position:010d} 00000 n \n"
    pdf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
    return pdf.encode("ascii")


# --------------------------------------------------------------------------
# 1-4  The mouse: straight up and straight down, on every draggable surface
# --------------------------------------------------------------------------

@test("drag_straight_up_changes_only_dy")
def _(page, mock: MockApi):
    """40 px up on screen = +40/scale pt of dy (positive UP), no dx at all."""
    open_sign(page, mock, expect_images=2)
    scale = snapshot(page)["scale"]
    drag_slot(page, "sig_1", 0, -40)
    xs, ys = offsets(page, mock)
    approx(ys.get("sig_1"), 40 / scale, 0.35, "dy in points (up = positive)")
    equals(xs.get("sig_1"), None, "no horizontal offset from a vertical drag")
    approx(by_field(snapshot(page)["slots"], "sig_1")["bottomPt"], 199.5 - ys["sig_1"], 0.01,
           "box bottom = line - dy")


@test("drag_straight_down_changes_only_dy")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    scale = snapshot(page)["scale"]
    drag_slot(page, "date_1", 0, 40)
    xs, ys = offsets(page, mock)
    approx(ys.get("date_1"), -40 / scale, 0.35, "dy in points (down = negative)")
    equals(xs.get("date_1"), None, "no horizontal offset from a vertical drag")


@test("drag_the_placed_signature_image_up_and_down")
def _(page, mock: MockApi):
    """The user grabs the ink, not the slot box underneath: both axes must follow."""
    open_sign(page, mock, expect_images=2)
    scale = snapshot(page)["scale"]
    image = page.locator('img.placed-signature[data-field-id="sig_2"]')
    drag_locator(page, image, 0, -30)
    _, ys = offsets(page, mock)
    approx(ys.get("sig_2"), 30 / scale, 0.35, "image dragged up")
    drag_locator(page, image, 0, 60)
    _, ys = offsets(page, mock)
    approx(ys.get("sig_2"), -30 / scale, 0.5, "image dragged down past its start")
    placed = by_field(snapshot(page)["placed"], "sig_2")
    approx(placed["bottomPt"], 394.0 - SIGNATURE_INK_GAP - ys["sig_2"], 0.01,
           "image bottom = line - 1 - dy (the PDF's formula)")


@test("drag_the_date_text_up_and_down")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    apply_dates_to_all(page)
    scale = snapshot(page)["scale"]
    text = page.locator('.placed-dates text[data-field-id="date_1"]')
    drag_locator(page, text, 0, -25)
    xs, ys = offsets(page, mock)
    approx(ys.get("date_1"), 25 / scale, 0.35, "date text dragged up")
    equals(xs.get("date_1"), None, "and not sideways")
    date = by_field(snapshot(page)["dates"], "date_1")
    approx(date["baselinePt"], 199.5 - DATE_INK_GAP - ys["date_1"], 0.01, "baseline = line - 1 - dy")


# --------------------------------------------------------------------------
# 5  Per-slot vertical stepper on a date slot
# --------------------------------------------------------------------------

@test("date_slot_vertical_stepper_moves_up_and_down")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    apply_dates_to_all(page)
    slot_stepper(page, "date_2", "up").click(modifiers=["Shift"])
    equals(panel_number(page, "offset-slot-date_2"), 2.0, "▲ Shift = +2 pt")
    approx(by_field(snapshot(page)["dates"], "date_2")["baselinePt"], 400 - DATE_INK_GAP - 2, 0.01,
           "baseline moved up 2 pt")
    for _ in range(3):
        slot_stepper(page, "date_2", "down").click()
    equals(panel_number(page, "offset-slot-date_2"), 0.5, "three ▼ = -1.5 pt")


# --------------------------------------------------------------------------
# 6-8  "Move together": four directions, per group
# --------------------------------------------------------------------------

@test("move_together_signatures_four_ways")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    together(page, "Signatures", "up").click(modifiers=["Shift"])
    together(page, "Signatures", "right").click(modifiers=["Shift"])
    together(page, "Signatures", "right").click()
    xs, ys = offsets(page, mock)
    equals(ys, {"sig_1": 2, "sig_2": 2}, "both signatures up 2 pt")
    equals(xs, {"sig_1": 2.5, "sig_2": 2.5}, "both signatures right 2.5 pt")
    together(page, "Signatures", "down").click()
    together(page, "Signatures", "left").click(modifiers=["Shift"])
    xs, ys = offsets(page, mock)
    equals(ys, {"sig_1": 1.5, "sig_2": 1.5}, "down 0.5 pt")
    equals(xs, {"sig_1": 0.5, "sig_2": 0.5}, "left 2 pt")
    state = snapshot(page)
    approx(by_field(state["slots"], "sig_1")["bottomPt"], 198.0, 0.01, "sig_1 box on screen")
    approx(by_field(state["placed"], "sig_2")["leftPt"], 72.5, 0.01, "sig_2 image follows dx")
    approx(by_field(state["slots"], "date_1")["bottomPt"], 199.5, 0.01, "dates untouched")
    equals(panel_number(page, "offset-all-signatures"), 0.0, "global ±20 pt offset untouched")


@test("move_together_dates_four_ways")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    apply_dates_to_all(page)
    together(page, "Dates", "down").click(modifiers=["Shift"])
    together(page, "Dates", "left").click()
    xs, ys = offsets(page, mock)
    equals(ys, {"date_1": -2, "date_2": -2}, "both dates down 2 pt")
    equals(xs, {"date_1": -0.5, "date_2": -0.5}, "both dates left 0.5 pt")
    dates = snapshot(page)["dates"]
    approx(by_field(dates, "date_1")["baselinePt"], 199.5 - 1 + 2, 0.01, "date_1 baseline")
    approx(by_field(dates, "date_2")["xPt"], 349.5, 0.01, "date_2 x")
    together(page, "Dates", "up").click(modifiers=["Shift"])
    together(page, "Dates", "right").click()
    equals(stored_layout(page, mock), None, "back where they started: the layout is default again")


@test("move_together_everything_moves_signatures_and_dates")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    together(page, "Everything", "up").click()
    together(page, "Everything", "right").click(modifiers=["Shift"])
    xs, ys = offsets(page, mock)
    ids = {"sig_1", "sig_2", "date_1", "date_2"}
    equals(set(ys), ids, "every slot got a dy")
    equals(set(xs), ids, "every slot got a dx")
    truthy(all(v == 0.5 for v in ys.values()), f"dy 0.5 everywhere, got {ys}")
    truthy(all(v == 2 for v in xs.values()), f"dx 2 everywhere, got {xs}")
    # the per-slot rows show the same numbers
    equals(panel_number(page, "offset-slot-date_2"), 0.5, "date_2 row dy")
    equals(panel_number(page, "offset-slot-x-sig_1"), 2.0, "sig_1 row dx")


# --------------------------------------------------------------------------
# 9-10  Page edges: each slot stops at its own edge
# --------------------------------------------------------------------------

@test("move_together_clamps_each_slot_at_its_page_edge")
def _(page, mock: MockApi):
    seed_layout(page, mock, {**BASE_LAYOUT, "slot_offsets_x_pt": {"sig_1": -71}})
    open_sign(page, mock, expect_images=2)
    left = together(page, "Signatures", "left")
    left.click(modifiers=["Shift"])
    xs, _ = offsets(page, mock)
    equals(xs.get("sig_1"), -72, "sig_1 stops at x = 0 (bbox.x 72)")
    equals(xs.get("sig_2"), -2, "sig_2 keeps going")
    truthy(left.is_enabled(), "◀ stays on while one signature can still move")
    for _ in range(40):
        if left.is_disabled():
            break
        left.click(modifiers=["Shift"])
    xs, _ = offsets(page, mock)
    equals(xs, {"sig_1": -72, "sig_2": -72}, "both on the left edge")
    truthy(left.is_disabled(), "◀ is off once no signature can move left")
    truthy(together(page, "Signatures", "right").is_enabled(), "▶ still on")
    approx(by_field(snapshot(page)["slots"], "sig_1")["leftPt"], 0.0, 0.01, "box left = 0")
    equals(page.locator('[data-testid="clamp-note"]').count(), 0, "nothing had to be pulled back")


@test("move_together_on_mixed_792_and_817_pt_pages")
def _(page, mock: MockApi):
    """Each slot is clamped to its OWN page: 792 pt on page 1, 817 pt on page 2."""
    mock.set_slots([
        slot("sig_a", "signature", 72, 746, 180, 34, rule_y=780, label="Page one signature", page=1),
        slot("sig_b", "signature", 72, 766, 180, 34, rule_y=800, label="Page two signature", page=2),
    ], signed=[])
    mock.set_pdf(mixed_height_pdf([792, 817]))
    open_sign(page, mock, expect_slots=2)
    page.wait_for_function(
        "() => document.querySelectorAll('.pdf-page').length === 2", timeout=15000,
    )
    heights = page.evaluate(
        "() => [...document.querySelectorAll('.pdf-page')].map((p) => {"
        " const b = p.getBoundingClientRect(); return Math.round(b.height / (b.width / 612)); })"
    )
    equals(heights, [792, 817], "the two pages really are 792 and 817 pt tall")
    down = together(page, "Signatures", "down")
    for _ in range(7):
        down.click(modifiers=["Shift"])
    _, ys = offsets(page, mock)
    equals(ys.get("sig_a"), -12, "page 1: line 780 stops at 792 (-12)")
    equals(ys.get("sig_b"), -14, "page 2 (817 pt) keeps going")
    for _ in range(3):
        if down.is_disabled():
            break
        down.click(modifiers=["Shift"])
    _, ys = offsets(page, mock)
    equals(ys.get("sig_b"), -17, "page 2: line 800 stops at 817 (-17)")
    truthy(down.is_disabled(), "▼ off once both are on their bottom edge")
    slots = snapshot(page)["slots"]
    approx(by_field(slots, "sig_a")["bottomPt"], 792.0, 0.01, "page 1 box bottom = 792")
    approx(by_field(slots, "sig_b")["bottomPt"], 817.0, 0.01, "page 2 box bottom = 817")
    # and back up: both move again
    together(page, "Signatures", "up").click(modifiers=["Shift"])
    _, ys = offsets(page, mock)
    equals(ys, {"sig_a": -10, "sig_b": -15}, "both come back up 2 pt")


# --------------------------------------------------------------------------
# 11  What leaves the page
# --------------------------------------------------------------------------

@test("finalize_body_after_move_together")
def _(page, mock: MockApi):
    """Only the existing per-slot maps change; no new key, global offsets stay 0."""
    open_sign(page, mock, expect_images=2)
    apply_dates_to_all(page)
    together(page, "Everything", "up").click(modifiers=["Shift"])
    together(page, "Everything", "left").click()
    together(page, "Signatures", "down").click()
    page.wait_for_timeout(200)
    page.get_by_role("button", name="Generate final contract").click()
    page.wait_for_url(f"**/contracts/{mock.contract_id}/final", timeout=20000)
    body = mock.bodies.get("finalize")
    truthy(body, "finalize was actually called")
    sent = body["layout"]
    equals(sorted(sent.keys()), sorted([
        "signature_offset_pt", "date_offset_pt", "slot_offsets_pt", "slot_offsets_x_pt",
        "signature_scale", "date_font", "date_font_size",
    ]), "no new key in the finalize layout")
    equals(sent["signature_offset_pt"], 0, "global signature offset unchanged")
    equals(sent["date_offset_pt"], 0, "global date offset unchanged")
    equals(sent["slot_offsets_pt"], {"sig_1": 1.5, "sig_2": 1.5, "date_1": 2, "date_2": 2},
           "vertical map")
    equals(sent["slot_offsets_x_pt"], {"sig_1": -0.5, "sig_2": -0.5, "date_1": -0.5, "date_2": -0.5},
           "horizontal map")


if __name__ == "__main__":
    ensure_target_is_up()
    raise SystemExit(cli_main(test))
