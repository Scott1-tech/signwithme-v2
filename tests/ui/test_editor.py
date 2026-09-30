"""
The full-screen document editor: shell, selection, undo/redo, and all five
things it can put on a page.

WHAT THESE CHECKS ARE FOR
-------------------------
The editor is a *second* screen over the same viewer the signing page uses, and
the whole point of building it that way was that the 94 checks already in this
directory stay true. So these checks are deliberately about the new layer only:

  * an annotation lands where the pointer landed, in **PDF points**, and each
    kind lands the way its contract says -- a run of text and a date on their
    **baseline**, a tick and a cross on their **centre**. Those are the numbers
    the server will print with, so "about right" is not good enough and every
    assertion here is in points;
  * a mark can be selected, dragged, resized, retyped (where it has text),
    and deleted, and each of those is exactly **one** step of the history -- a
    drag across the page is one Ctrl+Z, not forty;
  * a size stops where its own kind stops: 48 pt for a run, 72 pt for a mark,
    because a tick has to be able to fill a tick-box;
  * nothing ever leaves the page: a drag past an edge is clamped, because the
    server answers 422 to a coordinate outside its page and a refusal the user
    could not see coming is a bug, not a limit;
  * what is stored survives a reload, and what is stored is never trusted: a
    hand-edited entry is dropped in silence, the good ones beside it are kept,
    and a mark is stored with no `text` and no `font` key at all;
  * a **free signature is not an annotation**. It is a row created by the
    unchanged `POST /api/contracts/{id}/signatures` with `field_id: null`, so
    it cannot be dragged and Undo does not reach it -- and the interface has to
    say so rather than merely be true;
  * the editor is one link away from the dashboard, the review screen and the
    signing screen, and adding those links moved nothing that was there.

Nothing here opens a real contract: the document is the synthetic one-page PDF
from `_harness.py` and every `/api/*` call is answered by `MockApi`.
"""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (  # noqa: E402
    BASE,
    MockApi,
    Registry,
    apply_dates_to_all,
    approx,
    arm_tool,
    cli_main,
    contains,
    equals,
    goto_blank,
    missing,
    open_review,
    open_sign,
    truthy,
    zoom_out,
)

test = Registry("document editor")

#: US Letter, the size every mock page uses (`_harness.PAGE_W/H`).
PAGE_W = 612.0
PAGE_H = 792.0

#: Mirrors of what the code owns. If one of these moves, this file is the
#: other half of the change.
SIZE_MIN = 6.0                    # lib/annotations.ts
SIZE_MAX = 48.0                   # lib/annotations.ts
MARK_SIZE_MIN = 6.0               # lib/annotations.ts
MARK_SIZE_MAX = 72.0              # lib/annotations.ts
DEFAULT_SIZE = 11.0               # lib/annotations.ts
DEFAULT_MARK_SIZE = 16.0          # lib/annotations.ts
GRID_PT = 0.5                     # lib/annotations.ts
NUDGE_STEP = 0.5                  # lib/annotations.ts
NUDGE_STEP_LARGE = 2.0            # lib/annotations.ts
#: components/PdfViewer.tsx -- the box a free placement is given, centred on
#: the click. The editor hands it straight to the placement endpoint.
PLACEMENT_W = 180.0
PLACEMENT_H = 34.0

#: How far a placed point may honestly sit from the pixel that was clicked.
#: `clientX` / `clientY` are whole CSS pixels, and the result is then snapped
#: to the 0.5 pt grid, so one pixel (~0.8 pt at the fitted scale) plus a
#: quarter of a grid step is the floor of what any click-to-place check can
#: assert. Still far inside a point, which is the precision that matters here.
CLICK_TOL_PT = 0.8

#: Every tool on the toolbar. All of them work.
TOOLS = ["select", "text", "check", "cross", "signature", "date"]

STORAGE_KEY = "swm.annotations.ZZ-UI-1"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def open_editor(page, mock: MockApi, seed: str | None = None) -> None:
    """Open the editor, optionally on a pre-seeded `localStorage` entry."""
    if seed is not None:
        goto_blank(page, mock)
        page.evaluate(
            "([key, value]) => localStorage.setItem(key, value)",
            [f"swm.annotations.{mock.contract_id}", seed],
        )
    page.goto(f"{BASE}/contracts/{mock.contract_id}/edit")
    page.wait_for_selector('[data-testid="editor-toolbar"]', timeout=30000)
    page.wait_for_function(
        "() => { const p = document.querySelector('.pdf-page');"
        " return !!p && p.getBoundingClientRect().width > 300; }",
        timeout=30000,
    )
    # The initial fit re-renders at the fitted scale; settle so no measurement
    # lands between the two paints.
    page.wait_for_timeout(350)


def page_rect(page) -> dict:
    box = page.locator('.pdf-page[data-page-number="1"]').bounding_box()
    if box is None:
        raise AssertionError("page 1 has no box")
    return box


def scale_of(page) -> float:
    return page_rect(page)["width"] / PAGE_W


MARKS_JS = """
() => [...document.querySelectorAll('.swm-ann')].map((el) => {
  const rect = el.getBoundingClientRect();
  const id = el.dataset.annId;
  const text = document.querySelector(`.swm-ann__text[data-ann-id="${id}"]`);
  const mark = document.querySelector(`.swm-ann__mark[data-ann-id="${id}"]`);
  return {
    id,
    kind: el.dataset.kind,
    page: Number(el.dataset.page),
    x: Number(el.dataset.xPt),
    y: Number(el.dataset.yPt),
    size: Number(el.dataset.sizePt),
    font: el.dataset.font === undefined ? null : el.dataset.font,
    selected: el.dataset.selected === 'true',
    left: rect.left, top: rect.top, width: rect.width, height: rect.height,
    text: text ? text.textContent : null,
    svgX: text ? Number(text.getAttribute('x')) : null,
    svgY: text ? Number(text.getAttribute('y')) : null,
    svgSize: text ? Number(text.getAttribute('font-size')) : null,
    fontFamily: text ? text.style.fontFamily : null,
    // A mark is drawn by one `translate + scale`, so these three numbers are
    // the whole of its geometry: centre x, centre y, side -- all in CSS px.
    cx: mark ? Number(mark.dataset.cx) : null,
    cy: mark ? Number(mark.dataset.cy) : null,
    side: mark ? Number(mark.dataset.side) : null,
    strokes: mark ? mark.querySelectorAll('path').length : 0,
  };
})
"""

SIGS_JS = """
() => [...document.querySelectorAll('.swm-sig')].map((el) => {
  const img = el.querySelector('img.swm-sig__image');
  const remove = el.querySelector('.swm-sig__remove');
  return {
    id: el.dataset.signatureId,
    page: Number(el.dataset.page),
    x: Number(el.dataset.xPt), y: Number(el.dataset.yPt),
    w: Number(el.dataset.wPt), h: Number(el.dataset.hPt),
    image: img ? img.getAttribute('src') : null,
    removeLabel: remove ? remove.getAttribute('aria-label') : null,
    title: el.getAttribute('title'),
    // What a draggable mark has and this deliberately has not.
    grab: getComputedStyle(el).cursor,
  };
})
"""


def marks(page) -> list[dict]:
    return page.evaluate(MARKS_JS)


def sigs(page) -> list[dict]:
    return page.evaluate(SIGS_JS)


def only_mark(page) -> dict:
    found = marks(page)
    if len(found) != 1:
        raise AssertionError(f"expected exactly one annotation, got {len(found)}: {found}")
    return found[0]


def stored(page) -> list | None:
    raw = page.evaluate("(key) => localStorage.getItem(key)", STORAGE_KEY)
    return json.loads(raw) if raw else None


def depth(page, which: str) -> int:
    return int(page.locator(f'[data-testid="{which}-depth"]').inner_text().strip())


def wait_for_marks(page, count: int) -> None:
    page.wait_for_function(
        "(n) => document.querySelectorAll('.swm-ann').length === n",
        arg=count,
        timeout=15000,
    )


def wait_for_sigs(page, count: int) -> None:
    page.wait_for_function(
        "(n) => document.querySelectorAll('.swm-sig').length === n",
        arg=count,
        timeout=20000,
    )


