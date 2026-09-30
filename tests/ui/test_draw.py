"""
W4: the drawing tools -- Draw (P, freehand ink), Rectangle (R), Ellipse (O),
Line (L), Arrow (A), Note (N), Image (M) -- their handles, the stroke / fill
properties, the image upload, and the toolbar's drop-downs.

What a user does (Sejda-style): arm a shape and DRAG on the page (Shift =
square / circle / 45°); draw freehand with the pen; click to drop a sticky
note and type several lines; click to place a picture. A drawing is selected,
moved, resized by its handles, recoloured, re-stroked, filled, deleted, undone.

These checks pin, in PDF points (±0.5 pt):

  * a drag is the box / the two ends / the stroke it looks like, one undo
    step, and the live preview is only on the page it happens on;
  * Shift: a rect / ellipse is square, a line / arrow snaps to 45°;
  * ink is thinned (RDP 0.5 pt), a click is a dot, a stroke too long for one
    element is split to the server's limits (≤ 2000 points a path, ≤ 4000 an
    element, ≤ 20 paths) in ONE step, and the 20 000-point request ceiling is
    refused out loud;
  * the preview is the print: stroke centred, the colour's hex, fill alpha
    0.2, butt line caps, the arrow head 6 × 3 × stroke;
  * handles: 8 on a rect / ellipse / image (an image keeps its ratio, Shift
    frees it; a rect's Shift corner is a square), 2 ends on a line / arrow;
    ink and notes move whole; everything stays on its page;
  * a note is multi-line, checked for the PDF's character set before it is
    kept, shows its text when selected;
  * an image is checked here (PNG / JPEG, ≤ 2 MB, ≤ 4000 px), uploaded ONCE as
    a JSON `image_data` data URL, previewed through `GET .../annotation-assets/
    {id}`, placed at its natural ratio; 409 / 413 / 422 are said in words; the
    tool is off on a contract nobody approved;
  * Esc drops a drag; a turned page draws nothing;
  * the finalize body of every new kind has exactly the backend's keys (the
    body is also written to `SWM_DRAW_BODY_OUT` when that is set, for the
    backend schema proof);
  * the toolbar: every tool in one row at 1600 px (nothing under «More ▾»;
    F1a: eight buttons, the spec's menus), «Shapes ▾» works by mouse and
    keyboard, a letter presses its menu («Annotate ▾» for H).

Everything is synthetic; every `/api/*` call is answered by `MockApi`.
Screenshots: `c:\\PTG\\.playwright-mcp\\swm_draw\\`.
"""

from __future__ import annotations

import json
import math
import os
import sys

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
    open_section,
    rule_line,
    solid_png,
    synthetic_multipage_pdf,
    truthy,
)

test = Registry("draw")

PAGE_W = 612.0
PAGE_H = 792.0
NOT_APPROVED = "Contract not approved yet"
PALETTE = {"black": "#000000", "blue": "#1D4ED8", "red": "#DC2626", "green": "#16A34A",
           "yellow": "#FACC15", "orange": "#EA580C", "purple": "#7E22CE"}
ASSET_1 = f"{1:032x}"

#: The backend's request shapes (`app/schemas/contract.py`, contract v5).
KEYS = {
    "ink": ["color", "id", "kind", "page", "paths", "stroke_pt"],
    "rect": ["color", "fill", "h_pt", "id", "kind", "page", "stroke_pt", "w_pt", "x_pt", "y_pt"],
    "ellipse": ["color", "fill", "h_pt", "id", "kind", "page", "stroke_pt", "w_pt", "x_pt", "y_pt"],
    "line": ["color", "id", "kind", "page", "stroke_pt", "x1_pt", "x2_pt", "y1_pt", "y2_pt"],
    "arrow": ["color", "id", "kind", "page", "stroke_pt", "x1_pt", "x2_pt", "y1_pt", "y2_pt"],
    "note": ["color", "id", "kind", "page", "text", "x_pt", "y_pt"],
    "image": ["asset_id", "h_pt", "id", "kind", "page", "w_pt", "x_pt", "y_pt"],
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def shot(page, name: str) -> None:
    folder = os.path.join("c:\\", "PTG", ".playwright-mcp", "swm_draw")
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, f"{name}.png"))


def clean(mock: MockApi) -> None:
    equals(mock.page_errors, [], "no uncaught error")
    equals(mock.unmocked, [], "no unmocked API call")
    warnings = [text for text in mock.console_errors if "Warning:" in text and "React Router" not in text]
    equals(warnings, [], "no React warning")


def storage_key(mock: MockApi) -> str:
    return f"swm.annotations.{mock.contract_id}"


def open_doc(page, mock: MockApi, seed: list | None = None, panel: str = "edit") -> None:
    if seed is not None:
        goto_blank(page, mock)
        page.evaluate("([k, v]) => localStorage.setItem(k, v)", [storage_key(mock), json.dumps(seed)])
    page.goto(f"{BASE}/contracts/{mock.contract_id}/{panel}")
    page.wait_for_selector('[data-testid="editor-toolbar"]', timeout=30000)
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_function(
        "() => { const p = document.querySelector('.pdf-page');"
        " return !!p && p.getBoundingClientRect().width > 300; }",
        timeout=30000,
    )
    page.wait_for_timeout(400)


def origin(page, number: int = 1) -> dict:
    box = page.locator(f'.pdf-page[data-page-number="{number}"]').bounding_box()
    truthy(box is not None, f"page {number} has a box")
    return box


def scale_of(page) -> float:
    return origin(page)["width"] / PAGE_W


def px(page, x_pt: float, y_pt: float, number: int = 1) -> tuple[float, float]:
    box = origin(page, number)
    scale = box["width"] / PAGE_W
    return box["x"] + x_pt * scale, box["y"] + y_pt * scale


def drag_pt(page, x0: float, y0: float, x1: float, y1: float, *, shift: bool = False, steps: int = 10) -> None:
    """Press at (x0, y0) pt, move to (x1, y1) pt, release (Shift held while moving)."""
    ax, ay = px(page, x0, y0)
    bx, by = px(page, x1, y1)
    page.mouse.move(ax, ay)
    page.mouse.down()
    if shift:
        page.keyboard.down("Shift")
    page.mouse.move(bx, by, steps=steps)
    page.mouse.up()
    if shift:
        page.keyboard.up("Shift")
    page.wait_for_timeout(150)


def stored(page, mock: MockApi) -> list:
    raw = page.evaluate("(k) => localStorage.getItem(k)", storage_key(mock))
    return json.loads(raw) if raw else []


def wait_stored(page, mock: MockApi, count: int) -> None:
    page.wait_for_function(
        "([k, n]) => { const raw = localStorage.getItem(k); return (raw ? JSON.parse(raw).length : 0) === n; }",
        arg=[storage_key(mock), count],
        timeout=10000,
    )


