"""
Baseline: "Position & appearance" — slot boxes, mouse dragging, the steppers,
persistence and what finalize sends.

30 checks (the two `swm.dates.<id>` checks were added with W0). Every one of them records how the app behaves **today**; a red
result is either a regression or a stale expectation here, never a licence to
change `frontend/src`.

Run alone:      python tests/ui/test_layout_drag.py
Run one check:  python tests/ui/test_layout_drag.py --only drag_clamps
"""

from __future__ import annotations

import json
import math
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
    contains,
    dblclick_slot,
    default_slots,
    drag_slot,
    ensure_target_is_up,
    equals,
    goto_blank,
    open_sign,
    panel_number,
    panel_value,
    press_without_dragging,
    seed_layout,
    slot,
    slot_stepper,
    snapshot,
    stored_layout,
    truthy,
)

test = Registry("layout + drag")

# `lib/layout.ts` constants mirrored here so a drift shows up as a failure.
OFFSET_STEP = 0.5
OFFSET_STEP_LARGE = 2.0
SIGNATURE_INK_GAP = 1.0
DATE_INK_GAP = 1.0


def js_round(value: float) -> int:
    """JS `Math.round` (half away from zero upwards), not Python's banker's rounding."""
    return math.floor(value + 0.5)


def snap(value: float) -> float:
    return js_round(value / OFFSET_STEP) * OFFSET_STEP


def is_half_point(value: float) -> bool:
    return abs(value * 2 - round(value * 2)) < 1e-9


def global_stepper(page, which: str, direction: str):
    return page.get_by_role("button", name=f"{which}: move {direction}")


def layout_panel(page):
    return page.locator('[data-testid="layout-panel"]')


# --------------------------------------------------------------------------
# 1-4  Where a mark sits before anything is moved
# --------------------------------------------------------------------------

@test("slot_box_sits_on_the_detected_rule")
def _(page, mock: MockApi):
    """A slot with `rule_y` stands its bottom edge exactly on that rule."""
    open_sign(page, mock, expect_images=2)
    state = snapshot(page)
    sig = by_field(state["slots"], "sig_1")
    date = by_field(state["slots"], "date_1")
    approx(sig["bottomPt"], 199.5, 0.01, "sig_1 data-bottom-pt")
    approx(sig["leftPt"], 72.0, 0.01, "sig_1 data-left-pt")
    approx(date["bottomPt"], 199.5, 0.01, "date_1 data-bottom-pt")
    approx(date["leftPt"], 350.0, 0.01, "date_1 data-left-pt")
    # and the rendered box agrees with the numbers it advertises
    approx(sig["domBottomPt"], 199.5, 0.6, "sig_1 rendered bottom")
    approx(sig["domLeftPt"], 72.0, 0.6, "sig_1 rendered left")


@test("slot_box_without_a_rule_uses_the_bbox_bottom")
def _(page, mock: MockApi):
    """`rule_y = null` falls back to `bbox.y + bbox.height`, not to the raw bbox top."""
    open_sign(page, mock, expect_images=2)
    state = snapshot(page)
    sig = by_field(state["slots"], "sig_2")
    date = by_field(state["slots"], "date_2")
    approx(sig["bottomPt"], 394.0, 0.01, "sig_2 data-bottom-pt (160+34 style fallback)")
    approx(date["bottomPt"], 400.0, 0.01, "date_2 data-bottom-pt")
    approx(sig["domHeightPt"], 34.0, 0.6, "sig_2 box height = bbox height")


@test("placed_signature_stands_one_point_above_the_rule")
def _(page, mock: MockApi):
    """The image is fitted into `bbox x scale` and its ink bottom sits at line - 1 pt."""
    open_sign(page, mock, expect_images=2)
    state = snapshot(page)
    equals([p["tag"] for p in state["placed"]], ["img", "img"], "both placements are <img>")
    first = by_field(state["placed"], "sig_1")
    second = by_field(state["placed"], "sig_2")
    approx(first["bottomPt"], 199.5 - SIGNATURE_INK_GAP, 0.01, "sig_1 image bottom")
    approx(first["leftPt"], 72.0, 0.01, "sig_1 image left")
    approx(second["bottomPt"], 394.0 - SIGNATURE_INK_GAP, 0.01, "sig_2 image bottom")
    # 300x100 natural, contained in 180x34 -> 102 x 34 pt
    approx(first["widthPt"], 102.0, 1.0, "fitted width")
    approx(first["heightPt"], 34.0, 1.0, "fitted height")