def place_text(page, value: str, x_px: float, y_px: float, expect: int = 1) -> dict:
    """Arm the text tool, click that spot on page 1, type and commit."""
    arm_tool(page, "text")
    box = page_rect(page)
    page.mouse.click(box["x"] + x_px, box["y"] + y_px)
    page.wait_for_selector('[data-testid="ann-input"]', timeout=10000)
    page.keyboard.type(value)
    page.keyboard.press("Enter")
    wait_for_marks(page, expect)
    page.wait_for_timeout(120)
    return marks(page)[expect - 1]


def place_mark(page, kind: str, x_px: float, y_px: float, expect: int = 1) -> dict:
    """Arm ✔ or ✖ and click. One click is the whole gesture -- no box opens."""
    arm_tool(page, kind)
    box = page_rect(page)
    page.mouse.click(box["x"] + x_px, box["y"] + y_px)
    wait_for_marks(page, expect)
    page.wait_for_timeout(120)
    return marks(page)[expect - 1]


def place_date(page, x_px: float, y_px: float, expect: int = 1) -> dict:
    """Arm the date tool and click. The date was chosen on the toolbar first."""
    arm_tool(page, "date")
    box = page_rect(page)
    page.mouse.click(box["x"] + x_px, box["y"] + y_px)
    wait_for_marks(page, expect)
    page.wait_for_timeout(120)
    return marks(page)[expect - 1]


def open_signature_chooser(page, x_px: float, y_px: float) -> None:
    arm_tool(page, "signature")
    box = page_rect(page)
    page.mouse.click(box["x"] + x_px, box["y"] + y_px)
    page.wait_for_selector(".modal__panel", timeout=15000)


def draw_on_pad(page) -> None:
    """Put one stroke on the chooser's canvas, so `Apply signature` wakes up."""
    box = page.locator(".signature-pad__canvas").bounding_box()
    if box is None:
        raise AssertionError("the signature pad has no box")
    mid = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + 30, mid)
    page.mouse.down()
    page.mouse.move(box["x"] + 120, mid - 22, steps=8)
    page.mouse.move(box["x"] + 230, mid + 18, steps=8)
    page.mouse.up()
    page.wait_for_timeout(150)


def select_tool(page) -> None:
    page.locator('[data-testid="tool-select"]').click()


def click_mark(page, ann_id: str) -> None:
    page.locator(f'[data-testid="ann-{ann_id}"]').click()
    page.wait_for_timeout(120)


def drag_mark(page, ann_id: str, dx: float, dy: float, steps: int = 10) -> None:
    """Press an annotation and drag it. The first 4 px cross `DRAG_THRESHOLD_PX`."""
    handle = page.locator(f'[data-testid="ann-{ann_id}"]')
    box = handle.bounding_box()
    if box is None:
        raise AssertionError(f"annotation {ann_id!r} has no box")
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + 4, cy + 4)
    page.mouse.move(cx + dx, cy + dy, steps=steps)
    page.mouse.up()
    page.wait_for_timeout(150)


def drag_handle(page, dx: float, dy: float, steps: int = 10) -> None:
    """Press the corner grip of the selected annotation and drag it."""
    grip = page.locator('[data-testid="ann-handle"]')
    box = grip.bounding_box()
    if box is None:
        raise AssertionError("no corner handle on screen")
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + dx, cy + dy, steps=steps)
    page.mouse.up()
    page.wait_for_timeout(150)


def expected_size(start_size: float, box: dict, dx: float, dy_up: float,
                  ceiling: float = SIZE_MAX) -> float:
    """Mirror of the corner handle's maths (`hooks/useAnnotationEdit.ts`)."""
    ratio = (
        (box["width"] + dx) / box["width"] + (box["height"] + dy_up) / box["height"]
    ) / 2
    snapped = round(start_size * ratio / GRID_PT) * GRID_PT
    return min(ceiling, max(SIZE_MIN, snapped))


def shot(page, name: str) -> None:
    folder = os.path.join("c:\\", "PTG", ".playwright-mcp", "swm_editor")
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, f"{name}.png"))


def shot2(page, name: str) -> None:
    """Part 2's own folder, so the two sets never overwrite one another."""
    folder = os.path.join("c:\\", "PTG", ".playwright-mcp", "swm_editor2")
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, f"{name}.png"))


def clean(page, mock: MockApi) -> None:
    """No uncaught error, and no API call the mock does not know about."""
    equals(mock.page_errors, [], "no uncaught error")
    equals(mock.unmocked, [], "no unmocked API call")


def serve_one_contract(page, mock: MockApi) -> None:
    """Answer `GET /api/contracts` with one row, so the dashboard has a row.

    Registered *after* `MockApi.install`, and Playwright tries routes newest
    first -- so this wins for that one path and every other call still goes to
    the shared router.
    """
    row = {
        **mock.contract_payload(),
        "template_name": "ZZ UI Template",
        "can_sign": True,
        "critical_missing_count": 0,
        "critical_inconsistency_count": 0,
        "critical_fmcsa_count": 0,
    }
    page.route(
        lambda url: urlsplit(url).path == "/api/contracts",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps([row])
        ),
    )


# --------------------------------------------------------------------------
# The shell
# --------------------------------------------------------------------------

@test("editor_opens_as_its_own_screen")
def _(page, mock: MockApi) -> None:
    """Thumbnails on the left, tools on top, the document in the middle."""
    open_editor(page, mock)
    # W1: `/edit` is the single workspace (it was its own `document-editor`
    # screen); the editor is the workspace opened on its Objects section.
    workspace = page.locator('[data-testid="workspace"]')
    equals(workspace.count(), 1, "the editor route renders the workspace")
    equals(workspace.get_attribute("data-panel"), "edit", "opened on its edit panel")
    equals(page.locator('[data-testid="document-editor"]').count(), 0, "no second editor screen")
    equals(page.locator('[data-testid="editor-toolbar"]').count(), 1, "one toolbar")
    # The strip and the viewer chrome are the EXISTING components, reused whole.
    equals(page.locator('[data-testid="pdf-thumbs"]').count(), 1, "the page strip is there")
    equals(page.locator('[data-testid="pdf-thumb-1"]').count(), 1, "page 1 has a thumbnail")
    truthy(page.locator(".pdf-toolbar__zoom").inner_text().endswith("%"), "the zoom control is there")
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=20000)

    equals(marks(page), [], "nothing is annotated yet")
    equals(sigs(page), [], "and nothing is signed on it freely")
    contains(page.locator('[data-testid="ann-count"]').inner_text(), "0 / 500", "the counter starts empty")
    equals(depth(page, "undo"), 0, "no history yet")
    truthy(page.locator('[data-testid="editor-undo"]').is_disabled(), "undo is disabled at the start")
    truthy(page.locator('[data-testid="editor-redo"]').is_disabled(), "redo is disabled at the start")
    clean(page, mock)
    shot(page, "editor_shell")


@test("every_tool_is_live_and_says_what_it_does")
def _(page, mock: MockApi) -> None:
    """All six tools work: no disabled button, no "part 2" badge left anywhere."""
    open_editor(page, mock)
    for name in TOOLS:
        button = page.locator(f'[data-testid="tool-{name}"]')
        equals(button.count(), 1, f"the {name} tool is on the toolbar")
        truthy(button.is_enabled(), f"the {name} tool works")
        equals(button.get_attribute("data-part"), None, f"{name} carries no part marker")
        label = button.get_attribute("aria-label") or ""
        truthy(len(label) > 0, f"{name} has an accessible name")
        missing(label, "part 2", f"{name} no longer defers to a later part")
        missing(label, "Coming", f"{name} is not announced as coming")

    equals(page.locator(".editor__tool-badge").count(), 0, "no tool wears a part-2 badge")
    equals(page.locator(".editor__tool:disabled").count(), 0, "no tool is disabled")
    missing(page.locator('[data-testid="editor-toolbar"]').inner_text(),
            "Coming in part 2", "and the phrase is gone from the toolbar")

    # Each of the four new ones names the gesture, not just itself.
    contains(page.locator('[data-testid="tool-check"]').get_attribute("aria-label") or "",
             "centred", "the tick says the click is its centre")
    contains(page.locator('[data-testid="tool-cross"]').get_attribute("aria-label") or "",
             "centred", "the cross says the same")
    contains(page.locator('[data-testid="tool-signature"]').get_attribute("aria-label") or "",
             "signature", "the signature tool names itself")
    contains(page.locator('[data-testid="tool-date"]').get_attribute("aria-label") or "",
             "date", "and the date tool names itself")

    # Select is the tool a screen starts on.
    equals(
        page.locator('[data-testid="tool-select"]').get_attribute("aria-pressed"),
        "true",
        "the editor opens in Select",
    )
    clean(page, mock)
    shot2(page, "toolbar_all_live")