def depth(page, which: str = "undo") -> int:
    return int(page.locator(f'[data-testid="{which}-depth"]').inner_text().strip())


def note_text(page) -> str:
    locator = page.locator('[data-testid="editor-note"]')
    return locator.inner_text() if locator.count() else ""


def finalize_body(page, mock: MockApi) -> dict:
    arm_tool(page, "select")
    apply_dates_to_all(page)
    page.locator('[data-testid="editor-finalize"]').click()
    page.wait_for_function("() => window.location.pathname.endsWith('/final')", timeout=20000)
    body = mock.bodies.get("finalize")
    truthy(body is not None, "finalize was sent")
    return body


def rgb(hex_value: str) -> str:
    value = hex_value.lstrip("#")
    r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgb({r}, {g}, {b})"


def svg_attr(page, testid: str, name: str) -> str | None:
    return page.locator(f'[data-testid="{testid}"]').get_attribute(name)


def handle_drag(page, testid: str, dx_pt: float, dy_pt: float, *, shift: bool = False) -> None:
    handle = page.locator(f'[data-testid="{testid}"]')
    box = handle.bounding_box()
    truthy(box is not None, f"{testid} is on screen")
    scale = scale_of(page)
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    if shift:
        page.keyboard.down("Shift")
    page.mouse.move(cx + dx_pt * scale, cy + dy_pt * scale, steps=8)
    page.mouse.up()
    if shift:
        page.keyboard.up("Shift")
    page.wait_for_timeout(150)