@test("date_text_baseline_sits_one_point_above_the_rule")
def _(page, mock: MockApi):
    """`.placed-dates text` carries x / y / font-size as ATTRIBUTES, scaled by the viewer."""
    open_sign(page, mock, expect_images=2)
    apply_dates_to_all(page)
    state = snapshot(page)
    equals(len(state["dates"]), 2, "one <text> per filled date slot")
    first = by_field(state["dates"], "date_1")
    approx(first["baselinePt"], 199.5 - DATE_INK_GAP, 0.01, "date_1 data-baseline-pt")
    approx(first["xPt"], 350.0, 0.01, "date_1 data-x-pt")
    approx(first["attrY"], (199.5 - DATE_INK_GAP) * state["scale"], 0.6, "y attribute")
    approx(first["attrX"], 350.0 * state["scale"], 0.6, "x attribute")
    approx(first["attrFontSize"], 11.0 * state["scale"], 0.6, "font-size attribute")
    truthy(len(first["text"]) == 10 and first["text"][2] == "/", "date prints as MM/DD/YYYY")


# --------------------------------------------------------------------------
# 5-12  Mouse dragging
# --------------------------------------------------------------------------

@test("drag_moves_a_slot_and_keeps_dx_and_dy")
def _(page, mock: MockApi):
    """Pointer travel becomes dx (right positive) and dy (UP positive) in PDF points."""
    open_sign(page, mock, expect_images=2)
    scale = snapshot(page)["scale"]
    drag_slot(page, "sig_1", 30, -20)
    stored = stored_layout(page, mock)
    truthy(stored, "a layout was written to localStorage")
    dx = stored["slot_offsets_x_pt"].get("sig_1")
    dy = stored["slot_offsets_pt"].get("sig_1")
    truthy(dx and dx > 0, f"dx is positive (moved right), got {dx}")
    truthy(dy and dy > 0, f"dy is positive (moved UP), got {dy}")
    approx(dx, 30 / scale, 0.35, "dx in points")
    approx(dy, 20 / scale, 0.35, "dy in points")


@test("drag_below_three_pixels_stays_an_ordinary_click")
def _(page, mock: MockApi):
    """DRAG_THRESHOLD_PX = 3: a 2 px press must not write any offset."""
    open_sign(page, mock, expect_images=2)
    before = by_field(snapshot(page)["slots"], "date_1")
    press_without_dragging(page, "date_1", travel_px=2)
    equals(stored_layout(page, mock), None, "no layout saved by a 2 px press")
    after = by_field(snapshot(page)["slots"], "date_1")
    equals(after["leftPt"], before["leftPt"], "date_1 left unchanged")
    equals(after["bottomPt"], before["bottomPt"], "date_1 bottom unchanged")


@test("drag_snaps_to_the_half_point_grid")
def _(page, mock: MockApi):
    """Dragged positions land on the same 0.5 pt grid the steppers use."""
    open_sign(page, mock, expect_images=2)
    drag_slot(page, "sig_1", 37, -23)
    stored = stored_layout(page, mock)
    dx = stored["slot_offsets_x_pt"]["sig_1"]
    dy = stored["slot_offsets_pt"]["sig_1"]
    truthy(is_half_point(dx), f"dx {dx} is a multiple of 0.5")
    truthy(is_half_point(dy), f"dy {dy} is a multiple of 0.5")


