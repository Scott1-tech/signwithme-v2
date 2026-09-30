"""
Rotate to read: does the view really turn, and does everything drawn on the
page turn with it?

The feature is deliberately **view only** -- the file is untouched, no stored
coordinate moves, and the turn is per page, because the case it exists for is
one sheet of a scan fed in sideways. Which makes the dangerous question the
second one: a reader who turns a page and then signs it must not be able to
put a signature where they did not aim.

These checks answer it from both ends:

  * the turn is real, in pixels -- the ink moves to where a clockwise quarter
    turn puts it, the page box changes shape, and the bitmap itself is
    re-rendered at the new shape rather than stretched;
  * the slot boxes land exactly where the same quarter turn puts them (to the
    point, not "about right"), **and** the page refuses to place or drag while
    it is turned, with the reason on screen. The first half is what makes the
    view honest; the second is what makes it safe, because the drag hook and
    the click-to-place both convert pointer travel straight through the render
    scale and know nothing about a turn.

Turning the page four times must leave the document exactly as it was found.

The document is the synthetic multi-page PDF (`_harness.synthetic_multipage_pdf`):
a solid block over the middle of every page plus `Page N of M` in 48 pt
Helvetica near the top. Those two are what make "the ink moved" measurable.
No real contract is ever opened.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (  # noqa: E402
    MockApi,
    Registry,
    approx,
    by_field,
    cli_main,
    drag_slot,
    equals,
    open_review,
    open_sign,
    pixel_bands,
    rule_line,
    snapshot,
    stored_layout,
    synthetic_multipage_pdf,
    truthy,
    zoom_out,
)

test = Registry("rotate to read")

DOC_PAGES = 12

#: Zoom-out presses that bring a whole US-Letter page inside the stage at this
#: viewport (1600 x 1200), so an ink profile is of the *whole* page. It also
#: leaves fit-to-width mode, which is what makes two readings comparable: the
#: scale then stops moving when the page is turned.
FIT_WHOLE_PAGE_CLICKS = 3

#: The exact sentence a reader is shown while a page is turned, on a screen
#: where something can be placed. Changing it breaks this check on purpose.
PLACEMENT_NOTE = "Rotated view — placement disabled; rotate back to place signatures"
#: ... and on a read-only screen (the review page).
READONLY_NOTE = "Rotated view — marks are read-only; rotate back to interact"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def open_doc(page, mock: MockApi, pages: int = DOC_PAGES) -> None:
    mock.set_pdf(synthetic_multipage_pdf(pages, [rule_line(s) for s in mock.slots]))
    open_review(page, mock)
    page.wait_for_function(
        "(n) => document.querySelectorAll('.pdf-page').length === n",
        arg=pages,
        timeout=30000,
    )
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_timeout(400)


def open_signing(page, mock: MockApi, pages: int = DOC_PAGES) -> None:
    mock.set_pdf(synthetic_multipage_pdf(pages, [rule_line(s) for s in mock.slots]))
    open_sign(page, mock, expect_images=2)


def rotate(page, times: int = 1) -> None:
    """Press the toolbar's rotate button, waiting for the repaint each time."""
    for _ in range(times):
        before = page.evaluate(
            "() => document.querySelector('.pdf-page[data-page-number=\"1\"]')"
            "?.getAttribute('data-rotation') || '0'"
        )
        page.locator('[data-testid="pdf-rotate"]').click()
        page.wait_for_function(
            "(was) => (document.querySelector('.pdf-page[data-page-number=\"1\"]')"
            "?.getAttribute('data-rotation') || '0') !== was",
            arg=before,
            timeout=15000,
        )
    # The turn re-fits the document and re-renders the pages in the window.
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=20000)
    page.wait_for_timeout(600)


BOX_JS = """
(pageNumber) => {
  const box = document.querySelector(`.pdf-page[data-page-number="${pageNumber}"]`);
  if (!box) return null;
  const rect = box.getBoundingClientRect();
  const canvas = box.querySelector('canvas.pdf-canvas');
  return {
    rotation: box.getAttribute('data-rotation'),
    interactive: box.classList.contains('pdf-page--interactive'),
    width: Math.round(rect.width),
    height: Math.round(rect.height),
    canvasWidth: canvas ? canvas.width : 0,
    canvasHeight: canvas ? canvas.height : 0,
  };
}
"""