# --------------------------------------------------------------------------
# Placing text
# --------------------------------------------------------------------------

@test("text_tool_places_a_run_where_the_pointer_landed")
def _(page, mock: MockApi) -> None:
    """The click point becomes the run's baseline, in PDF points."""
    open_editor(page, mock)
    scale = scale_of(page)
    mark = place_text(page, "N/A", 200, 300)

    approx(mark["x"], round(200 / scale / GRID_PT) * GRID_PT, 0.3, "x of the placed run (pt)")
    approx(mark["y"], round(300 / scale / GRID_PT) * GRID_PT, 0.3, "y of the placed run (pt)")
    equals(mark["text"], "N/A", "the text that was typed")
    equals(mark["kind"], "text", "the kind")
    equals(mark["page"], 1, "on the page that was clicked")
    approx(mark["size"], DEFAULT_SIZE, 0.01, "the default size")
    equals(mark["font"], "Helvetica", "the default font")
    # Drawn on its baseline, at the render scale: the same two numbers the PDF
    # builder will use, multiplied in exactly one place.
    approx(mark["svgX"], mark["x"] * scale, 0.5, "the SVG x is the point at scale")
    approx(mark["svgY"], mark["y"] * scale, 0.5, "the SVG y is the baseline at scale")
    approx(mark["svgSize"], mark["size"] * scale, 0.5, "the SVG size is the size at scale")

    truthy(mark["selected"], "the new run is selected")
    contains(page.locator('[data-testid="ann-count"]').inner_text(), "1 / 500", "the counter")
    equals(depth(page, "undo"), 1, "placing is one step")
    clean(page, mock)
    shot(page, "editor_text_placed")


@test("an_empty_run_is_never_placed")
def _(page, mock: MockApi) -> None:
    """Escape, and a box left blank, both leave the contract exactly as it was."""
    open_editor(page, mock)
    arm_tool(page, "text")
    box = page_rect(page)

    page.mouse.click(box["x"] + 150, box["y"] + 200)
    page.wait_for_selector('[data-testid="ann-input"]')
    page.keyboard.type("throw away")
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    equals(marks(page), [], "Escape places nothing")

    page.mouse.click(box["x"] + 150, box["y"] + 260)
    page.wait_for_selector('[data-testid="ann-input"]')
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    equals(marks(page), [], "an empty box places nothing")
    equals(depth(page, "undo"), 0, "and neither is a step of the history")
    equals(stored(page), None, "nothing is stored either")
    clean(page, mock)


# --------------------------------------------------------------------------
# Placing the marks
# --------------------------------------------------------------------------

@test("check_and_cross_land_centred_on_the_click")
def _(page, mock: MockApi) -> None:
    """One click is the whole gesture, and the point is the CENTRE of the mark."""
    open_editor(page, mock)
    scale = scale_of(page)
    page_box = page_rect(page)

    tick = place_mark(page, "check", 220, 300)
    equals(tick["kind"], "check", "the kind that was armed")
    equals(tick["page"], 1, "on the page that was clicked")
    equals(page.locator('[data-testid="ann-input"]').count(), 0, "no box opens for a mark")
    approx(tick["x"], round(220 / scale / GRID_PT) * GRID_PT, CLICK_TOL_PT, "centre x (pt)")
    approx(tick["y"], round(300 / scale / GRID_PT) * GRID_PT, CLICK_TOL_PT, "centre y (pt)")
    approx(tick["size"], DEFAULT_MARK_SIZE, 0.01, "a mark's own default size")

    # The drawing: one `translate + scale`, so these three numbers are the whole
    # of the geometry and every one of them is the point through the scale.
    approx(tick["cx"], tick["x"] * scale, 0.5, "the drawn centre x is the point at scale")
    approx(tick["cy"], tick["y"] * scale, 0.5, "the drawn centre y is the point at scale")
    approx(tick["side"], tick["size"] * scale, 0.5, "the side is the size at scale")
    equals(tick["strokes"], 1, "a tick is one stroke")
    equals(tick["text"], None, "a mark has no text run at all")
    equals(tick["font"], None, "and no font")

    # ...and the hit surface is centred on the same point, not hung off it.
    approx(tick["left"] + tick["width"] / 2 - page_box["x"], tick["x"] * scale, 1.0,
           "the hit box is centred on the point (x)")
    approx(tick["top"] + tick["height"] / 2 - page_box["y"], tick["y"] * scale, 1.0,
           "the hit box is centred on the point (y)")

    truthy(tick["selected"], "the new mark is selected")
    equals(depth(page, "undo"), 1, "placing a mark is one step")

    # What is stored carries no `text` and no `font` key: the server's model for
    # a mark has neither, and an unexpected key is a 422.
    entry = (stored(page) or [])[0]
    equals(sorted(entry.keys()), ["id", "kind", "page", "size_pt", "x_pt", "y_pt"],
           "a stored mark's keys are exactly the contract's")
    equals(entry["kind"], "check", "the stored kind")
    approx(entry["x_pt"], tick["x"], 0.01, "the stored centre x")

    cross = place_mark(page, "cross", 320, 380, expect=2)
    equals(cross["kind"], "cross", "the second mark is a cross")
    equals(cross["strokes"], 1, "a cross is one path with two strokes in it")
    approx(cross["cx"], cross["x"] * scale, 0.5, "drawn centred too")
    equals(depth(page, "undo"), 2, "two placements, two steps")
    contains(page.locator('[data-testid="ann-count"]').inner_text(), "2 / 500", "the counter")
    clean(page, mock)
    shot2(page, "marks_placed")


@test("a_mark_and_a_run_stop_at_different_ceilings")
def _(page, mock: MockApi) -> None:
    """6…72 pt for a tick (a tick-box is bigger than a line of type), 6…48 for text."""
    open_editor(page, mock)
    place_mark(page, "check", 200, 300)

    size_box = page.locator('[data-testid="ann-size"]')
    equals(size_box.get_attribute("data-kind"), "check", "the box describes the selected mark")
    equals(size_box.get_attribute("min"), str(int(MARK_SIZE_MIN)), "a mark's floor")
    equals(size_box.get_attribute("max"), str(int(MARK_SIZE_MAX)), "a mark's ceiling")
    contains(size_box.get_attribute("aria-label") or "", "72 pt", "and the name says so")

    # A size only a mark may have.
    size_box.fill("60")
    page.wait_for_timeout(180)
    approx(only_mark(page)["size"], 60, 0.01, "60 pt is a legal mark")
    approx(only_mark(page)["side"], 60 * scale_of(page), 0.6, "and it is drawn that big")

    # The grip stops exactly on each limit, not near it.
    drag_handle(page, 500, -500)
    approx(only_mark(page)["size"], MARK_SIZE_MAX, 0.01, "a mark's ceiling is exact")
    drag_handle(page, -500, 500)
    approx(only_mark(page)["size"], MARK_SIZE_MIN, 0.01, "and so is its floor")

    # A run of text has the smaller ceiling, on the same control.
    page.locator('[data-testid="ann-delete"]').click()
    wait_for_marks(page, 0)
    place_text(page, "Tall", 260, 400)
    equals(size_box.get_attribute("data-kind"), "text", "the box now describes the run")
    equals(size_box.get_attribute("max"), str(int(SIZE_MAX)), "a run's ceiling")
    drag_handle(page, 500, -500)
    approx(only_mark(page)["size"], SIZE_MAX, 0.01, "a run stops at 48 pt")

    # And the font box goes with the kind: a mark has no font to change.
    font_box = page.locator('[data-testid="ann-font"]')
    truthy(font_box.is_enabled(), "a run of text has a font")
    place_mark(page, "cross", 340, 460, expect=2)
    truthy(font_box.is_disabled(), "a mark has none, and the control says so")
    contains(font_box.get_attribute("aria-label") or "", "no font", "in words, not just in grey")
    clean(page, mock)
    shot2(page, "mark_sizes")


# --------------------------------------------------------------------------
# The free date
# --------------------------------------------------------------------------