@test("drag_preview_matches_the_dom_after_release")
def _(page, mock: MockApi):
    """After release the box reports `bbox.x + dx` and `line - dy`, to the centipoint."""
    open_sign(page, mock, expect_images=2)
    drag_slot(page, "sig_1", 40, -15)
    stored = stored_layout(page, mock)
    dx = stored["slot_offsets_x_pt"]["sig_1"]
    dy = stored["slot_offsets_pt"]["sig_1"]
    state = snapshot(page)
    box = by_field(state["slots"], "sig_1")
    image = by_field(state["placed"], "sig_1")
    approx(box["leftPt"], 72.0 + dx, 0.01, "box left = bbox.x + dx")
    approx(box["bottomPt"], 199.5 - dy, 0.01, "box bottom = line - dy")
    approx(image["leftPt"], 72.0 + dx, 0.01, "image left follows the same dx")
    approx(image["bottomPt"], 199.5 - SIGNATURE_INK_GAP - dy, 0.01, "image bottom = line - 1 - dy")


@test("drag_clamps_to_the_left_page_edge")
def _(page, mock: MockApi):
    """dx can never take a slot past x = 0; -200 pt is pulled back to -bbox.x."""
    seed_layout(page, mock, {
        "signature_offset_pt": 0, "date_offset_pt": 0,
        "slot_offsets_pt": {}, "slot_offsets_x_pt": {"sig_1": -200},
        "signature_scale": 1, "date_font": "Helvetica", "date_font_size": 11,
    })
    open_sign(page, mock, expect_images=2)
    page.wait_for_selector('[data-testid="clamp-note"]', timeout=15000)
    stored = stored_layout(page, mock)
    approx(stored["slot_offsets_x_pt"]["sig_1"], -72.0, 0.01, "dx clamped to -bbox.x")
    approx(by_field(snapshot(page)["slots"], "sig_1")["leftPt"], 0.0, 0.01, "box left edge = 0")


@test("drag_clamps_to_the_right_page_edge")
def _(page, mock: MockApi):
    """dx stops where the mark would print past 612 pt."""
    seed_layout(page, mock, {
        "signature_offset_pt": 0, "date_offset_pt": 0,
        "slot_offsets_pt": {}, "slot_offsets_x_pt": {"date_1": 200},
        "signature_scale": 1, "date_font": "Helvetica", "date_font_size": 11,
    })
    open_sign(page, mock, expect_images=2)
    page.wait_for_selector('[data-testid="clamp-note"]', timeout=15000)
    stored = stored_layout(page, mock)
    # 612 - bbox.x(350) - width(120) = 142
    approx(stored["slot_offsets_x_pt"]["date_1"], 142.0, 0.01, "dx clamped to the right edge")
    approx(by_field(snapshot(page)["slots"], "date_1")["leftPt"], 492.0, 0.01, "box left = 350 + 142")


@test("drag_clamps_to_the_bottom_page_edge")
def _(page, mock: MockApi):
    """A slot low on the page cannot be pushed below 792 pt."""
    low = default_slots() + [
        slot("sig_low", "signature", 72, 666, 180, 34, rule_y=700,
             label="Low signature", required=False),
    ]
    mock.set_slots(low, signed=[])
    seed_layout(page, mock, {
        "signature_offset_pt": 0, "date_offset_pt": 0,
        "slot_offsets_pt": {"sig_low": -200}, "slot_offsets_x_pt": {},
        "signature_scale": 1, "date_font": "Helvetica", "date_font_size": 11,
    })
    open_sign(page, mock, expect_slots=5)
    page.wait_for_selector('[data-testid="clamp-note"]', timeout=15000)
    stored = stored_layout(page, mock)
    # line(700) - pageHeight(792) = -92
    approx(stored["slot_offsets_pt"]["sig_low"], -92.0, 0.01, "dy clamped to the bottom edge")
    approx(by_field(snapshot(page)["slots"], "sig_low")["bottomPt"], 792.0, 0.01,
           "box bottom = page bottom")


@test("double_click_puts_a_slot_back")
def _(page, mock: MockApi):
    """Double-clicking a dragged slot clears both its dx and its dy."""
    open_sign(page, mock, expect_images=2)
    drag_slot(page, "sig_1", 35, -18)
    truthy(stored_layout(page, mock), "an offset exists before the reset")
    dblclick_slot(page, "sig_1")
    equals(stored_layout(page, mock), None, "the layout is default again, so the key is removed")
    box = by_field(snapshot(page)["slots"], "sig_1")
    approx(box["leftPt"], 72.0, 0.01, "left back to bbox.x")
    approx(box["bottomPt"], 199.5, 0.01, "bottom back on the rule")