def page_box(page, page_number: int = 1) -> dict:
    box = page.evaluate(BOX_JS, page_number)
    if box is None:
        raise AssertionError(f"page {page_number} is not in the DOM")
    return box


def ink_columns(page, page_number: int = 1, strips: int = 5) -> list[int]:
    """Dark pixels per vertical strip of one page, left to right.

    The synthetic page is black on white, so this is a fingerprint of where
    the ink is -- and a quarter turn moves it in a way a fingerprint shows.
    """
    bands = pixel_bands(page, f'.pdf-page[data-page-number="{page_number}"]', strips)
    return bands["darkStrips"]


def slot_points(page, field_id: str, page_number: int = 1) -> dict:
    """One slot box in PDF points, measured against the page as it is laid out.

    The scale is read off the page box itself, so it works upright (612 pt
    wide) and turned (792 pt wide) alike -- which is exactly why a turned page
    cannot be measured with the `rect.width / 612` every other suite uses.
    """
    found = page.evaluate(
        """(args) => {
          const box = document.querySelector(`.pdf-page[data-page-number="${args.page}"]`);
          const slot = box && box.querySelector(`.slot[data-field-id="${args.field}"]`);
          if (!box || !slot) return null;
          const rect = box.getBoundingClientRect();
          const turned = (box.getAttribute('data-rotation') || '0') !== '0';
          const scale = rect.width / (turned ? 792 : 612);
          const b = slot.getBoundingClientRect();
          const round = (v) => Math.round(v * 100) / 100;
          return {
            scale: round(scale),
            leftPt: round((b.left - rect.left) / scale),
            topPt: round((b.top - rect.top) / scale),
            widthPt: round(b.width / scale),
            heightPt: round(b.height / scale),
          };
        }""",
        {"page": page_number, "field": field_id},
    )
    if found is None:
        raise AssertionError(f"slot {field_id!r} is not on page {page_number}")
    return found


def shot(page, name: str) -> None:
    folder = os.path.join("c:\\", "PTG", ".playwright-mcp", "swm_search")
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, f"{name}.png"))


# --------------------------------------------------------------------------
# The view really turns
# --------------------------------------------------------------------------

@test("rotate_turns_the_page_box")
def _(page, mock: MockApi) -> None:
    """A quarter turn makes a portrait page landscape, and says so."""
    open_doc(page, mock)
    upright = page_box(page)
    truthy(upright["rotation"] in (None, "0"), f"upright at rest ({upright['rotation']})")
    truthy(upright["height"] > upright["width"], f"portrait at rest ({upright})")
    equals(page.locator('[data-testid="pdf-rotation-value"]').count(), 0,
           "no angle is shown at rest")

    rotate(page)
    turned = page_box(page)
    equals(turned["rotation"], "90", "the page records its turn")
    truthy(turned["width"] > turned["height"], f"landscape after a quarter turn ({turned})")
    # US Letter turned is 792 x 612: the box has to be the turned size or the
    # scrollbar lies and every mark inside it is placed against the wrong edge.
    approx(turned["width"] / turned["height"], 792 / 612, 0.02, "turned aspect ratio")
    equals(page.locator('[data-testid="pdf-rotation-value"]').inner_text().strip(), "90°",
           "the toolbar shows the angle")
    shot(page, "rotation_90")


@test("rotation_moves_the_ink")
def _(page, mock: MockApi) -> None:
    """Pixels, not attributes: the page's ink lands where a clockwise turn puts it.

    Upright, the block covers x = 72..540 of 612, so every fifth of the page
    has ink in it. A clockwise quarter turn maps the page's *vertical* axis
    onto the horizontal one back-to-front, so the leftmost fifth of the turned
    page is the bottom fifth of the upright one -- which is blank paper.
    """
    open_doc(page, mock)
    zoom_out(page, FIT_WHOLE_PAGE_CLICKS)
    upright = ink_columns(page)
    truthy(all(value > 0 for value in upright), f"ink across the upright page {upright}")

    rotate(page)
    turned = ink_columns(page)
    truthy(
        turned[0] * 200 < upright[0],
        f"the left edge of a turned page is the blank foot of the upright one "
        f"({turned[0]} vs {upright[0]})",
    )
    truthy(turned[4] > 0, f"and the page number has moved to the right edge {turned}")
    truthy(sum(turned) > sum(upright) * 0.5, f"the ink is still there {turned}")
    shot(page, "rotation_ink")