@test("the_date_tool_drops_the_date_that_was_chosen")
def _(page, mock: MockApi) -> None:
    """Chosen on the toolbar, printed on the page, and editable afterwards."""
    open_editor(page, mock)
    picker = page.locator('[data-testid="ann-date"]')
    equals(picker.count(), 1, "the date lives on the toolbar, not in the page")
    # No automatic date (2026-09-25): it used to start on the UTC "today",
    # which is yesterday between 00:00 and 05:00 in Tashkent.
    equals(picker.input_value(), "", "it starts empty -- the date is always chosen by hand")

    picker.fill("2026-03-09")
    page.wait_for_timeout(120)
    scale = scale_of(page)
    placed = place_date(page, 260, 340)

    equals(placed["kind"], "date", "its own kind, not a plain text run")
    equals(placed["text"], "03/09/2026", "printed the way the contract prints a date")
    approx(placed["x"], round(260 / scale / GRID_PT) * GRID_PT, CLICK_TOL_PT, "x of the run (pt)")
    approx(placed["y"], round(340 / scale / GRID_PT) * GRID_PT, CLICK_TOL_PT, "y of the run (pt)")
    # Same geometry as a text run: the point is the baseline, not a centre.
    approx(placed["svgX"], placed["x"] * scale, 0.5, "drawn from the baseline's left end")
    approx(placed["svgY"], placed["y"] * scale, 0.5, "on the baseline itself")
    approx(placed["size"], DEFAULT_SIZE, 0.01, "the same default size as text")
    equals(placed["font"], "Helvetica", "and the same font")
    equals(depth(page, "undo"), 1, "one click, one step")

    entry = (stored(page) or [])[0]
    equals(entry["kind"], "date", "stored as a date")
    equals(entry["text"], "03/09/2026", "with the value in its text")
    equals(sorted(entry.keys()),
           ["font", "id", "kind", "page", "size_pt", "text", "x_pt", "y_pt"],
           "a date carries the same keys a run does")

    # It stays an ordinary run afterwards: double-click and retype it.
    select_tool(page)
    page.locator(f'[data-testid="ann-{placed["id"]}"]').dblclick()
    box = page.wait_for_selector('[data-testid="ann-input"]', timeout=10000)
    equals(box.input_value(), "03/09/2026", "the box opens on the date that is there")
    page.keyboard.type("03/10/2026")
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    retyped = only_mark(page)
    equals(retyped["text"], "03/10/2026", "a free date can be corrected by hand")
    equals(retyped["kind"], "date", "and it is still a date")
    equals(retyped["id"], placed["id"], "the same annotation, not a new one")
    clean(page, mock)
    shot2(page, "free_date")


# --------------------------------------------------------------------------
# The free signature (a SERVER row, not an annotation)
# --------------------------------------------------------------------------

@test("a_free_signature_goes_through_the_unchanged_placement_endpoint")
def _(page, mock: MockApi) -> None:
    """`field_id: null` + a bbox centred on the click; drawn from the image route."""
    open_editor(page, mock)
    scale = scale_of(page)
    open_signature_chooser(page, 300, 400)

    # The existing chooser, imported whole: three tabs, opening on Draw because
    # this user's library is empty.
    equals(page.locator(".modal__panel").count(), 1, "one signature dialog")
    equals(page.locator('.sign-modal__tabs [role="tab"]').count(), 3, "its three tabs")
    equals(
        page.locator('[role="tab"][aria-selected="true"]').get_attribute("data-tab"),
        "draw",
        "an empty library opens on Draw",
    )

    draw_on_pad(page)
    page.get_by_role("button", name="Apply signature").click()
    wait_for_sigs(page, 1)

    body = mock.bodies.get("place")
    truthy(body is not None, "the placement endpoint was called")
    equals(body["field_id"], None, "a free placement belongs to no template slot")
    equals(body["page"], 1, "on the page that was clicked")
    truthy(
        isinstance(body["image_data"], str) and body["image_data"].startswith("data:image/png;base64,"),
        "the drawn PNG travelled as image_data",
    )
    # The box is the placement box, centred on the click, in PDF points.
    approx(body["bbox"]["width"], PLACEMENT_W, 0.01, "the placement width (pt)")
    approx(body["bbox"]["height"], PLACEMENT_H, 0.01, "the placement height (pt)")
    approx(body["bbox"]["x"], 300 / scale - PLACEMENT_W / 2, 1.0, "centred on the click (x)")
    approx(body["bbox"]["y"], 400 / scale - PLACEMENT_H / 2, 1.0, "centred on the click (y)")

    # It is drawn from the server's answer, through the image route.
    page.wait_for_function(
        "() => { const i = document.querySelector('img.swm-sig__image');"
        " return !!i && i.complete && i.naturalWidth > 0; }",
        timeout=20000,
    )
    drawn = sigs(page)[0]
    equals(drawn["id"], "zz-sig-new", "the row the server created")
    truthy((drawn["image"] or "").startswith("blob:"), "its picture came from the image route")
    approx(drawn["w"], PLACEMENT_W, 0.01, "drawn at the box the server reported")

    # THE LIMITATION, stated rather than merely true.
    equals(drawn["grab"], "default", "nothing about it invites a drag")
    contains(drawn["title"] or "", "cannot be dragged", "the signature says so")
    contains(drawn["removeLabel"] or "", "Remove this signature and place it again",
             "and the way out is named in full")
    contains(drawn["removeLabel"] or "", "Undo does not reach it",
             "including what Undo will not do for it")
    equals(depth(page, "undo"), 0, "a server row is not a step of the annotation history")
    contains(page.locator('[data-testid="ann-count"]').inner_text(), "0 / 500",
             "and it is not one of the 500 annotations either")
    contains(page.locator('[data-testid="sig-count"]').inner_text(), "1 signature",
             "it is counted separately")
    clean(page, mock)
    shot2(page, "free_signature")

    # Ctrl+Z must not reach across to the server.
    page.keyboard.press("Control+z")
    page.wait_for_timeout(300)
    equals(len(sigs(page)), 1, "Undo leaves a placed signature exactly where it is")

    # Remove and place again: the DELETE really goes out, and the page says
    # what to do next.
    page.locator('[data-testid="ann-sig-remove-zz-sig-new"]').click()
    wait_for_sigs(page, 0)
    truthy(
        any(call.startswith("DELETE /api/contracts/") for call in mock.calls),
        f"the row was deleted server-side ({mock.calls})",
    )
    contains(page.locator('[data-testid="editor-note"]').inner_text(), "place again",
             "and the next step is spelled out")
    clean(page, mock)


# --------------------------------------------------------------------------
# Selecting, moving, resizing, retyping, deleting
# --------------------------------------------------------------------------

@test("a_run_can_be_selected_and_let_go")
def _(page, mock: MockApi) -> None:
    """Click it to select (frame + corner grip); click the paper to drop it."""
    open_editor(page, mock)
    mark = place_text(page, "Initials", 200, 300)
    select_tool(page)
    page.wait_for_timeout(100)

    # Clicking empty paper in Select mode deselects, and places nothing.
    box = page_rect(page)
    page.mouse.click(box["x"] + 500, box["y"] + 650)
    page.wait_for_timeout(150)
    truthy(not only_mark(page)["selected"], "clicking the paper deselects")
    equals(page.locator('[data-testid="ann-handle"]').count(), 0, "and takes the grip away")
    equals(len(marks(page)), 1, "Select mode never places a run")

    click_mark(page, mark["id"])
    truthy(only_mark(page)["selected"], "clicking the run selects it")
    equals(page.locator('[data-testid="ann-handle"]').count(), 1, "the corner grip is back")
    truthy(page.locator('[data-testid="ann-delete"]').is_enabled(), "Delete applies to it")
    clean(page, mock)


@test("dragging_moves_the_run_by_what_the_pointer_travelled")
def _(page, mock: MockApi) -> None:
    """Pointer travel in CSS pixels, through the render scale, onto the 0.5 pt grid."""
    open_editor(page, mock)
    mark = place_text(page, "Moved", 200, 300)
    select_tool(page)
    scale = scale_of(page)

    drag_mark(page, mark["id"], 120, -60)
    moved = only_mark(page)
    approx(moved["x"], round((mark["x"] + 120 / scale) / GRID_PT) * GRID_PT, 0.3, "x after the drag")
    approx(moved["y"], round((mark["y"] - 60 / scale) / GRID_PT) * GRID_PT, 0.3, "y after the drag")
    approx(moved["svgX"], moved["x"] * scale, 0.5, "the drawing followed")

    saved = stored(page)
    truthy(saved and len(saved) == 1, f"one annotation is stored ({saved})")
    approx(saved[0]["x_pt"], moved["x"], 0.01, "the stored x is the shown x")
    equals(depth(page, "undo"), 2, "place + move = two steps, however long the drag was")
    clean(page, mock)
    shot(page, "editor_dragged")