def drag_ann(page, ann_id: str, dx_pt: float, dy_pt: float) -> None:
    """Press an annotation's hit box and drag it (4 px first: past the 3 px threshold, still on it)."""
    target = page.locator(f'[data-testid="ann-{ann_id}"]').bounding_box()
    truthy(target is not None, f"ann-{ann_id} is on screen")
    scale = scale_of(page)
    cx, cy = target["x"] + target["width"] / 2, target["y"] + target["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + 4, cy + 4)
    page.mouse.move(cx + dx_pt * scale, cy + dy_pt * scale, steps=10)
    page.mouse.up()
    page.wait_for_timeout(150)


def select_ann(page, ann_id: str) -> None:
    arm_tool(page, "select")
    page.locator(f'[data-testid="ann-{ann_id}"]').click()
    page.wait_for_timeout(120)


#: One persistent `filechooser` listener per page. Playwright switches the file
#: chooser interception on asynchronously when the FIRST listener is attached;
#: `expect_file_chooser` attached right before the click could lose that race
#: (the dialog then opened natively and was dismissed -- measured: ~1 run in 4).
_CHOOSERS: dict[int, list] = {}


def upload_via_click(page, x_pt: float, y_pt: float, name: str, mime: str, data: bytes) -> None:
    queue = _CHOOSERS.get(id(page))
    if queue is None:
        queue = []
        _CHOOSERS[id(page)] = queue
        page.on("filechooser", lambda chooser: queue.append(chooser))
        page.wait_for_timeout(300)
    queue.clear()
    ax, ay = px(page, x_pt, y_pt)
    page.mouse.click(ax, ay)
    for _ in range(100):
        if queue:
            break
        page.wait_for_timeout(100)
    truthy(queue, "the click on the page opened the file dialog")
    queue.pop().set_files(files=[{"name": name, "mimeType": mime, "buffer": data}])
    page.wait_for_timeout(400)


def seed_rect(**extra) -> dict:
    item = {"id": "r1", "kind": "rect", "page": 1, "x_pt": 100, "y_pt": 250, "w_pt": 100, "h_pt": 50,
            "stroke_pt": 2, "color": "black", "fill": False}
    item.update(extra)
    return item


def seed_image(**extra) -> dict:
    item = {"id": "i1", "kind": "image", "page": 1, "asset_id": ASSET_1,
            "x_pt": 100, "y_pt": 250, "w_pt": 200, "h_pt": 100}
    item.update(extra)
    return item


# --------------------------------------------------------------------------
# The toolbar
# --------------------------------------------------------------------------

@test("seventeen_tools_fit_one_row_at_1600_and_the_drop_downs_work")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    toolbar = page.locator('[data-testid="editor-toolbar"]')
    equals(toolbar.get_attribute("data-overflow"), "0", "nothing under «More ▾» with Select armed")
    for name, key in (("ink", "P"), ("rect", "R"), ("ellipse", "O"), ("line", "L"), ("arrow", "A"),
                      ("note", "N"), ("image", "M")):
        tool = page.locator(f'[data-testid="tool-{name}"]')
        equals(tool.count(), 1, f"tool-{name} exists")
        equals(tool.get_attribute("aria-keyshortcuts"), key, f"{name} announces {key}")
    truthy(not page.locator('[data-testid="tool-rect"]').is_visible(), "the shapes sit in a closed drop-down")
    row = page.evaluate(
        "() => [...document.querySelectorAll('.ws-toolbar__tools .editor__tool')].map((b) => b.dataset.testid)"
    )
    # F1a (EDITOR-V2-PLAN §7): Select | Text ▼ | Forms ▼ | Images ▼ | Sign ▼ |
    # Whiteout | Annotate ▼ | Shapes ▼ -- 19 tools, 8 buttons. Draw (P) and
    # Note (N) moved into «Annotate ▼», Image (M) into «Images ▼».
    equals(row[6:8], ["toolgroup-annotate", "toolgroup-shapes"], "Annotate ▼ and Shapes ▼ close the row")
    equals(len(row), 8, "eight buttons in the row (twelve before F1a)")
    group = page.locator('[data-testid="toolgroup-shapes"]')
    equals(group.get_attribute("data-tool"), "rect", "the drop-down starts on its first member (rectangle)")
    equals(page.locator('[data-testid="toolgroup-annotate"]').get_attribute("data-tool"), "highlight",
           "Annotate: Highlight")
    equals(page.locator('[data-testid="toolgroup-images"]').get_attribute("data-tool"), "image", "Images: Image")
    for name, menu in (("ink", "annotate"), ("note", "annotate"), ("image", "images")):
        equals(page.locator(f'[data-testid="tool-{name}"]').get_attribute("data-menu"), menu,
               f"{name} lives in {menu} ▼")
    # Mouse: open, pick Ellipse -> the button now shows (and arms) Ellipse.
    page.locator('[data-testid="toolgroup-shapes-toggle"]').click()
    menu = page.locator('[data-testid="toolgroup-shapes-menu"]')
    truthy(menu.is_visible(), "▾ opens the list")
    shot(page, "shapes_menu")
    page.locator('[data-testid="tool-ellipse"]').click()
    truthy(not menu.is_visible(), "a pick closes it")
    equals(page.locator('[data-testid="tool-ellipse"]').get_attribute("aria-pressed"), "true", "Ellipse armed")
    equals(group.get_attribute("data-tool"), "ellipse", "the button shows the last pick")
    equals(group.get_attribute("aria-pressed"), "true", "and is pressed")
    # The row still fits with the shape's stroke + fill groups on it.
    page.wait_for_timeout(300)
    equals(toolbar.get_attribute("data-overflow"), "0", "a shape armed: still nothing under «More ▾»")
    truthy(toolbar.bounding_box()["height"] < 64, "one row")
    truthy(page.locator('[data-testid="ann-stroke"]').is_visible(), "the stroke box is there")
    truthy(page.locator('[data-testid="ann-fill"]').is_visible(), "the fill toggle is there")
    # Keyboard: the ▾ opens with Enter onto the armed item, ↓ moves, Enter arms, Esc closes.
    page.keyboard.press("v")
    page.locator('[data-testid="toolgroup-shapes-toggle"]').focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(100)
    truthy(menu.is_visible(), "Enter opens the list")
    active = page.evaluate("() => document.activeElement.dataset.testid")
    equals(active, "tool-ellipse", "focus on the member the button shows")
    page.keyboard.press("ArrowDown")
    equals(page.evaluate("() => document.activeElement.dataset.testid"), "tool-line", "↓ moves down")
    page.keyboard.press("Enter")
    page.wait_for_timeout(100)
    equals(page.locator('[data-testid="tool-line"]').get_attribute("aria-pressed"), "true", "Enter arms Line")
    truthy(not menu.is_visible(), "and closes the list")
    # One roving stop per drop-down: Alt+↓ on the button opens the list.
    group.focus()
    page.keyboard.press("Alt+ArrowDown")
    page.wait_for_timeout(100)
    truthy(menu.is_visible(), "Alt+↓ on the button opens the list")
    equals(page.evaluate("() => document.activeElement.dataset.testid"), "tool-line", "on the armed member")
    page.keyboard.press("Escape")
    page.wait_for_timeout(100)
    truthy(not menu.is_visible(), "Esc closes it")
    equals(page.evaluate("() => document.activeElement.dataset.testid"), "toolgroup-shapes",
           "focus back on the button")
    equals(page.locator('[data-testid="toolgroup-shapes-toggle"]').get_attribute("tabindex"), "-1",
           "the ▾ corner is not a stop of its own")
    # The main half arms the member it shows with one click.
    page.keyboard.press("v")
    group.click()
    equals(page.locator('[data-testid="tool-line"]').get_attribute("aria-pressed"), "true",
           "one click on the button re-arms Line")
    # A shortcut arms a member directly and the button follows.
    page.keyboard.press("h")
    equals(page.locator('[data-testid="toolgroup-annotate"]').get_attribute("aria-pressed"), "true",
           "H: the Annotate button is pressed")
    clean(mock)


@test("the_properties_follow_what_is_drawn")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    equals(page.locator('[data-testid="ann-stroke"]').count(), 0, "Select: no stroke box")
    equals(page.locator('[data-testid="ann-font"]').count(), 1, "the font box as before")
    for tool, stroke, fill, color in (("ink", 1, 0, "black"), ("rect", 1, 1, "black"), ("ellipse", 1, 1, "black"),
                                     ("line", 1, 0, "black"), ("arrow", 1, 0, "black"), ("note", 0, 0, "yellow")):
        arm_tool(page, tool)
        page.wait_for_timeout(100)
        equals(page.locator('[data-testid="ann-stroke"]').count(), stroke, f"{tool}: stroke box")
        equals(page.locator('[data-testid="ann-fill"]').count(), fill, f"{tool}: fill toggle")
        equals(page.locator('[data-testid="ann-font"]').count(), 0, f"{tool}: no font")
        equals(page.locator('[data-testid="ann-color"]').get_attribute("data-color"), color,
               f"{tool}: starts {color}")
    arm_tool(page, "image")
    truthy(page.locator('[data-testid="ann-color"]').is_disabled(), "an image has no colour")
    contains(page.locator('[data-testid="ann-color"]').get_attribute("title") or "", "image has no colour",
             "and says so")
    stroke_box = page.locator('[data-testid="ann-stroke"]')
    arm_tool(page, "rect")
    equals(stroke_box.get_attribute("min"), "0.5", "stroke ≥ 0.5 pt")
    equals(stroke_box.get_attribute("max"), "12", "stroke ≤ 12 pt")
    equals(stroke_box.input_value(), "2", "default 2 pt")
    clean(mock)


# --------------------------------------------------------------------------
# Rectangle / ellipse
# --------------------------------------------------------------------------

@test("a_rectangle_drag_is_its_box_in_points_one_step_even_over_a_slot")
def _(page, mock: MockApi) -> None:
    mock.set_pdf(synthetic_multipage_pdf(3, [rule_line(s) for s in mock.slots]))
    open_doc(page, mock)
    arm_tool(page, "rect")
    classes = page.locator('[data-testid="workspace"]').get_attribute("class") or ""
    contains(classes, "workspace--drawing", "the workspace says a drag tool is armed")
    contains(classes, "workspace--placing", "the slots step aside")
    equals(page.locator('[data-testid="ws-capture-1"]').count(), 1, "page 1 has a capture layer")
    page.evaluate("() => { window.__swmOverlayRenders = {}; }")
    # Starts ON the sig_1 slot box (72,160 180x34): it still draws.
    ax, ay = px(page, 80, 170)
    bx, by = px(page, 180.2, 220.1)
    page.mouse.move(ax, ay)
    page.mouse.down()
    page.mouse.move(bx, by, steps=12)
    preview = page.locator('[data-testid="ws-capture-1"] [data-testid="capture-preview"]')
    equals(preview.count(), 1, "a live preview on page 1")
    equals(page.locator('[data-testid="capture-preview"]').count(), 1, "and on no other page")
    equals(preview.get_attribute("data-box-pt"), "80,170,100,50", "the preview's box (pt)")
    renders = page.evaluate("() => window.__swmOverlayRenders || {}")
    equals(sum(v for k, v in renders.items() if k != "1"), 0, f"no other page re-rendered ({renders})")
    page.mouse.up()
    wait_stored(page, mock, 1)
    item = stored(page, mock)[0]
    equals(item["kind"], "rect", "a rectangle")
    for key, expected in (("x_pt", 80), ("y_pt", 170), ("w_pt", 100), ("h_pt", 50)):
        approx(item[key], expected, 0.5, f"{key} (pt)")
    equals((item["stroke_pt"], item["color"], item["fill"]), (2, "black", False), "defaults 2 pt, black, no fill")
    equals(depth(page), 1, "ONE undo step")
    equals(page.locator('[data-testid="capture-preview"]').count(), 0, "the preview is gone")
    shape = page.locator(f'[data-testid="ann-shape-{item["id"]}"]')
    scale = scale_of(page)
    approx(float(shape.get_attribute("stroke-width")), 2 * scale, 0.01, "stroke 2 pt (px)")
    equals(shape.get_attribute("fill"), "none", "no fill")
    approx(float(shape.get_attribute("x")), 80 * scale, 0.01, "drawn at x (px): the stroke is centred on it")
    approx(float(shape.get_attribute("width")), 100 * scale, 0.01, "drawn w (px)")
    equals([c for c in mock.calls if "annotation-assets" in c], [], "no server call")
    shot(page, "rect")
    clean(mock)


@test("shift_draws_a_square_and_a_circle_that_stay_on_the_page")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm_tool(page, "rect")
    drag_pt(page, 100, 250, 180, 280, shift=True)
    arm_tool(page, "ellipse")
    drag_pt(page, 300, 250, 330, 330, shift=True)
    # From 30 pt left of the right edge, 60 pt wide with Shift: the square stops at the edge.
    arm_tool(page, "rect")
    drag_pt(page, PAGE_W - 30, 400, PAGE_W - 30 + 25, 460, shift=True)
    wait_stored(page, mock, 3)
    square, circle, edge = stored(page, mock)
    equals((square["w_pt"], square["h_pt"]), (80, 80), "Shift: a square (the longer side)")
    equals((circle["kind"], circle["w_pt"], circle["h_pt"]), ("ellipse", 80, 80), "Shift: a circle")
    truthy(edge["x_pt"] + edge["w_pt"] <= PAGE_W + 0.01, f"inside the page ({edge})")
    equals(edge["w_pt"], edge["h_pt"], "still square at the edge")
    clean(mock)


@test("fill_is_translucent_stroke_is_its_width_and_both_travel")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm_tool(page, "ellipse")
    page.locator('[data-testid="ann-fill"]').click()
    equals(page.locator('[data-testid="ann-fill"]').get_attribute("aria-pressed"), "true", "fill on")
    stroke = page.locator('[data-testid="ann-stroke"]')
    stroke.fill("4.5")
    page.locator('[data-testid="ann-color"]').click()
    page.locator('[data-testid="ann-color-blue"]').click()
    drag_pt(page, 150, 300, 250, 360)
    wait_stored(page, mock, 1)
    item = stored(page, mock)[0]
    equals((item["fill"], item["stroke_pt"], item["color"]), (True, 4.5, "blue"), "fill, 4.5 pt, blue")
    shape = page.locator(f'[data-testid="ann-shape-{item["id"]}"]')
    equals(shape.evaluate("(el) => el.tagName"), "ellipse", "an SVG ellipse")
    equals(shape.get_attribute("fill"), PALETTE["blue"], "filled with its own colour")
    approx(float(shape.get_attribute("fill-opacity")), 0.2, 0.0001, "at alpha 0.2 (never opaque)")
    equals(shape.get_attribute("stroke"), PALETTE["blue"], "stroked in #1D4ED8")
    approx(float(shape.get_attribute("stroke-width")), 4.5 * scale_of(page), 0.01, "4.5 pt (px)")
    # Change the selected one: one step each.
    select_ann(page, item["id"])
    before = depth(page)
    page.locator('[data-testid="ann-fill"]').click()
    equals(stored(page, mock)[0]["fill"], False, "unfilled")
    equals(depth(page), before + 1, "one step")
    stroke.fill("12")
    page.wait_for_timeout(100)
    equals(stored(page, mock)[0]["stroke_pt"], 12, "12 pt")
    equals(depth(page), before + 2, "one step")
    stroke.fill("40")
    page.wait_for_timeout(100)
    equals(stored(page, mock)[0]["stroke_pt"], 12, "a value past 12 is not taken")
    shot(page, "fill_stroke")
    clean(mock)


# --------------------------------------------------------------------------
# Line / arrow
# --------------------------------------------------------------------------

@test("a_line_and_an_arrow_are_their_two_ends_and_a_click_is_nothing")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm_tool(page, "line")
    drag_pt(page, 100, 450, 250, 480)
    arm_tool(page, "arrow")
    page.locator('[data-testid="ann-stroke"]').fill("3")
    drag_pt(page, 300, 450, 420, 520)
    # A click (no travel) draws nothing.
    ax, ay = px(page, 350, 300)
    page.mouse.click(ax, ay)
    page.wait_for_timeout(200)
    wait_stored(page, mock, 2)
    line, arrow = stored(page, mock)
    equals([line[k] for k in ("x1_pt", "y1_pt", "x2_pt", "y2_pt")], [100, 450, 250, 480], "line ends (pt)")
    equals([arrow[k] for k in ("x1_pt", "y1_pt", "x2_pt", "y2_pt")], [300, 450, 420, 520], "arrow ends (pt)")
    equals(depth(page), 2, "two steps, the click added none")
    # The arrow head: tip at (x2, y2), length 6 × 3 = 18 pt, half-width 3 × 3 = 9 pt.
    head = page.locator(f'[data-testid="ann-line-{arrow["id"]}"] polygon').get_attribute("data-head-pt")
    points = [tuple(float(v) for v in pair.split(",")) for pair in head.split()]
    tip, left, right = points
    equals(tip, (420.0, 520.0), "the tip is the end")
    length = math.hypot(120, 70)
    ux, uy = 120 / length, 70 / length
    base = (420 - ux * 18, 520 - uy * 18)
    approx(left[0], base[0] - uy * 9, 0.01, "left corner x")
    approx(left[1], base[1] + ux * 9, 0.01, "left corner y")
    approx(right[0], base[0] + uy * 9, 0.01, "right corner x")
    shaft = page.locator(f'[data-testid="ann-line-{arrow["id"]}"] line')
    equals(shaft.get_attribute("stroke-linecap"), "butt", "butt caps")
    approx(float(shaft.get_attribute("x2")), base[0] * scale_of(page), 0.05, "the shaft stops at the head's base")
    shot(page, "line_arrow")
    clean(mock)


@test("shift_snaps_a_line_to_45_degrees_inside_the_page")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm_tool(page, "line")
    drag_pt(page, 100, 300, 300, 318, shift=True)      # ~5° -> 0°
    drag_pt(page, 100, 400, 190, 500, shift=True)      # ~48° -> 45°
    arm_tool(page, "arrow")
    drag_pt(page, 500, 200, 640, 190, shift=True)      # past the right edge, ~0° -> stopped there
    wait_stored(page, mock, 3)
    flat, diagonal, edge = stored(page, mock)
    approx(flat["y2_pt"], 300, 0.01, "0°: y stays")
    approx(flat["x2_pt"], 100 + math.hypot(200, 18), 0.5, "same length along the snapped ray")
    approx(diagonal["x2_pt"] - 100, diagonal["y2_pt"] - 400, 0.02, "45°: dx = dy")
    approx(edge["y2_pt"], 200, 0.01, "still horizontal")
    approx(edge["x2_pt"], PAGE_W, 0.01, "stopped on the page edge, along the ray")
    clean(mock)


# --------------------------------------------------------------------------
# Ink
# --------------------------------------------------------------------------

@test("ink_is_thinned_by_rdp_and_a_click_is_a_dot")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm_tool(page, "ink")
    # A straight stroke of 80 pointer moves is two points after RDP.
    drag_pt(page, 100, 300, 300, 300, steps=80)
    # A wave keeps its turns.
    ax, ay = px(page, 100, 400)
    page.mouse.move(ax, ay)
    page.mouse.down()
    raw = 0
    for i in range(1, 121):
        x, y = px(page, 100 + i * 2, 400 + 20 * math.sin(i / 6))
        page.mouse.move(x, y)
        raw += 1
    page.mouse.up()
    page.wait_for_timeout(150)
    # A click: a dot.
    cx, cy = px(page, 450, 300)
    page.mouse.click(cx, cy)
    wait_stored(page, mock, 3)
    straight, wave, dot = stored(page, mock)
    equals(len(straight["paths"]), 1, "one path")
    equals(len(straight["paths"][0]), 2, "a straight stroke is its two ends")
    approx(straight["paths"][0][0]["x_pt"], 100, 0.5, "starts where pressed (pt)")
    approx(straight["paths"][0][-1]["x_pt"], 300, 0.5, "ends where released (pt)")
    points = wave["paths"][0]
    truthy(8 < len(points) < raw, f"the wave is thinned ({raw} moves -> {len(points)} points)")
    truthy(all(0 <= p["x_pt"] <= PAGE_W and 0 <= p["y_pt"] <= PAGE_H for p in points), "every point on the page")
    equals(len(dot["paths"][0]), 2, "a click is a 2-point path ...")
    equals(dot["paths"][0][0], dot["paths"][0][1], "... on one spot: a round dot")
    path = page.locator(f'[data-testid="ann-ink-{wave["id"]}"]')
    equals(path.get_attribute("stroke-linecap"), "round", "round caps")
    equals(path.get_attribute("stroke-linejoin"), "round", "round joins")
    equals(depth(page), 3, "one step per stroke")
    shot(page, "ink")
    clean(mock)


@test("a_stroke_too_long_for_one_element_is_split_in_one_step")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm_tool(page, "ink")
    # 4500 zig-zag points (every one a turn: RDP keeps them all), fed straight
    # to the capture layer between a real press and a real release.
    ax, ay = px(page, 20, 300)
    page.mouse.move(ax, ay)
    page.mouse.down()
    page.evaluate(
        """([x0, y0, scale]) => {
          const el = document.querySelector('[data-testid="ws-capture-1"]');
          for (let i = 1; i <= 4500; i += 1) {
            const x = x0 + (i * 0.12) * scale, y = y0 + (i % 2) * 3 * scale;
            el.dispatchEvent(new PointerEvent('pointermove', {
              bubbles: true, pointerId: 1, pointerType: 'mouse', clientX: x, clientY: y }));
          }
        }""",
        [ax, ay, scale_of(page)],
    )
    bx, by = px(page, 20 + 4500 * 0.12, 300)
    page.mouse.move(bx, by)
    page.mouse.up()
    page.wait_for_timeout(300)
    items = [item for item in stored(page, mock) if item["kind"] == "ink"]
    equals(len(items), 2, "split into two ink elements")
    for item in items:
        truthy(1 <= len(item["paths"]) <= 20, "≤ 20 paths per element")
        truthy(all(2 <= len(path) <= 2000 for path in item["paths"]), "2…2000 points per path")
        truthy(sum(len(path) for path in item["paths"]) <= 4000, "≤ 4000 points per element")
    paths = [path for item in items for path in item["paths"]]
    equals([len(path) for path in paths][:2], [2000, 2000], "full pieces of 2000 points")
    for before, after in zip(paths, paths[1:]):
        equals(before[-1], after[0], "consecutive pieces share their joint: the line is unbroken")
    total = sum(len(path) for path in paths) - (len(paths) - 1)
    truthy(total > 4400, f"a zig-zag keeps (nearly) every point through RDP ({total} of 4501)")
    equals(depth(page), 1, "ONE undo step for the whole stroke")
    page.keyboard.press("Control+z")
    wait_stored(page, mock, 0)
    clean(mock)


@test("the_20000_ink_point_ceiling_is_refused_out_loud")
def _(page, mock: MockApi) -> None:
    def ink(n: int) -> dict:
        path = [{"x_pt": 20 + (i % 500) * 1.1, "y_pt": 100 + (i // 500) * 3} for i in range(1999)]
        return {"id": f"k{n}", "kind": "ink", "page": 1, "paths": [path, path], "stroke_pt": 1, "color": "black"}
    seed = [ink(n) for n in range(5)]  # 5 × 3998 = 19 990 points
    open_doc(page, mock, seed=seed)
    equals(len(stored(page, mock)), 5, "19 990 points are read back")
    arm_tool(page, "ink")
    ax, ay = px(page, 100, 500)
    page.mouse.move(ax, ay)
    page.mouse.down()
    for i in range(1, 30):
        x, y = px(page, 100 + i * 4, 500 + (i % 2) * 8)
        page.mouse.move(x, y)
    page.mouse.up()
    page.wait_for_timeout(300)
    equals(len(stored(page, mock)), 5, "the stroke was not kept")
    contains(note_text(page), "limited to 20,000 points", "and the note says why")
    equals(depth(page), 0, "no step")
    clean(mock)


# --------------------------------------------------------------------------
# Handles and moving
# --------------------------------------------------------------------------

@test("eight_handles_resize_a_rectangle_shift_makes_it_square")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock, seed=[seed_rect()])
    select_ann(page, "r1")
    equals(page.locator('[data-testid^="ann-handle-"]').count(), 8, "eight handles")
    equals(page.locator('[data-testid="ann-handle"]').count(), 0, "no size grip on a shape")
    handle_drag(page, "ann-handle-se", 20, 10)
    item = stored(page, mock)[0]
    equals([item[k] for k in ("x_pt", "y_pt", "w_pt", "h_pt")], [100, 250, 120, 60], "SE: grows, NW corner stays")
    equals(depth(page), 1, "one step")
    handle_drag(page, "ann-handle-nw", -10, -20)
    item = stored(page, mock)[0]
    equals([item[k] for k in ("x_pt", "y_pt", "w_pt", "h_pt")], [90, 230, 130, 80], "NW: SE corner stays")
    handle_drag(page, "ann-handle-n", 0, 200)
    item = stored(page, mock)[0]
    equals(item["y_pt"] + item["h_pt"], 310, "the bottom edge stays ...")
    equals(item["h_pt"], 1, "... and the box stops at 1 pt, never inside out")
    page.keyboard.press("Control+z")
    page.wait_for_timeout(120)
    select_ann(page, "r1")
    handle_drag(page, "ann-handle-se", 10, 60, shift=True)
    item = stored(page, mock)[0]
    equals(item["w_pt"], item["h_pt"], f"Shift on a corner: a square ({item})")
    handle_drag(page, "ann-handle-e", 900, 0)
    item = stored(page, mock)[0]
    approx(item["x_pt"] + item["w_pt"], PAGE_W, 0.01, "the right edge stops at the page's")
    shot(page, "rect_handles")
    clean(mock)


@test("an_image_keeps_its_ratio_unless_shift")
def _(page, mock: MockApi) -> None:
    mock.assets[ASSET_1] = solid_png(400, 200)
    open_doc(page, mock, seed=[seed_image()])
    page.wait_for_function(
        "() => document.querySelector('[data-testid=\"ann-image-i1\"]')?.dataset.state === 'ready'", timeout=10000)
    equals(sum(1 for c in mock.calls if c == f"GET /api/contracts/{mock.contract_id}/annotation-assets/{ASSET_1}"),
           1, "the preview is fetched once")
    select_ann(page, "i1")
    handle_drag(page, "ann-handle-se", 50, 0)
    item = stored(page, mock)[0]
    equals((item["w_pt"], item["h_pt"]), (250, 125), "corner: 2 : 1 kept")
    handle_drag(page, "ann-handle-e", -50, 0)
    item = stored(page, mock)[0]
    equals((item["w_pt"], item["h_pt"]), (200, 100), "side edge: the height follows")
    equals((item["x_pt"], item["y_pt"]), (100, 250), "the top-left stayed")
    handle_drag(page, "ann-handle-se", 0, 40, shift=True)
    item = stored(page, mock)[0]
    equals((item["w_pt"], item["h_pt"]), (200, 140), "Shift: the ratio is free")
    handle_drag(page, "ann-handle-se", 600, 0)
    item = stored(page, mock)[0]
    truthy(item["x_pt"] + item["w_pt"] <= PAGE_W + 0.01, "stops at the page edge ...")
    approx(item["w_pt"] / item["h_pt"], 200 / 140, 0.01, "... still in its (new) ratio")
    equals(depth(page), 4, "one step per drag")
    image = page.locator('[data-testid="ann-image-i1"]')
    equals(image.get_attribute("preserveAspectRatio"), "none", "it fills its box, as the PDF will")
    shot(page, "image_handles")
    clean(mock)


@test("line_ends_move_and_snap_with_shift")
def _(page, mock: MockApi) -> None:
    seed = [{"id": "l1", "kind": "arrow", "page": 1, "x1_pt": 100, "y1_pt": 450, "x2_pt": 250, "y2_pt": 480,
             "stroke_pt": 2, "color": "red"}]
    open_doc(page, mock, seed=seed)
    select_ann(page, "l1")
    equals(page.locator('[data-testid^="ann-handle-"]').count(), 2, "two handles: the ends")
    handle_drag(page, "ann-handle-end", 50, 20)
    item = stored(page, mock)[0]
    equals([item[k] for k in ("x1_pt", "y1_pt", "x2_pt", "y2_pt")], [100, 450, 300, 500], "the head moved")
    handle_drag(page, "ann-handle-start", 0, -10, shift=True)
    item = stored(page, mock)[0]
    equals(item["y1_pt"], item["y2_pt"], f"Shift: the start snapped level with the head ({item})")
    equals((item["x2_pt"], item["y2_pt"]), (300, 500), "the head stayed")
    equals(depth(page), 2, "one step per drag")
    page.keyboard.press("Control+z")
    page.wait_for_timeout(120)
    equals(stored(page, mock)[0]["y1_pt"], 450, "undo: back")
    clean(mock)


@test("ink_and_notes_move_whole_and_stay_on_the_page")
def _(page, mock: MockApi) -> None:
    seed = [
        {"id": "k1", "kind": "ink", "page": 1, "stroke_pt": 2, "color": "green",
         "paths": [[{"x_pt": 100, "y_pt": 500}, {"x_pt": 150, "y_pt": 520}, {"x_pt": 200, "y_pt": 500}]]},
        {"id": "n1", "kind": "note", "page": 1, "x_pt": 400, "y_pt": 300, "text": "Check this", "color": "yellow"},
    ]
    open_doc(page, mock, seed=seed)
    arm_tool(page, "select")
    drag_ann(page, "k1", 30, -20)
    ink = stored(page, mock)[0]
    equals([(p["x_pt"], p["y_pt"]) for p in ink["paths"][0]], [(130, 480), (180, 500), (230, 480)],
           "every point moved 30 right, 20 up")
    equals(page.locator('[data-testid^="ann-handle"]').count(), 0, "ink has no handles")
    # Far past the left edge: the leftmost point stops at 0.
    drag_ann(page, "k1", -400, 0)
    ink = stored(page, mock)[0]
    equals(min(p["x_pt"] for p in ink["paths"][0]), 0, "stopped on the left edge")
    # The note: moved whole, its icon kept on the page.
    drag_ann(page, "n1", 400, 20)
    note = stored(page, mock)[1]
    equals(note["x_pt"], PAGE_W - 20, "the icon (20 pt) stops at the right edge")
    equals(note["y_pt"], 320, "moved down 20")
    equals(depth(page), 3, "one step per drag")
    clean(mock)


# --------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------

@test("a_note_is_multi_line_shows_its_text_and_refuses_what_the_pdf_cannot_carry")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm_tool(page, "note")
    ax, ay = px(page, 450, 300)
    page.mouse.click(ax, ay)
    box = page.locator('[data-testid="note-input"]')
    box.wait_for(timeout=5000)
    page.keyboard.type("Deliver by Friday")
    page.keyboard.press("Enter")
    page.keyboard.type("Dock 4")
    contains(page.locator('[data-testid="note-count"]').inner_text(), "24 / 1000", "a counter")
    page.keyboard.press("Control+Enter")
    wait_stored(page, mock, 1)
    note = stored(page, mock)[0]
    equals(note["text"], "Deliver by Friday\nDock 4", "two lines, one line break")
    approx(note["x_pt"], 450, 0.51, "icon at the click: x (pt, half-point grid)")
    approx(note["y_pt"], 300, 0.51, "icon at the click: y (pt)")
    equals(note["color"], "yellow", "yellow by default")
    equals(depth(page), 1, "one step")
    icon = page.locator(f'[data-testid="ann-note-{note["id"]}"]')
    equals(icon.count(), 1, "the icon is drawn")
    popup = page.locator(f'[data-testid="note-popup-{note["id"]}"]')
    equals(popup.inner_text(), "Deliver by Friday\nDock 4", "selected: its text is shown, lines kept")
    shot(page, "note")
    # Re-edit: a Cyrillic word is refused BEFORE anything is kept.
    arm_tool(page, "select")
    page.locator(f'[data-testid="ann-{note["id"]}"]').dblclick()
    box.wait_for(timeout=5000)
    equals(box.input_value(), "Deliver by Friday\nDock 4", "the box opens with the text")
    page.keyboard.press("End")
    page.keyboard.type(" Привет")
    page.keyboard.press("Control+Enter")
    error = page.locator('[data-testid="note-input-error"]')
    error.wait_for(timeout=5000)
    contains(error.inner_text(), "(U+041F)", "names the character")
    contains(error.inner_text(), "Western European", "and why")
    contains(note_text(page), "cannot carry", "the same sentence in the editor note")
    equals(stored(page, mock)[0]["text"], "Deliver by Friday\nDock 4", "nothing changed")
    shot(page, "note_refused")
    page.keyboard.press("Escape")
    equals(page.locator('[data-testid="note-editor"]').count(), 0, "Esc cancels")
    equals(depth(page), 1, "still one step")
    # An empty note is a cancel.
    arm_tool(page, "note")
    bx, by = px(page, 300, 500)
    page.mouse.click(bx, by)
    box.wait_for(timeout=5000)
    page.keyboard.press("Control+Enter")
    equals(len(stored(page, mock)), 1, "an empty note is not kept")
    clean(mock)


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

@test("an_image_is_uploaded_once_as_json_and_placed_at_its_natural_ratio")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm_tool(page, "image")
    contains(page.locator('[data-testid="workspace"]').get_attribute("class") or "", "workspace--placing",
             "the slots step aside for the image tool")
    upload_via_click(page, 100, 420, "stamp.png", "image/png", solid_png(400, 200))
    wait_stored(page, mock, 1)
    equals(len(mock.asset_uploads), 1, "ONE upload")
    body = mock.asset_uploads[0]
    equals(sorted(body.keys()), ["image_data"], "a JSON body with image_data only")
    truthy(body["image_data"].startswith("data:image/png;base64,"), "a PNG data URL")
    item = stored(page, mock)[0]
    equals(item["asset_id"], ASSET_1, "the server's asset id")
    approx(item["x_pt"], 100, 0.51, "top-left at the click: x (pt, half-point grid)")
    approx(item["y_pt"], 420, 0.51, "top-left at the click: y (pt)")
    equals((item["w_pt"], item["h_pt"]), (200, 100), "400 × 200 px = 300 × 150 pt, fitted to 200 pt: ratio kept")
    page.wait_for_function(
        f"() => document.querySelector('[data-testid=\"ann-image-{item['id']}\"]')?.dataset.state === 'ready'",
        timeout=10000)
    gets = [c for c in mock.calls if c.startswith("GET") and "annotation-assets" in c]
    equals(gets, [f"GET /api/contracts/{mock.contract_id}/annotation-assets/{ASSET_1}"], "previewed through GET, once")
    equals(depth(page), 1, "placing is one step")
    shot(page, "image_placed")
    page.keyboard.press("Control+z")
    wait_stored(page, mock, 0)
    truthy(not any(c.startswith("DELETE") for c in mock.calls), "undo removes the annotation, not the upload")
    page.keyboard.press("Control+y")
    wait_stored(page, mock, 1)
    clean(mock)


@test("image_refusals_are_said_in_words_before_and_after_the_upload")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm_tool(page, "image")
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (3 * 1024 * 1024)
    upload_via_click(page, 100, 420, "huge.png", "image/png", big)
    contains(note_text(page), "the limit is 2 MB", "a 3 MB file: refused here")
    upload_via_click(page, 100, 420, "notes.png", "image/png", b"hello, I am text")
    contains(note_text(page), "is not a PNG or JPEG", "a text file named .png: refused here")
    upload_via_click(page, 100, 420, "wide.png", "image/png", solid_png(4001, 4))
    contains(note_text(page), "at most 4000 px", "4001 px wide: refused here")
    equals(len(mock.asset_uploads), 0, "none of those reached the server")
    cases = [
        (413, {"error": "payload_too_large", "message": "Image too large", "details": {}}, "too large for the server"),
        (422, {"error": "validation_error", "message": "Image dimensions are too large", "details": {}},
         "could not use this image"),
        (409, {"error": "conflict", "message": "Contract is not approved", "details": {}},
         "once the contract is approved"),
        (409, {"error": "conflict", "message": "A contract can carry at most 100 annotation images",
               "details": {"limit": 100}}, "maximum of 100 uploaded images"),
    ]
    for status, body, words in cases:
        mock.asset_response = {"status": status, "body": body}
        upload_via_click(page, 100, 420, "stamp.png", "image/png", solid_png(40, 20))
        page.wait_for_function(
            "(w) => (document.querySelector('[data-testid=\"editor-note\"]')?.textContent || '').includes(w)",
            arg=words, timeout=5000)
    equals(len(mock.asset_uploads), 4, "four uploads were tried")
    equals(stored(page, mock), [], "nothing was placed")
    equals(depth(page), 0, "no step")
    shot(page, "image_refused")
    clean(mock)


@test("the_image_tool_is_off_on_an_unapproved_contract_drawing_is_not")
def _(page, mock: MockApi) -> None:
    mock.review_status = "pending"
    mock.status = "analyzed"
    open_doc(page, mock, panel="review")
    image = page.locator('[data-testid="tool-image"]')
    truthy(image.is_disabled(), "Image is off")
    equals(image.get_attribute("data-disabled-reason"), NOT_APPROVED, "and says why")
    page.keyboard.press("m")
    page.wait_for_timeout(100)
    equals(image.get_attribute("aria-pressed"), "false", "M does not arm it")
    arm_tool(page, "rect")
    drag_pt(page, 100, 250, 200, 300)
    wait_stored(page, mock, 1)
    equals(stored(page, mock)[0]["kind"], "rect", "a local drawing still works")
    equals(len(mock.asset_uploads), 0, "nothing uploaded")
    clean(mock)


# --------------------------------------------------------------------------
# Esc, undo, rotation
# --------------------------------------------------------------------------

@test("esc_drops_a_drag_in_flight")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    for tool in ("rect", "line", "ink"):
        arm_tool(page, tool)
        ax, ay = px(page, 100, 250)
        bx, by = px(page, 220, 330)
        page.mouse.move(ax, ay)
        page.mouse.down()
        page.mouse.move(bx, by, steps=8)
        equals(page.locator('[data-testid="capture-preview"]').count(), 1, f"{tool}: a preview")
        page.keyboard.press("Escape")
        page.wait_for_timeout(80)
        equals(page.locator('[data-testid="capture-preview"]').count(), 0, f"{tool}: Esc drops it")
        page.mouse.up()
        page.wait_for_timeout(120)
    equals(stored(page, mock), [], "nothing was kept")
    equals(depth(page), 0, "no step")
    clean(mock)


@test("every_tool_is_one_undo_step_and_redo_puts_it_back")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm_tool(page, "rect")
    drag_pt(page, 100, 250, 200, 300)
    arm_tool(page, "arrow")
    drag_pt(page, 250, 250, 350, 300)
    arm_tool(page, "ink")
    drag_pt(page, 100, 350, 300, 380, steps=30)
    arm_tool(page, "note")
    ax, ay = px(page, 450, 250)
    page.mouse.click(ax, ay)
    page.locator('[data-testid="note-input"]').wait_for(timeout=5000)
    page.keyboard.type("x")
    page.locator('[data-testid="note-save"]').click()
    wait_stored(page, mock, 4)
    equals(depth(page), 4, "four drawings, four steps")
    arm_tool(page, "select")
    page.locator("body").click(position={"x": 5, "y": 5})
    for remaining in (3, 2, 1, 0):
        page.keyboard.press("Control+z")
        wait_stored(page, mock, remaining)
    for count in (1, 2, 3, 4):
        page.keyboard.press("Control+y")
        wait_stored(page, mock, count)
    equals([item["kind"] for item in stored(page, mock)], ["rect", "arrow", "ink", "note"], "all back, in order")
    # Delete: one step too.
    first = stored(page, mock)[0]["id"]
    select_ann(page, first)
    page.keyboard.press("Delete")
    wait_stored(page, mock, 3)
    page.keyboard.press("Control+z")
    wait_stored(page, mock, 4)
    clean(mock)


@test("a_turned_page_draws_nothing")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm_tool(page, "rect")
    page.locator('[data-testid="pdf-rotate"]').click()
    page.wait_for_timeout(400)
    box = origin(page)
    page.mouse.move(box["x"] + 100, box["y"] + 100)
    page.mouse.down()
    page.mouse.move(box["x"] + 300, box["y"] + 200, steps=8)
    page.mouse.up()
    page.wait_for_timeout(200)
    equals(stored(page, mock), [], "nothing is drawn on a turned page")
    truthy(page.locator('[data-testid="pdf-rotation-note"]').is_visible(), "the rotation notice is up")
    for _ in range(3):
        page.locator('[data-testid="pdf-rotate"]').click()
        page.wait_for_timeout(250)
    page.wait_for_timeout(300)
    drag_pt(page, 100, 250, 200, 300)
    wait_stored(page, mock, 1)
    clean(mock)


# --------------------------------------------------------------------------
# Storage and the finalize body
# --------------------------------------------------------------------------

@test("stored_drawings_survive_a_reload_and_rubbish_is_dropped")
def _(page, mock: MockApi) -> None:
    mock.assets[ASSET_1] = solid_png(40, 20)
    seed = [
        seed_rect(color="white", stroke_pt=40, fill="yes"),
        {"id": "e0", "kind": "ellipse", "page": 1, "x_pt": 10, "y_pt": 10, "w_pt": 0.5, "h_pt": 30},
        {"id": "l0", "kind": "line", "page": 1, "x1_pt": 10, "y1_pt": 10, "x2_pt": 10.4, "y2_pt": 10.4},
        {"id": "l1", "kind": "line", "page": 1, "x1_pt": 10, "y1_pt": 10, "x2_pt": 90, "y2_pt": 10},
        {"id": "k0", "kind": "ink", "page": 1, "paths": [[{"x_pt": 1, "y_pt": "x"}]]},
        {"id": "n0", "kind": "note", "page": 1, "x_pt": 5, "y_pt": 5, "text": "   "},
        {"id": "n1", "kind": "note", "page": 1, "x_pt": 500, "y_pt": 5, "text": "Pink is no colour", "color": "pink"},
        {"id": "i0", "kind": "image", "page": 1, "asset_id": "../../etc", "x_pt": 1, "y_pt": 1, "w_pt": 9, "h_pt": 9},
        seed_image(id="i1", w_pt=40, h_pt=20),
    ]
    open_doc(page, mock, seed=seed)
    items = {item["id"]: item for item in stored(page, mock)}
    equals(sorted(items), ["i1", "l1", "n1", "r1"], "unreadable entries are dropped")
    rect = items["r1"]
    equals((rect["color"], rect["stroke_pt"], rect["fill"]), ("black", 12, False),
           "white → black, 40 pt → 12, 'yes' is not true")
    equals(items["n1"]["color"], "yellow", "not a palette colour: a note's default, yellow")
    equals(items["l1"]["color"], "black", "no colour: black")
    page.reload()
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_timeout(300)
    equals(sorted(item["id"] for item in stored(page, mock)), ["i1", "l1", "n1", "r1"], "and survive a reload")
    open_section(page, "objects")
    rows = page.locator('[data-testid="objects-list"]').inner_text()
    for words in ("Rectangle", "Line", "Note: Pink is no colour", "Image (40 × 20 pt)"):
        contains(rows, words, "named in Objects")
    clean(mock)


@test("the_finalize_body_carries_every_new_kind_exactly")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm_tool(page, "rect")
    drag_pt(page, 100, 250, 200, 300)
    arm_tool(page, "ellipse")
    page.locator('[data-testid="ann-fill"]').click()
    drag_pt(page, 220, 250, 300, 300)
    arm_tool(page, "line")
    drag_pt(page, 100, 330, 200, 330)
    arm_tool(page, "arrow")
    page.locator('[data-testid="ann-color"]').click()
    page.locator('[data-testid="ann-color-red"]').click()
    drag_pt(page, 220, 330, 300, 360)
    arm_tool(page, "ink")
    drag_pt(page, 100, 420, 200, 440, steps=20)
    arm_tool(page, "note")
    ax, ay = px(page, 450, 250)
    page.mouse.click(ax, ay)
    page.locator('[data-testid="note-input"]').wait_for(timeout=5000)
    page.keyboard.type("Line one")
    page.keyboard.press("Enter")
    page.keyboard.type("Line two")
    page.keyboard.press("Control+Enter")
    arm_tool(page, "image")
    upload_via_click(page, 300, 420, "logo.png", "image/png", solid_png(120, 60))
    wait_stored(page, mock, 7)
    body = finalize_body(page, mock)
    sent = body.get("annotations") or []
    equals([item["kind"] for item in sent], ["rect", "ellipse", "line", "arrow", "ink", "note", "image"],
           "every drawing, in the order placed")
    for item in sent:
        equals(sorted(item.keys()), KEYS[item["kind"]], f"exactly the backend's keys for {item['kind']}")
    by_kind = {item["kind"]: item for item in sent}
    equals(by_kind["ellipse"]["fill"], True, "fill as a bool")
    equals(by_kind["rect"]["fill"], False, "and false is sent too")
    equals(by_kind["arrow"]["color"], "red", "colour always named")
    equals(by_kind["note"]["text"], "Line one\nLine two", "the note's lines")
    equals(by_kind["note"]["color"], "yellow", "a note is yellow by default")
    equals(by_kind["image"]["asset_id"], ASSET_1, "the uploaded asset")
    truthy(all(2 <= len(path) for path in by_kind["ink"]["paths"]), "ink paths of ≥ 2 points")
    truthy("x_pt" not in by_kind["line"] and "x_pt" not in by_kind["ink"], "no derived anchor on the wire")
    out = os.environ.get("SWM_DRAW_BODY_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(body, handle, indent=2, ensure_ascii=False)
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