# --------------------------------------------------------------------------
# 13-19  Steppers
# --------------------------------------------------------------------------

@test("slot_stepper_up_moves_half_a_point")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    slot_stepper(page, "sig_1", "up").click()
    equals(panel_number(page, "offset-slot-sig_1"), 0.5, "panel value")
    contains(panel_value(page, "offset-slot-sig_1"), "y +0.5 pt", "panel label")
    approx(by_field(snapshot(page)["slots"], "sig_1")["bottomPt"], 199.0, 0.01, "box moved up 0.5")


@test("slot_stepper_down_moves_half_a_point")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    slot_stepper(page, "sig_1", "down").click()
    equals(panel_number(page, "offset-slot-sig_1"), -0.5, "panel value")
    approx(by_field(snapshot(page)["slots"], "sig_1")["bottomPt"], 200.0, 0.01, "box moved down 0.5")


@test("slot_steppers_left_and_right_move_half_a_point")
def _(page, mock: MockApi):
    """The horizontal pair is a separate testid (`offset-slot-x-<id>`)."""
    open_sign(page, mock, expect_images=2)
    slot_stepper(page, "sig_1", "right").click()
    equals(panel_number(page, "offset-slot-x-sig_1"), 0.5, "x value after a right nudge")
    approx(by_field(snapshot(page)["slots"], "sig_1")["leftPt"], 72.5, 0.01, "box moved right 0.5")
    slot_stepper(page, "sig_1", "left").click()
    slot_stepper(page, "sig_1", "left").click()
    equals(panel_number(page, "offset-slot-x-sig_1"), -0.5, "x value after two left nudges")
    approx(by_field(snapshot(page)["slots"], "sig_1")["leftPt"], 71.5, 0.01, "box moved left 0.5")


@test("shift_click_on_a_stepper_moves_two_points")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    slot_stepper(page, "sig_1", "up").click(modifiers=["Shift"])
    equals(panel_number(page, "offset-slot-sig_1"), OFFSET_STEP_LARGE, "shift step")
    approx(by_field(snapshot(page)["slots"], "sig_1")["bottomPt"], 197.5, 0.01, "box moved up 2 pt")


@test("all_signatures_offset_applies_to_every_signature")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    global_stepper(page, "All signatures", "up").click()
    global_stepper(page, "All signatures", "up").click()
    equals(panel_number(page, "offset-all-signatures"), 1.0, "global signature offset")
    state = snapshot(page)
    approx(by_field(state["slots"], "sig_1")["bottomPt"], 198.5, 0.01, "sig_1 up 1 pt")
    approx(by_field(state["slots"], "sig_2")["bottomPt"], 393.0, 0.01, "sig_2 up 1 pt")
    approx(by_field(state["slots"], "date_1")["bottomPt"], 199.5, 0.01, "dates untouched")


@test("all_dates_offset_applies_to_every_date")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    apply_dates_to_all(page)
    global_stepper(page, "All dates", "down").click()
    equals(panel_number(page, "offset-all-dates"), -0.5, "global date offset")
    state = snapshot(page)
    approx(by_field(state["dates"], "date_1")["baselinePt"], 199.0, 0.01, "date_1 baseline down 0.5")
    approx(by_field(state["dates"], "date_2")["baselinePt"], 399.5, 0.01, "date_2 baseline down 0.5")
    approx(by_field(state["slots"], "sig_1")["bottomPt"], 199.5, 0.01, "signatures untouched")


@test("slot_offset_adds_on_top_of_the_global_offset")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    global_stepper(page, "All signatures", "up").click()
    global_stepper(page, "All signatures", "up").click()
    slot_stepper(page, "sig_1", "up").click()
    state = snapshot(page)
    approx(by_field(state["slots"], "sig_1")["bottomPt"], 198.0, 0.01, "1.0 global + 0.5 slot")
    approx(by_field(state["slots"], "sig_2")["bottomPt"], 393.0, 0.01, "sig_2 only gets the global")