@test("dragging_moves_a_mark_by_its_centre")
def _(page, mock: MockApi) -> None:
    """The same gesture, the same grid -- and the centre is what travels."""
    open_editor(page, mock)
    tick = place_mark(page, "check", 220, 320)
    select_tool(page)
    scale = scale_of(page)

    drag_mark(page, tick["id"], 90, 70)
    moved = only_mark(page)
    approx(moved["x"], round((tick["x"] + 90 / scale) / GRID_PT) * GRID_PT, CLICK_TOL_PT,
           "centre x after the drag")
    approx(moved["y"], round((tick["y"] + 70 / scale) / GRID_PT) * GRID_PT, CLICK_TOL_PT,
           "centre y after the drag")
    approx(moved["cx"], moved["x"] * scale, 0.5, "the drawing followed its centre")
    approx(moved["size"], tick["size"], 0.01, "a move never changes the size")
    equals(depth(page, "undo"), 2, "place + move = two steps")

    # And it cannot leave the page, exactly as a run cannot.
    drag_mark(page, moved["id"], -600, -600)
    pinned = only_mark(page)
    approx(pinned["x"], 0, 0.01, "the centre stops at the left edge")
    approx(pinned["y"], 0, 0.01, "and at the top edge")
    clean(page, mock)


@test("the_corner_grip_resizes_the_run")
def _(page, mock: MockApi) -> None:
    """Up and to the right is bigger; the baseline's left end never moves."""
    open_editor(page, mock)
    mark = place_text(page, "Size me", 200, 400)
    before = only_mark(page)

    grown = expected_size(before["size"], before, 60, 60)
    drag_handle(page, 60, -60)
    after = only_mark(page)
    truthy(after["size"] > before["size"], f"the run grew ({before['size']} -> {after['size']})")
    approx(after["size"], grown, 1.0, "the size the handle's ratio asks for")
    # The anchor: the baseline point is untouched by a resize.
    approx(after["x"], before["x"], 0.01, "x is unchanged by a resize")
    approx(after["y"], before["y"], 0.01, "y is unchanged by a resize")
    equals(depth(page, "undo"), 2, "place + resize = two steps")

    # Far enough the other way and it stops at the floor, exactly.
    drag_handle(page, -200, 200)
    approx(only_mark(page)["size"], SIZE_MIN, 0.01, "the size floor is exact")
    clean(page, mock)
    shot(page, "editor_resized")


@test("the_corner_grip_resizes_a_mark_around_its_centre")
def _(page, mock: MockApi) -> None:
    """A mark grows from its middle: the stored point does not budge."""
    open_editor(page, mock)
    place_mark(page, "cross", 240, 360)
    before = only_mark(page)

    grown = expected_size(before["size"], before, 40, 40, ceiling=MARK_SIZE_MAX)
    drag_handle(page, 40, -40)
    after = only_mark(page)
    truthy(after["size"] > before["size"], f"the mark grew ({before['size']} -> {after['size']})")
    approx(after["size"], grown, 1.5, "the size the handle's ratio asks for")
    approx(after["x"], before["x"], 0.01, "the centre x is unchanged by a resize")
    approx(after["y"], before["y"], 0.01, "the centre y is unchanged by a resize")
    approx(after["cx"], before["cx"], 0.6, "and the drawing stayed put")
    approx(after["side"], after["size"] * scale_of(page), 0.6, "only the side grew")
    equals(depth(page, "undo"), 2, "place + resize = two steps")
    clean(page, mock)


@test("double_click_retypes_a_run")
def _(page, mock: MockApi) -> None:
    """The box opens with the old text selected, so typing replaces it."""
    open_editor(page, mock)
    mark = place_text(page, "before", 220, 320)
    select_tool(page)

    page.locator(f'[data-testid="ann-{mark["id"]}"]').dblclick()
    box = page.wait_for_selector('[data-testid="ann-input"]', timeout=10000)
    equals(box.input_value(), "before", "the box opens on the text that is there")
    page.keyboard.type("after")
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)

    retyped = only_mark(page)
    equals(retyped["text"], "after", "the run now reads what was typed")
    equals(retyped["id"], mark["id"], "it is the same annotation, not a new one")
    approx(retyped["x"], mark["x"], 0.01, "and it did not move")
    equals(depth(page, "undo"), 2, "place + retype = two steps")

    # A mark has nothing to retype: double-clicking one selects it and stops.
    tick = place_mark(page, "check", 320, 420, expect=2)
    select_tool(page)
    page.locator(f'[data-testid="ann-{tick["id"]}"]').dblclick()
    page.wait_for_timeout(250)
    equals(page.locator('[data-testid="ann-input"]').count(), 0, "no box opens on a mark")
    equals(len(marks(page)), 2, "and nothing was placed by the second click")
    clean(page, mock)


@test("delete_takes_a_run_off_the_page")
def _(page, mock: MockApi) -> None:
    """The Delete key and the toolbar button do the same thing."""
    open_editor(page, mock)
    first = place_text(page, "one", 200, 300)
    select_tool(page)
    click_mark(page, first["id"])
    page.keyboard.press("Delete")
    wait_for_marks(page, 0)
    equals(stored(page), None, "an empty list leaves no entry behind")

    second = place_text(page, "two", 260, 360)
    click_mark(page, second["id"])
    page.locator('[data-testid="ann-delete"]').click()
    wait_for_marks(page, 0)
    truthy(page.locator('[data-testid="ann-delete"]').is_disabled(), "Delete needs a selection")

    # The same two routes out for a mark.
    third = place_mark(page, "check", 300, 420)
    select_tool(page)
    click_mark(page, third["id"])
    page.keyboard.press("Delete")
    wait_for_marks(page, 0)
    equals(stored(page), None, "and a mark leaves no entry behind either")
    clean(page, mock)


# --------------------------------------------------------------------------
# Undo / redo
# --------------------------------------------------------------------------

@test("undo_and_redo_cover_every_kind_of_change")
def _(page, mock: MockApi) -> None:
    """Place, move, resize, retype, delete -- five steps, back and forward again."""
    open_editor(page, mock)
    placed = place_text(page, "history", 200, 300)
    select_tool(page)

    drag_mark(page, placed["id"], 100, 40)
    moved = only_mark(page)
    drag_handle(page, 50, -50)
    resized = only_mark(page)
    page.locator(f'[data-testid="ann-{placed["id"]}"]').dblclick()
    page.wait_for_selector('[data-testid="ann-input"]')
    page.keyboard.type("rewritten")
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    retyped = only_mark(page)
    page.locator('[data-testid="ann-delete"]').click()
    wait_for_marks(page, 0)
    equals(depth(page, "undo"), 5, "five steps were recorded")

    undo = page.locator('[data-testid="editor-undo"]')
    undo.click()   # the delete
    wait_for_marks(page, 1)
    equals(only_mark(page)["text"], "rewritten", "undo brings the run back as it was")
    undo.click()   # the retype
    page.wait_for_timeout(120)
    equals(only_mark(page)["text"], "history", "undo restores the old text")
    undo.click()   # the resize
    page.wait_for_timeout(120)
    approx(only_mark(page)["size"], moved["size"], 0.01, "undo restores the old size")
    undo.click()   # the move
    page.wait_for_timeout(120)
    approx(only_mark(page)["x"], placed["x"], 0.01, "undo restores the old x")
    approx(only_mark(page)["y"], placed["y"], 0.01, "undo restores the old y")
    undo.click()   # the placement
    wait_for_marks(page, 0)
    equals(depth(page, "undo"), 0, "the history is back at the beginning")
    equals(depth(page, "redo"), 5, "and everything is ahead of it")
    truthy(undo.is_disabled(), "there is nothing left to undo")

    redo = page.locator('[data-testid="editor-redo"]')
    redo.click()
    wait_for_marks(page, 1)
    approx(only_mark(page)["x"], placed["x"], 0.01, "redo puts the run back")
    redo.click()
    page.wait_for_timeout(120)
    approx(only_mark(page)["x"], moved["x"], 0.01, "redo replays the move")
    redo.click()
    page.wait_for_timeout(120)
    approx(only_mark(page)["size"], resized["size"], 0.01, "redo replays the resize")
    redo.click()
    page.wait_for_timeout(120)
    equals(only_mark(page)["text"], retyped["text"], "redo replays the retype")
    redo.click()
    wait_for_marks(page, 0)
    truthy(page.locator('[data-testid="editor-redo"]').is_disabled(), "and nothing is left ahead")
    clean(page, mock)