@test("rotation_repaints_rather_than_stretches")
def _(page, mock: MockApi) -> None:
    """The bitmap itself is re-rendered turned -- no CSS stretch of a portrait."""
    open_doc(page, mock)
    upright = page_box(page)
    truthy(upright["canvasHeight"] > upright["canvasWidth"], f"portrait bitmap {upright}")

    rotate(page)
    turned = page_box(page)
    truthy(turned["canvasWidth"] > turned["canvasHeight"], f"landscape bitmap {turned}")
    # A stretched portrait would keep its pixel count; a re-render at a new
    # fit scale does not, and above all its aspect ratio follows the page.
    approx(
        turned["canvasWidth"] / turned["canvasHeight"], 792 / 612, 0.03,
        "bitmap aspect after the turn",
    )


@test("rotation_is_one_page_at_a_time")
def _(page, mock: MockApi) -> None:
    """Turning the page being read leaves the rest of the document upright."""
    open_doc(page, mock)
    rotate(page)
    equals(page_box(page, 1)["rotation"], "90", "page 1 is turned")
    second = page_box(page, 2)
    truthy(second["rotation"] in (None, "0"), f"page 2 is untouched ({second['rotation']})")
    truthy(second["height"] > second["width"], f"page 2 is still portrait ({second})")
    equals(page.locator(".pdf-page--rotated").count(), 1, "exactly one page is turned")


@test("four_turns_come_back_to_where_it_started")
def _(page, mock: MockApi) -> None:
    """A full circle is a no-op: same box, same ink, no notice, no angle."""
    open_doc(page, mock)
    zoom_out(page, FIT_WHOLE_PAGE_CLICKS)
    before_box = page_box(page)
    before_ink = ink_columns(page)

    rotate(page, 4)

    after_box = page_box(page)
    truthy(after_box["rotation"] in (None, "0"), f"back upright ({after_box['rotation']})")
    equals(after_box["width"], before_box["width"], "page width after a full circle")
    equals(after_box["height"], before_box["height"], "page height after a full circle")
    equals(page.locator('[data-testid="pdf-rotation-note"]').count(), 0, "the notice is gone")
    equals(page.locator('[data-testid="pdf-rotation-value"]').count(), 0, "the angle is gone")

    after_ink = ink_columns(page)
    for index, (was, now) in enumerate(zip(before_ink, after_ink)):
        truthy(
            abs(now - was) <= max(60, was * 0.06),
            f"strip {index} holds the same ink ({was} -> {now}); full profile "
            f"{before_ink} -> {after_ink}",
        )


@test("thumbnail_follows_the_turn")
def _(page, mock: MockApi) -> None:
    """The strip is a picture of the document, so it turns too."""
    open_doc(page, mock)
    page.wait_for_selector('[data-testid="pdf-thumb-1"][data-rendered]', timeout=20000)
    frame = page.locator('[data-testid="pdf-thumb-1"] .pdf-thumb__frame')
    upright = frame.bounding_box()
    truthy(upright["height"] > upright["width"], f"portrait thumbnail {upright}")

    rotate(page)
    page.wait_for_selector('[data-testid="pdf-thumb-1"][data-rendered]', timeout=20000)
    turned = frame.bounding_box()
    truthy(turned["width"] > turned["height"], f"landscape thumbnail {turned}")
    # Page 2's thumbnail is untouched, exactly like page 2 itself.
    second = page.locator('[data-testid="pdf-thumb-2"] .pdf-thumb__frame').bounding_box()
    truthy(second["height"] > second["width"], f"page 2's thumbnail is still portrait {second}")


# --------------------------------------------------------------------------
# What it means for the marks
# --------------------------------------------------------------------------