# --------------------------------------------------------------------------
# 20-22  Size and date typography
# --------------------------------------------------------------------------

@test("signature_size_scales_the_image_but_not_the_line")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    before = by_field(snapshot(page)["placed"], "sig_1")
    page.locator('input[aria-label="Signature size"]').fill("150")
    page.wait_for_timeout(200)
    after = by_field(snapshot(page)["placed"], "sig_1")
    approx(before["widthPt"], 102.0, 1.0, "width at 100 %")
    approx(after["widthPt"], 153.0, 1.5, "width at 150 %")
    approx(after["heightPt"], 51.0, 1.0, "height at 150 %")
    approx(after["bottomPt"], 198.5, 0.01, "the ink still stands on the same line")
    contains(layout_panel(page).inner_text(), "150%", "the panel shows the percentage")


@test("signature_size_is_limited_to_60_150_percent")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    control = page.locator('input[aria-label="Signature size"]')
    equals(control.get_attribute("min"), "60", "minimum")
    equals(control.get_attribute("max"), "150", "maximum")
    equals(control.get_attribute("step"), "5", "step")
    # Both ends are reachable and land in the stored layout as a 0.6 / 1.5 scale.
    control.fill("60")
    page.wait_for_timeout(150)
    equals(stored_layout(page, mock)["signature_scale"], 0.6, "minimum scale")
    control.fill("150")
    page.wait_for_timeout(150)
    equals(stored_layout(page, mock)["signature_scale"], 1.5, "maximum scale")


@test("date_font_and_size_apply_to_the_printed_text")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    apply_dates_to_all(page)
    page.locator('select[aria-label="Date font"]').select_option("Courier")
    page.locator('input[aria-label="Date size"]').fill("14")
    page.wait_for_timeout(200)
    state = snapshot(page)
    first = by_field(state["dates"], "date_1")
    approx(first["attrFontSize"], 14.0 * state["scale"], 0.6, "font-size attribute follows the slider")
    contains(first["fontFamily"], "Courier", "font-family")
    equals(stored_layout(page, mock)["date_font"], "Courier", "stored font id (PDF base-14 name)")
    equals(stored_layout(page, mock)["date_font_size"], 14, "stored size")


# --------------------------------------------------------------------------
# 23-26  Persistence
# --------------------------------------------------------------------------

@test("reset_restores_the_defaults_and_clears_storage")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    global_stepper(page, "All signatures", "up").click()
    slot_stepper(page, "sig_1", "right").click()
    page.locator('input[aria-label="Signature size"]').fill("150")
    page.wait_for_timeout(150)
    truthy(stored_layout(page, mock), "something was stored")
    layout_panel(page).get_by_role("button", name="Reset").click()
    page.wait_for_timeout(200)
    equals(stored_layout(page, mock), None, "the key is removed once the layout is default")
    equals(panel_number(page, "offset-all-signatures"), 0.0, "global signature offset")
    equals(panel_number(page, "offset-slot-x-sig_1"), 0.0, "per-slot x offset")
    equals(page.locator('input[aria-label="Signature size"]').input_value(), "100", "size")
    equals(page.locator('select[aria-label="Date font"]').input_value(), "Helvetica", "font")
    approx(by_field(snapshot(page)["slots"], "sig_1")["bottomPt"], 199.5, 0.01, "back on the rule")


@test("layout_is_saved_under_swm_layout_contract_id")
def _(page, mock: MockApi):
    """Exactly the shape `POST /finalize` expects, keyed per contract."""
    open_sign(page, mock, expect_images=2)
    global_stepper(page, "All signatures", "up").click()
    slot_stepper(page, "sig_1", "right").click()
    page.locator('select[aria-label="Date font"]').select_option("Times-Roman")
    page.wait_for_timeout(200)
    stored = stored_layout(page, mock)
    equals(sorted(stored.keys()), sorted([
        "signature_offset_pt", "date_offset_pt", "slot_offsets_pt",
        "slot_offsets_x_pt", "signature_scale", "date_font", "date_font_size",
    ]), "stored keys")
    equals(stored["signature_offset_pt"], 0.5, "global signature offset")
    equals(stored["slot_offsets_x_pt"], {"sig_1": 0.5}, "x map holds only non-zero entries")
    equals(stored["slot_offsets_pt"], {}, "y map stays empty")
    equals(stored["date_font"], "Times-Roman", "font")
    # the key is per contract
    other = page.evaluate("() => Object.keys(localStorage).filter((k) => k.startsWith('swm.layout.'))")
    equals(other, [f"swm.layout.{mock.contract_id}"], "one key, this contract only")