@test("undo_and_redo_cover_the_tick_the_cross_and_the_date")
def _(page, mock: MockApi) -> None:
    """Each new kind is one step of the same history, in and out again."""
    open_editor(page, mock)
    page.locator('[data-testid="ann-date"]').fill("2026-07-04")
    page.wait_for_timeout(120)

    tick = place_mark(page, "check", 200, 300)
    cross = place_mark(page, "cross", 280, 340, expect=2)
    dated = place_date(page, 360, 380, expect=3)
    equals(depth(page, "undo"), 3, "three placements, three steps")
    equals([m["kind"] for m in marks(page)], ["check", "cross", "date"], "all three kinds are on the page")

    # A nudge and a resize on a mark are steps of their own.
    select_tool(page)
    click_mark(page, tick["id"])
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(150)
    equals(depth(page, "undo"), 4, "nudging a mark is a step")
    approx(marks(page)[0]["x"], tick["x"] + NUDGE_STEP, 0.01, "and it really moved")

    undo = page.locator('[data-testid="editor-undo"]')
    undo.click()
    page.wait_for_timeout(150)
    approx(marks(page)[0]["x"], tick["x"], 0.01, "undo takes the nudge back")
    undo.click()
    wait_for_marks(page, 2)
    equals([m["kind"] for m in marks(page)], ["check", "cross"], "undo removes the date")
    undo.click()
    wait_for_marks(page, 1)
    equals(only_mark(page)["kind"], "check", "undo removes the cross")
    undo.click()
    wait_for_marks(page, 0)
    equals(depth(page, "undo"), 0, "the history is back at the beginning")

    redo = page.locator('[data-testid="editor-redo"]')
    redo.click()
    wait_for_marks(page, 1)
    approx(only_mark(page)["x"], tick["x"], 0.01, "redo puts the tick back where it was")
    approx(only_mark(page)["size"], tick["size"], 0.01, "at its own size")
    redo.click()
    wait_for_marks(page, 2)
    approx(marks(page)[1]["x"], cross["x"], 0.01, "redo replays the cross")
    redo.click()
    wait_for_marks(page, 3)
    equals(marks(page)[2]["text"], dated["text"], "and redo replays the date, value and all")
    equals(marks(page)[2]["kind"], "date", "still as a date")
    clean(page, mock)


@test("the_keyboard_shortcuts_drive_the_history")
def _(page, mock: MockApi) -> None:
    """Ctrl+Z, Ctrl+Shift+Z and Ctrl+Y, and a new step clears what was ahead."""
    open_editor(page, mock)
    place_text(page, "first", 200, 300)
    place_text(page, "second", 300, 400, expect=2)
    equals(depth(page, "undo"), 2, "two placements")

    page.keyboard.press("Control+z")
    wait_for_marks(page, 1)
    page.keyboard.press("Control+Shift+z")
    wait_for_marks(page, 2)
    page.keyboard.press("Control+z")
    wait_for_marks(page, 1)
    page.keyboard.press("Control+y")
    wait_for_marks(page, 2)
    equals(depth(page, "redo"), 0, "redo emptied the future")

    page.keyboard.press("Control+z")
    wait_for_marks(page, 1)
    equals(depth(page, "redo"), 1, "one step is ahead")
    place_text(page, "third", 400, 500, expect=2)
    equals(depth(page, "redo"), 0, "a new step drops the branch that was ahead")
    clean(page, mock)


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

@test("annotations_survive_a_reload")
def _(page, mock: MockApi) -> None:
    """`swm.annotations.<id>`: the shape that is stored is the shape that is sent."""
    open_editor(page, mock)
    mark = place_text(page, "Kept", 240, 330)
    saved = stored(page)
    truthy(saved is not None and len(saved) == 1, f"one entry is stored ({saved})")
    entry = saved[0]
    equals(sorted(entry.keys()),
           ["font", "id", "kind", "page", "size_pt", "text", "x_pt", "y_pt"],
           "the stored keys are the contract's")
    equals(entry["kind"], "text", "the kind")
    equals(entry["text"], "Kept", "the text")
    equals(entry["page"], 1, "the page")
    approx(entry["x_pt"], mark["x"], 0.01, "the stored x")

    page.reload()
    page.wait_for_selector('[data-testid="editor-toolbar"]', timeout=30000)
    wait_for_marks(page, 1)
    page.wait_for_timeout(200)
    back = only_mark(page)
    equals(back["text"], "Kept", "the run is drawn again after a reload")
    approx(back["x"], mark["x"], 0.01, "at the same x")
    approx(back["y"], mark["y"], 0.01, "at the same y")
    # The history is per visit; the annotations are not.
    equals(depth(page, "undo"), 0, "a reload starts a fresh history")
    clean(page, mock)


@test("a_mixed_list_survives_a_reload_and_travels_whole")
def _(page, mock: MockApi) -> None:
    """One of each kind: stored, reloaded, and sent in the finalize body."""
    open_editor(page, mock)
    page.locator('[data-testid="ann-date"]').fill("2026-12-01")
    page.wait_for_timeout(120)
    run = place_text(page, "Rider attached", 200, 280)
    tick = place_mark(page, "check", 300, 340, expect=2)
    cross = place_mark(page, "cross", 360, 400, expect=3)
    dated = place_date(page, 420, 460, expect=4)

    saved = stored(page) or []
    equals(len(saved), 4, "four entries are stored")
    equals([e["kind"] for e in saved], ["text", "check", "cross", "date"], "in the order they were placed")
    for entry in saved:
        if entry["kind"] in ("check", "cross"):
            equals(sorted(entry.keys()), ["id", "kind", "page", "size_pt", "x_pt", "y_pt"],
                   f"a stored {entry['kind']} carries no text and no font")
        else:
            equals(sorted(entry.keys()),
                   ["font", "id", "kind", "page", "size_pt", "text", "x_pt", "y_pt"],
                   f"a stored {entry['kind']} carries both")

    page.reload()
    page.wait_for_selector('[data-testid="editor-toolbar"]', timeout=30000)
    wait_for_marks(page, 4)
    page.wait_for_timeout(250)
    back = marks(page)
    equals([m["kind"] for m in back], ["text", "check", "cross", "date"], "all four are drawn again")
    approx(back[0]["x"], run["x"], 0.01, "the run is where it was")
    approx(back[1]["x"], tick["x"], 0.01, "and the tick")
    approx(back[1]["size"], tick["size"], 0.01, "at its own size")
    approx(back[2]["y"], cross["y"], 0.01, "and the cross")
    equals(back[3]["text"], dated["text"], "and the date still reads the same")

    # ...and the same four reach the server, each in its own shape. W1: there
    # is one finalize, gated the signing way -- every required date has to be
    # chosen first (the fixture's two signature slots are already signed).
    apply_dates_to_all(page)
    page.locator('[data-testid="editor-finalize"]').click()
    page.wait_for_function("() => window.location.pathname.endsWith('/final')", timeout=20000)
    sent = (mock.bodies.get("finalize") or {}).get("annotations")
    truthy(isinstance(sent, list) and len(sent) == 4, f"four annotations travelled ({sent})")
    equals([e["kind"] for e in sent], ["text", "check", "cross", "date"], "with their kinds")
    by_kind = {e["kind"]: e for e in sent}
    equals(sorted(by_kind["check"].keys()), ["id", "kind", "page", "size_pt", "x_pt", "y_pt"],
           "a mark's request entry has no text key at all")
    equals(sorted(by_kind["date"].keys()),
           ["font", "id", "kind", "page", "size_pt", "text", "x_pt", "y_pt"],
           "a date's has both")
    approx(by_kind["check"]["x_pt"], tick["x"], 0.01, "the tick's centre, in PDF points")
    approx(by_kind["check"]["y_pt"], tick["y"], 0.01, "on both axes")
    equals(by_kind["date"]["text"], "12/01/2026", "and the date's printed value")
    clean(page, mock)
    shot2(page, "mixed_list")