@test("slot_boxes_land_where_the_turn_puts_them")
def _(page, mock: MockApi) -> None:
    """The overlay turns with the page, to the point.

    A box at (x, y, w, h) on a page W x H points, turned a quarter clockwise,
    is at (H - y - h, x, h, w). That identity -- not "it looks about right" --
    is what says a reader is looking at the real place their signature goes.
    """
    open_signing(page, mock)
    upright = slot_points(page, "sig_1")
    truthy(upright["widthPt"] > upright["heightPt"], f"the slot is a wide box {upright}")

    rotate(page)
    turned = slot_points(page, "sig_1")
    approx(turned["leftPt"], 792 - upright["topPt"] - upright["heightPt"], 1.0,
           "left edge after the turn (pt)")
    approx(turned["topPt"], upright["leftPt"], 1.0, "top edge after the turn (pt)")
    approx(turned["widthPt"], upright["heightPt"], 1.0, "width after the turn (pt)")
    approx(turned["heightPt"], upright["widthPt"], 1.0, "height after the turn (pt)")
    shot(page, "rotation_slots")

    # Every other mark on the page rides the same transform, so the date slot
    # has to agree as well.
    date_up = None
    rotate(page, 3)
    date_up = slot_points(page, "date_1")
    rotate(page)
    date_turned = slot_points(page, "date_1")
    approx(date_turned["leftPt"], 792 - date_up["topPt"] - date_up["heightPt"], 1.0,
           "the date slot follows the same map")


@test("a_turned_page_refuses_to_place_and_says_why")
def _(page, mock: MockApi) -> None:
    """Marks stay correct, but nothing on them may be moved while turned."""
    open_signing(page, mock)
    equals(page.locator('[data-testid="pdf-rotation-note"]').count(), 0,
           "no notice while upright")
    truthy(page_box(page)["interactive"], "the upright page places")

    rotate(page)
    note = page.locator('[data-testid="pdf-rotation-note"]')
    equals(note.count(), 1, "the notice is shown")
    equals(" ".join(note.inner_text().split()), PLACEMENT_NOTE, "the notice's wording")
    truthy(not page_box(page)["interactive"], "a turned page does not place")

    # The shield is above every mark: the middle of a slot belongs to it, so
    # neither a click nor the start of a drag can reach the slot itself.
    equals(page.locator('[data-testid="pdf-rotation-shield-1"]').count(), 1,
           "the page is shielded")
    over_slot = page.evaluate(
        """() => {
          const slot = document.querySelector('.pdf-page .slot[data-field-id="sig_1"]');
          const b = slot.getBoundingClientRect();
          const el = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
          return String(el?.className || el?.tagName || '');
        }"""
    )
    truthy("pdf-overlay__shield" in over_slot, f"the shield is the hit target ({over_slot!r})")

    # And the drag really does nothing: the stored layout does not move, and
    # neither does the box on the page.
    before_layout = stored_layout(page, mock)
    before_slot = slot_points(page, "sig_1")
    drag_slot(page, "sig_1", 30, -20)
    equals(stored_layout(page, mock), before_layout,
           "a drag on a turned page writes nothing")
    equals(slot_points(page, "sig_1"), before_slot,
           "a drag on a turned page moves nothing")


@test("turning_back_restores_the_slots_and_the_placement")
def _(page, mock: MockApi) -> None:
    """Every mark, and the ability to move it, comes back untouched."""
    open_signing(page, mock)
    before = snapshot(page)

    rotate(page, 4)

    after = snapshot(page)
    equals(after["scale"], before["scale"], "render scale after a full circle")
    equals(after["slots"], before["slots"], "slot geometry after a full circle")
    equals(after["placed"], before["placed"], "placed signatures after a full circle")
    truthy(page_box(page)["interactive"], "the page places again")
    equals(page.locator('[data-testid="pdf-rotation-note"]').count(), 0, "no notice left")

    scale = after["scale"]
    drag_slot(page, "sig_1", 30, -20)
    stored = stored_layout(page, mock)
    truthy(stored, "dragging works again after turning back")
    dx = stored["slot_offsets_x_pt"].get("sig_1")
    approx(dx, 30 / scale, 0.35, "dx in points")
    approx(stored["slot_offsets_pt"].get("sig_1"), 20 / scale, 0.35, "dy in points")
    # The offset is applied to the position the slot had *before* the circle,
    # which is what says nothing was quietly re-anchored on the way round.
    equals(
        by_field(snapshot(page)["slots"], "sig_1")["leftPt"],
        round(by_field(before["slots"], "sig_1")["leftPt"] + dx, 2),
        "the slot moved from exactly where it started",
    )


@test("a_read_only_viewer_says_read_only")
def _(page, mock: MockApi) -> None:
    """The review screen has nothing to place, so it is told something else."""
    open_doc(page, mock)
    rotate(page)
    note = page.locator('[data-testid="pdf-rotation-note"]')
    equals(note.count(), 1, "the notice is shown on the review screen too")
    equals(" ".join(note.inner_text().split()), READONLY_NOTE, "the read-only wording")


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