@test("layout_survives_a_reload")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    global_stepper(page, "All signatures", "up").click()
    slot_stepper(page, "sig_1", "up").click()
    page.locator('input[aria-label="Signature size"]').fill("150")
    page.locator('input[aria-label="Date size"]').fill("13")
    page.wait_for_timeout(200)
    before = stored_layout(page, mock)

    open_sign(page, mock, expect_images=2)
    after = stored_layout(page, mock)
    equals(after, before, "stored layout unchanged by the reload")
    equals(panel_number(page, "offset-all-signatures"), 0.5, "global offset restored")
    equals(panel_number(page, "offset-slot-sig_1"), 0.5, "slot offset restored")
    equals(page.locator('input[aria-label="Signature size"]').input_value(), "150", "size restored")
    equals(page.locator('input[aria-label="Date size"]').input_value(), "13", "date size restored")
    approx(by_field(snapshot(page)["slots"], "sig_1")["bottomPt"], 198.5, 0.01,
           "the page is drawn with the restored offsets")


def stored_dates(page, mock: MockApi):
    raw = page.evaluate("(key) => localStorage.getItem(key)", f"swm.dates.{mock.contract_id}")
    return json.loads(raw) if raw else None


@test("dates_survive_a_reload")
def _(page, mock: MockApi):
    """`swm.dates.<id>`: the chosen date of every slot is there again after a reload."""
    open_sign(page, mock, expect_images=2)
    equals(stored_dates(page, mock), None, "nothing is stored before a date is chosen")
    equals(page.locator(".placed-dates text").count(), 0, "a fresh page has no dates")
    apply_dates_to_all(page)
    page.wait_for_timeout(200)
    stored = stored_dates(page, mock)
    equals(sorted(stored.keys()), ["date_1", "date_2"], "one entry per date slot")
    iso = stored["date_1"]
    equals(stored["date_2"], iso, "the same date in both")
    shown = sorted(t["text"] for t in snapshot(page)["dates"])

    open_sign(page, mock, expect_images=2)
    page.wait_for_function(
        "() => document.querySelectorAll('.placed-dates text').length === 2", timeout=15000,
    )
    equals(sorted(t["text"] for t in snapshot(page)["dates"]), shown, "the same dates are drawn")
    equals(stored_dates(page, mock), stored, "the stored map is unchanged by the reload")

    page.get_by_role("button", name="Generate final contract").click()
    page.wait_for_url(f"**/contracts/{mock.contract_id}/final", timeout=20000)
    sent = sorted((d["field_id"], d["value"]) for d in mock.bodies["finalize"]["dates"])
    equals(sent, [("date_1", iso), ("date_2", iso)], "finalize sends the restored dates")


@test("a_broken_stored_date_is_dropped")
def _(page, mock: MockApi):
    """What is stored is never trusted: bad entries vanish, the good one beside them stays."""
    goto_blank(page, mock)
    page.evaluate(
        "([key, value]) => localStorage.setItem(key, value)",
        [f"swm.dates.{mock.contract_id}", json.dumps({
            "date_1": "2026-10-01", "date_2": "next tuesday", "sig_1": 7, "": "2026-01-01",
        })],
    )
    open_sign(page, mock, expect_images=2)
    page.wait_for_function(
        "() => document.querySelectorAll('.placed-dates text').length === 1", timeout=15000,
    )
    dates = snapshot(page)["dates"]
    equals([(d["field"], d["text"]) for d in dates], [("date_1", "10/01/2026")], "only the valid entry")
    date_2 = page.locator('.pdf-page .slot[data-field-id="date_2"]').inner_text()
    contains(date_2, "Date needed", "the broken one reads as empty")

    # A value that is not JSON at all is an empty map, not a broken page.
    page.evaluate(
        "([key, value]) => localStorage.setItem(key, value)",
        [f"swm.dates.{mock.contract_id}", "{not json"],
    )
    open_sign(page, mock, expect_images=2)
    page.wait_for_timeout(300)
    equals(page.locator(".placed-dates text").count(), 0, "unreadable storage starts empty")
    equals(mock.page_errors, [], "no page error")