@test("a_corrupt_entry_is_dropped_in_silence")
def _(page, mock: MockApi) -> None:
    """Rubbish beside a good entry costs the rubbish, not the entry -- and never an error."""
    seed = json.dumps([
        # -- kept --------------------------------------------------------
        {"id": "good1", "kind": "text", "page": 1, "x_pt": 100, "y_pt": 200,
         "text": "Survivor", "font": "Courier", "size_pt": 13},
        {"id": "tick1", "kind": "check", "page": 1, "x_pt": 150, "y_pt": 250, "size_pt": 30},
        # A mark that was hand-edited to carry a run's fields, and an
        # impossible size: read as a mark, at the mark ceiling, and the two
        # fields a mark has no business carrying are simply not read.
        {"id": "tick2", "kind": "cross", "page": 1, "x_pt": 200, "y_pt": 300,
         "size_pt": 900, "text": "ignore me", "font": "Comic Sans"},
        # -- dropped -----------------------------------------------------
        {"id": "bad id!", "kind": "text", "page": 1, "x_pt": 1, "y_pt": 1, "text": "x"},
        {"id": "good1", "kind": "text", "page": 1, "x_pt": 2, "y_pt": 2, "text": "duplicate id"},
        {"id": "empty", "kind": "text", "page": 1, "x_pt": 3, "y_pt": 3, "text": "   "},
        {"id": "nopage", "kind": "text", "page": 0, "x_pt": 4, "y_pt": 4, "text": "y"},
        {"id": "nan", "kind": "text", "page": 1, "x_pt": "over there", "y_pt": 5, "text": "z"},
        # A mark is not exempt from any of the coordinate rules.
        {"id": "nocoord", "kind": "check", "page": 1, "x_pt": None, "y_pt": 6, "size_pt": 12},
        {"id": "markpage", "kind": "cross", "page": -2, "x_pt": 7, "y_pt": 7, "size_pt": 12},
        # A kind from some later build this one cannot draw.
        {"id": "sticker", "kind": "sticker", "page": 1, "x_pt": 8, "y_pt": 8, "size_pt": 12},
        "not even an object",
        None,
    ])
    open_editor(page, mock, seed=seed)
    wait_for_marks(page, 3)
    page.wait_for_timeout(250)

    kept = marks(page)
    equals([m["kind"] for m in kept], ["text", "check", "cross"], "the three good entries are drawn")
    equals(kept[0]["text"], "Survivor", "the run kept its text")
    equals(kept[0]["font"], "Courier", "with its own font")
    approx(kept[0]["size"], 13, 0.01, "and its own size")
    approx(kept[1]["size"], 30, 0.01, "the tick kept its own size")
    approx(kept[2]["size"], MARK_SIZE_MAX, 0.01, "an impossible size is pulled to the mark ceiling")
    equals(kept[2]["text"], None, "and the text a cross should never have carried is gone")
    equals(kept[2]["font"], None, "as is the font")

    written = stored(page) or []
    equals(len(written), 3, "only the three are written back")
    equals(sorted(written[2].keys()), ["id", "kind", "page", "size_pt", "x_pt", "y_pt"],
           "and the cross is rewritten in the shape a cross has")
    clean(page, mock)

    # A value that is not even JSON is the same non-event.
    open_editor(page, mock, seed="{ this is not json")
    page.wait_for_timeout(300)
    equals(marks(page), [], "a broken value simply yields no annotations")
    clean(page, mock)


# --------------------------------------------------------------------------
# Limits and the keyboard
# --------------------------------------------------------------------------

@test("a_run_can_never_be_dragged_off_its_page")
def _(page, mock: MockApi) -> None:
    """The server answers 422 outside the page, so the browser never gets there."""
    open_editor(page, mock)
    mark = place_text(page, "Edge", 300, 400)
    select_tool(page)

    drag_mark(page, mark["id"], -450, -450)
    pinned = only_mark(page)
    approx(pinned["x"], 0, 0.01, "x stops at the left edge")
    approx(pinned["y"], 0, 0.01, "y stops at the top edge")

    # And the far side. A whole page has to be inside the stage for that, so
    # zoom out first -- then a drag can actually reach the right/bottom edges.
    page.locator('[data-testid="ann-delete"]').click()
    wait_for_marks(page, 0)
    zoom_out(page, 6)
    far = place_text(page, "Far", 200, 200)
    select_tool(page)
    drag_mark(page, far["id"], 700, 600)
    pushed = only_mark(page)
    approx(pushed["x"], PAGE_W, 0.01, "x stops at the right edge")
    approx(pushed["y"], PAGE_H, 0.01, "y stops at the bottom edge")
    saved = stored(page)
    truthy(0 <= saved[0]["x_pt"] <= PAGE_W, "what is stored is on the page too")
    truthy(0 <= saved[0]["y_pt"] <= PAGE_H, "on both axes")
    clean(page, mock)


@test("the_keyboard_alone_can_move_edit_and_drop_a_run")
def _(page, mock: MockApi) -> None:
    """Arrows nudge on the same grid the mouse snaps to; Shift takes the long step."""
    open_editor(page, mock)
    mark = place_text(page, "Keys", 240, 320)
    select_tool(page)
    click_mark(page, mark["id"])

    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(120)
    approx(only_mark(page)["x"], mark["x"] + NUDGE_STEP, 0.01, "one step right")
    page.keyboard.press("Shift+ArrowDown")
    page.wait_for_timeout(120)
    approx(only_mark(page)["y"], mark["y"] + NUDGE_STEP_LARGE, 0.01, "one long step down")
    page.keyboard.press("Control+z")
    page.wait_for_timeout(120)
    approx(only_mark(page)["y"], mark["y"], 0.01, "and each nudge is its own step")

    # Enter opens the box on the selected run; Escape lets it go again.
    page.keyboard.press("Enter")
    page.wait_for_selector('[data-testid="ann-input"]', timeout=10000)
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    equals(page.locator('[data-testid="ann-input"]').count(), 0, "Escape closes the box")
    equals(only_mark(page)["text"], "Keys", "and changes nothing")

    page.keyboard.press("Escape")
    page.wait_for_timeout(120)
    truthy(not only_mark(page)["selected"], "Escape then drops the selection")
    clean(page, mock)


@test("the_font_and_size_boxes_follow_the_selection")
def _(page, mock: MockApi) -> None:
    """With a run selected they change that run; with none they arm the next one."""
    open_editor(page, mock)
    mark = place_text(page, "Styled", 220, 300)
    select_tool(page)
    click_mark(page, mark["id"])

    page.locator('[data-testid="ann-font"]').select_option("Times-Roman")
    page.wait_for_timeout(150)
    styled = only_mark(page)
    equals(styled["font"], "Times-Roman", "the selected run takes the new font")
    contains(styled["fontFamily"], "Times", "and is drawn in it")

    page.locator('[data-testid="ann-size"]').fill("24")
    page.wait_for_timeout(150)
    approx(only_mark(page)["size"], 24, 0.01, "and the new size")
    approx(only_mark(page)["svgSize"], 24 * scale_of(page), 0.6, "drawn at the new size")

    page.keyboard.press("Control+z")
    page.wait_for_timeout(150)
    approx(only_mark(page)["size"], DEFAULT_SIZE, 0.01, "a size change is one undo step")

    # Nothing selected: the boxes describe the NEXT run instead.
    box = page_rect(page)
    select_tool(page)
    page.mouse.click(box["x"] + 500, box["y"] + 700)
    page.wait_for_timeout(150)
    page.locator('[data-testid="ann-size"]').fill("30")
    page.wait_for_timeout(120)
    placed = place_text(page, "Next", 300, 500, expect=2)
    approx(placed["size"], 30, 0.01, "the next run is placed at the armed size")
    approx(marks(page)[0]["size"], DEFAULT_SIZE, 0.01, "and the first run was left alone")
    clean(page, mock)