@test("a_layout_saved_before_the_x_map_still_loads")
def _(page, mock: MockApi):
    """Pre-contract-v2 entries have no `slot_offsets_x_pt`; that must read as "no moves"."""
    seed_layout(page, mock, {
        "signature_offset_pt": 1,
        "date_offset_pt": -0.5,
        "slot_offsets_pt": {"sig_1": 2},
        "signature_scale": 1.2,
        "date_font": "Times-Roman",
        "date_font_size": 12,
    })
    open_sign(page, mock, expect_images=2)
    equals(mock.page_errors, [], "no uncaught error while loading the legacy layout")
    equals(panel_number(page, "offset-slot-x-sig_1"), 0.0, "missing x map reads as 0")
    equals(panel_number(page, "offset-slot-sig_1"), 2.0, "the y map is honoured")
    equals(panel_number(page, "offset-all-signatures"), 1.0, "global offset honoured")
    approx(by_field(snapshot(page)["slots"], "sig_1")["bottomPt"], 196.5, 0.01,
           "line - (global 1 + slot 2)")
    approx(by_field(snapshot(page)["slots"], "sig_1")["leftPt"], 72.0, 0.01, "no horizontal move")


# --------------------------------------------------------------------------
# 27-28  What leaves the page
# --------------------------------------------------------------------------

@test("finalize_body_carries_both_slot_offset_maps")
def _(page, mock: MockApi):
    open_sign(page, mock, expect_images=2)
    apply_dates_to_all(page)
    slot_stepper(page, "sig_1", "up").click()
    slot_stepper(page, "date_1", "left").click()
    page.wait_for_timeout(200)
    stored = stored_layout(page, mock)

    page.get_by_role("button", name="Generate final contract").click()
    page.wait_for_url(f"**/contracts/{mock.contract_id}/final", timeout=20000)

    body = mock.bodies.get("finalize")
    truthy(body, "finalize was actually called")
    sent = body["layout"]
    equals(sent["slot_offsets_pt"], {"sig_1": 0.5}, "vertical map")
    equals(sent["slot_offsets_x_pt"], {"date_1": -0.5}, "horizontal map (contract v2)")
    equals(sent["signature_offset_pt"], stored["signature_offset_pt"], "global signature offset")
    equals(sent["signature_scale"], stored["signature_scale"], "signature scale")
    equals(sent["date_font"], stored["date_font"], "date font")
    equals(sent["date_font_size"], stored["date_font_size"], "date font size")
    equals(len(body["dates"]), 2, "one value per date slot")
    equals(body["apply_paired_dates"], False, "dates are sent explicitly, not inferred")


@test("clamp_note_appears_and_then_clears")
def _(page, mock: MockApi):
    """An out-of-range stored offset is pulled back in and the user is told once."""
    seed_layout(page, mock, {
        "signature_offset_pt": 0, "date_offset_pt": 0,
        "slot_offsets_pt": {}, "slot_offsets_x_pt": {"sig_1": -200},
        "signature_scale": 1, "date_font": "Helvetica", "date_font_size": 11,
    })
    open_sign(page, mock, expect_images=2)
    note = page.locator('[data-testid="clamp-note"]')
    note.wait_for(state="visible", timeout=15000)
    contains(note.inner_text(), "1 slot was moved to stay on the page", "clamp note text")
    # any later change to the layout clears it
    global_stepper(page, "All dates", "up").click()
    note.wait_for(state="detached", timeout=10000)
    equals(page.locator('[data-testid="clamp-note"]').count(), 0, "note is gone after a change")


if __name__ == "__main__":
    ensure_target_is_up()
    raise SystemExit(cli_main(test))