@test("a_marks_armed_size_is_kept_apart_from_a_runs")
def _(page, mock: MockApi) -> None:
    """11 pt is a sensible run and a laughable tick, so the two are armed separately."""
    open_editor(page, mock)
    size_box = page.locator('[data-testid="ann-size"]')

    # Arm the text tool at 30 pt...
    arm_tool(page, "text")
    page.wait_for_timeout(120)
    equals(size_box.get_attribute("data-kind"), "text", "the box describes the armed text tool")
    size_box.fill("30")
    page.wait_for_timeout(120)

    # ...then the tick tool: it still offers a tick's own default, not 30.
    arm_tool(page, "check")
    page.wait_for_timeout(150)
    equals(size_box.get_attribute("data-kind"), "check", "the box follows the armed tool")
    approx(float(size_box.input_value()), DEFAULT_MARK_SIZE, 0.01, "a tick's own default is offered")
    size_box.fill("40")
    page.wait_for_timeout(120)

    tick = place_mark(page, "check", 220, 300)
    approx(tick["size"], 40, 0.01, "the tick is placed at the size armed for ticks")

    run = place_text(page, "Beside it", 320, 380, expect=2)
    approx(run["size"], 30, 0.01, "and the run at the size armed for runs")
    clean(page, mock)


# --------------------------------------------------------------------------
# Finalize
# --------------------------------------------------------------------------

@test("finalize_sends_the_annotations_and_the_saved_layout")
def _(page, mock: MockApi) -> None:
    """Contract v4: the editor's own body, with the signing page's layout carried through."""
    goto_blank(page, mock)
    # A layout the signing page could have saved. The editor never edits it --
    # it reads it and sends it on, so a contract finalized from here prints its
    # signatures exactly where the signer put them.
    page.evaluate(
        "([key, value]) => localStorage.setItem(key, value)",
        [f"swm.layout.{mock.contract_id}", json.dumps({
            "signature_offset_pt": 2.5,
            "date_offset_pt": 0,
            "slot_offsets_pt": {"sig_1": 1.5},
            "slot_offsets_x_pt": {},
            "signature_scale": 1.1,
            "date_font": "Courier",
            "date_font_size": 12,
        })],
    )
    open_editor(page, mock)
    mark = place_text(page, "Rider attached", 260, 350)

    # W1: one finalize, gated the signing way (every required date first).
    apply_dates_to_all(page)
    page.locator('[data-testid="editor-finalize"]').click()
    page.wait_for_function(
        "() => window.location.pathname.endsWith('/final')",
        timeout=20000,
    )
    body = mock.bodies.get("finalize")
    truthy(body is not None, "finalize was called")

    sent = body.get("annotations")
    truthy(isinstance(sent, list) and len(sent) == 1, f"one annotation travelled ({sent})")
    entry = sent[0]
    equals(entry["kind"], "text", "the kind the server expects")
    equals(entry["text"], "Rider attached", "the text")
    equals(entry["page"], 1, "the page")
    equals(entry["font"], "Helvetica", "the font")
    approx(entry["size_pt"], DEFAULT_SIZE, 0.01, "the size")
    approx(entry["x_pt"], mark["x"], 0.01, "the x, in PDF points")
    approx(entry["y_pt"], mark["y"], 0.01, "the y, in PDF points")
    truthy(len(entry["id"]) <= 64, "an id the server will accept")

    layout = body.get("layout")
    truthy(layout is not None, "the saved layout travelled too")
    approx(layout["signature_offset_pt"], 2.5, 0.01, "unchanged by the editor")
    approx(layout["slot_offsets_pt"]["sig_1"], 1.5, 0.01, "including the per-slot nudge")
    equals(layout["date_font"], "Courier", "and the date font")
    # ...and, since the one finalize is the signing one, the dates as well.
    sent_dates = sorted(entry["field_id"] for entry in body.get("dates") or [])
    equals(sent_dates, ["date_1", "date_2"], "the chosen dates travelled with it")
    clean(page, mock)


@test("finalize_without_annotations_leaves_the_key_out")
def _(page, mock: MockApi) -> None:
    """A server that predates contract v4 never has to see the field."""
    open_editor(page, mock)
    # W1: one finalize, gated the signing way (every required date first).
    apply_dates_to_all(page)
    page.locator('[data-testid="editor-finalize"]').click()
    page.wait_for_function("() => window.location.pathname.endsWith('/final')", timeout=20000)
    body = mock.bodies.get("finalize")
    truthy(body is not None, "finalize was called")
    truthy("annotations" not in body, f"no empty annotations key ({list(body)})")
    clean(page, mock)


@test("an_older_server_is_explained_rather_than_blamed")
def _(page, mock: MockApi) -> None:
    """A 422 about `annotations` means the backend is old; the work is still safe."""
    mock.finalize_response = {
        "status": 422,
        "body": {
            "error": "validation_error",
            "message": "body › annotations: extra fields not permitted",
            "details": {},
        },
    }
    open_editor(page, mock)
    place_text(page, "Rider", 240, 330)
    # W1: one finalize, gated the signing way (every required date first).
    apply_dates_to_all(page)
    page.locator('[data-testid="editor-finalize"]').click()
    alert = page.wait_for_selector('[data-testid="editor-error"]', timeout=20000)
    contains(alert.inner_text(), "does not accept annotations yet", "the reason is named")
    contains(alert.inner_text(), "saved in this browser", "and the work is accounted for")
    equals(len(marks(page)), 1, "the annotation is still on the page")
    truthy(stored(page) is not None, "and still stored")
    truthy(
        page.evaluate("() => window.location.pathname").endswith("/edit"),
        "the editor stays open",
    )
    clean(page, mock)
    shot(page, "editor_finalize_refused")


# --------------------------------------------------------------------------
# Getting there
# --------------------------------------------------------------------------

@test("the_editor_is_one_workspace_behind_three_addresses")
def _(page, mock: MockApi) -> None:
    """Dashboard row still links to /edit; /review, /sign, /edit are ONE workspace.

    Moved in W1 (was `the_editor_is_one_link_away_from_the_three_screens`):
    the review and signing panels no longer carry an "Open editor" link,
    because the editor's toolbar is already on both -- they are the same
    screen. What the old check protected (the dashboard entry, the review's
    issue list, the signing layout panel, and actually reaching the editor)
    is all still asserted here.
    """
    target = f"/contracts/{mock.contract_id}/edit"

    # -- the dashboard row: unchanged ---------------------------------------
    serve_one_contract(page, mock)
    page.goto(f"{BASE}/")
    page.wait_for_selector('[data-testid="dash-row"]', timeout=30000)
    row_link = page.locator('[data-testid="dash-open-editor"]')
    equals(row_link.count(), 1, "the row offers the editor")
    contains(row_link.get_attribute("href") or "", target, "and points at this contract's editor")
    contains(row_link.get_attribute("aria-label") or "", "Open the editor for",
             "with a name that says whose contract it is")
    equals(row_link.inner_text().strip(), "Open editor", "in the words a reader sees")
    # The actions that were already on the row are untouched.
    equals(page.locator('a[aria-label^="Review the contract for"]').count(), 1, "Review is still there")
    equals(page.locator('a[aria-label^="Sign the contract for"]').count(), 1, "Sign is still there")
    clean(page, mock)
    shot2(page, "entry_dashboard")

    # ...and it really goes there.
    row_link.click()
    page.wait_for_selector('[data-testid="workspace"][data-panel="edit"]', timeout=30000)
    truthy(page.evaluate("() => window.location.pathname").endswith("/edit"),
           "the link really opens the editor")
    equals(page.locator('[data-testid="editor-toolbar"]').count(), 1, "with its toolbar")
    shot2(page, "entry_editor_opened")

    # -- the review address: the same workspace, the editor already there ----
    open_review(page, mock)
    equals(page.locator('[data-testid="workspace"][data-panel="review"]').count(), 1,
           "/review is the workspace")
    equals(page.locator('[data-testid="editor-toolbar"]').count(), 1,
           "the editor's toolbar is on the review panel itself")
    equals(page.locator('[data-testid="open-editor-link"]').count(), 0,
           "so there is no link away to a second screen")
    equals(page.locator(".sidebar__summary").count(), 1, "and the issue list is still where it was")
    clean(page, mock)
    shot2(page, "entry_review")

    # -- the signing address -------------------------------------------------
    open_sign(page, mock)
    equals(page.locator('[data-testid="workspace"][data-panel="sign"]').count(), 1,
           "/sign is the workspace")
    equals(page.locator('[data-testid="editor-toolbar"]').count(), 1, "with the same toolbar")
    equals(page.locator('[data-testid="open-editor-link"]').count(), 0, "and no link away")
    equals(page.locator('[data-testid="layout-panel"]').count(), 1,
           "and the layout panel is still where it was")
    clean(page, mock)
    shot2(page, "entry_sign")

if __name__ == "__main__":
    raise SystemExit(cli_main(test))
