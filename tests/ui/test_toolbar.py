"""
W2: the tool registry, the floating toolbar, the colour palette and the ONE
undo history (annotations + slot positions).

WHAT THESE CHECKS ARE FOR
-------------------------
  * the toolbar opens DOCKED on top, one row, exactly where the old one was
    (every check in this directory clicks page pixels), with every hook the
    old one had (`tool-<id>`, `ann-font`, `ann-size`, `ann-date`,
    `ann-delete`, `editor-undo`/`-redo`, `undo-depth`/`redo-depth`,
    `ann-count`, `sig-count`);
  * the grip floats it; the top edge docks it, the left / right edges stand
    it up vertically; the placement survives a reload (`swm.toolbar`) and a
    stored rubbish value is ignored; a floating panel is pulled back into the
    column when the window shrinks; it folds to the armed tool alone;
  * the keyboard: V T D K X S arm the six tools (never inside an input, never
    with Alt/Ctrl), Alt+T lands on the armed tool, the arrows rove between
    the tools without nudging a selected annotation, Esc gives the focus
    back; the grip moves with the arrows and docks with Enter;
  * ≤ 640 px: a bar fixed to the bottom, no dragging, properties in a
    pop-over; a row that runs out of room hides the tool labels first, then
    moves the last groups under «More ▾»;
  * a disabled tool's reason is a one-line badge, never a second row;
  * the palette: seven colours (no white), the preview paints the backend's
    exact hex values, the colour travels in the finalize body -- and black
    leaves the key out entirely (the v4 body, byte for byte);
  * ONE history: a slot drag is undone by Ctrl+Z and redone by Ctrl+Y, the
    panel's controls are steps (a slider dragged along is one step), the
    page-edge clamp is NOT a step, and a server write never enters it;
  * the memo: while one annotation (or one slot) is dragged, the other pages'
    overlays do not re-render (`window.__swmOverlayRenders`, dev build).

Nothing here opens a real contract: the documents are the synthetic ones from
`_harness.py` and every `/api/*` call is answered by `MockApi`. Screenshots
go to `c:\\PTG\\.playwright-mcp\\swm_w2\\` and are synthetic by construction.
"""

from __future__ import annotations

import json
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
    by_field,
    cli_main,
    contains,
    drag_slot,
    equals,
    goto_blank,
    open_review,
    open_sign,
    rule_line,
    seed_layout,
    slot_stepper,
    snapshot,
    stored_layout,
    synthetic_multipage_pdf,
    truthy,
)

test = Registry("toolbar")

PAGE_W = 612.0
NOT_APPROVED = "Contract not approved yet"
TOOLBAR_KEY = "swm.toolbar"
#: `lib/annotations.ts` ANNOTATION_COLOR_HEX == backend `pdf_builder.py:177`.
PALETTE = {
    "black": "#000000",
    "blue": "#1D4ED8",
    "red": "#DC2626",
    "green": "#16A34A",
    "yellow": "#FACC15",
    "orange": "#EA580C",
    "purple": "#7E22CE",
}
SHORTCUTS = {"select": "V", "text": "T", "date": "D", "check": "K", "cross": "X", "signature": "S",
             "highlight": "H", "underline": "U", "strikeout": "C", "edit-text": "E",
             "ink": "P", "rect": "R", "ellipse": "O", "line": "L", "arrow": "A", "note": "N",
             "image": "M", "initials": "I", "whiteout": "W"}
#: Every tool, in registry order (group, then order). F1a regrouped them into
#: the spec's menus (EDITOR-V2-PLAN §7): Select | Text (text, edit-text, date)
#: | Forms (check, cross) | Images (image) | Sign (signature, initials) |
#: Whiteout | Annotate (highlight, underline, strikeout, ink, note) | Shapes
#: (rect, ellipse, line, arrow). Still 19 tools, every `tool-<id>` kept.
ALL_TOOLS = ["select", "text", "edit-text", "date", "check", "cross", "image",
             "signature", "initials", "whiteout",
             "highlight", "underline", "strikeout", "ink", "note",
             "rect", "ellipse", "line", "arrow"]
#: The ROW of the docked toolbar (W4, F1a): the tools that share a drop-down
#: are ONE button there (`toolgroup-<menu>`, at its first member's place);
#: their own `tool-<id>` buttons are in the drop-down's list.
TOOL_ORDER = ["select", "toolgroup-text", "toolgroup-forms", "toolgroup-images", "toolgroup-sign",
              "whiteout", "toolgroup-annotate", "toolgroup-shapes"]
#: The drop-downs' members, in registry order (every tool but Select and Whiteout).
MENU_MEMBERS = ["text", "edit-text", "date", "check", "cross", "image", "signature", "initials",
                "highlight", "underline", "strikeout", "ink", "note",
                "rect", "ellipse", "line", "arrow"]
#: The hooks the old toolbar had; the docked default must keep every one of them.
KEPT_HOOKS = [
    "editor-toolbar", "ann-font", "ann-size", "ann-date", "ann-delete", "editor-undo",
    "editor-redo", "undo-depth", "redo-depth", "ann-count",
] + [f"tool-{name}" for name in ALL_TOOLS]


def tid(name: str) -> str:
    """The testid of a row entry: `tool-<id>`, or a drop-down's own `toolgroup-<menu>`."""
    return name if name.startswith("toolgroup-") else f"tool-{name}"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def shot(page, name: str) -> None:
    folder = os.path.join("c:\\", "PTG", ".playwright-mcp", "swm_w2")
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, f"{name}.png"))


def clean(mock: MockApi) -> None:
    equals(mock.page_errors, [], "no uncaught error")
    equals(mock.unmocked, [], "no unmocked API call")


def open_edit(page, mock: MockApi, seed_annotations: str | None = None) -> None:
    if seed_annotations is not None:
        goto_blank(page, mock)
        page.evaluate(
            "([key, value]) => localStorage.setItem(key, value)",
            [f"swm.annotations.{mock.contract_id}", seed_annotations],
        )
    page.goto(f"{BASE}/contracts/{mock.contract_id}/edit")
    wait_ready(page)


def wait_ready(page) -> None:
    page.wait_for_selector('[data-testid="editor-toolbar"]', timeout=30000)
    page.wait_for_function(
        "() => { const p = document.querySelector('.pdf-page');"
        " return !!p && p.getBoundingClientRect().width > 300; }",
        timeout=30000,
    )
    page.wait_for_timeout(400)


def toolbar(page):
    return page.locator('[data-testid="editor-toolbar"]')


def tb_box(page) -> dict:
    box = toolbar(page).bounding_box()
    if box is None:
        raise AssertionError("the toolbar has no box")
    return box


def stage_box(page) -> dict:
    box = page.locator(".workspace__stage").bounding_box()
    if box is None:
        raise AssertionError("the stage has no box")
    return box


def tb_attr(page, name: str) -> str | None:
    return toolbar(page).get_attribute(name)


DEFAULT_LAYOUT = {
    "signature_offset_pt": 0, "date_offset_pt": 0, "slot_offsets_pt": {}, "slot_offsets_x_pt": {},
    "signature_scale": 1, "date_font": "Helvetica", "date_font_size": 11,
}


def layout_now(page, mock: MockApi) -> dict:
    """The stored layout; a default one is stored as NO key (`saveLayout`)."""
    return stored_layout(page, mock) or DEFAULT_LAYOUT


def stored_placement(page) -> dict | None:
    raw = page.evaluate("(key) => localStorage.getItem(key)", TOOLBAR_KEY)
    return json.loads(raw) if raw else None


def drag_grip_to(page, x: float, y: float, steps: int = 14) -> None:
    """Press the grip's centre and release it at (x, y), viewport pixels."""
    grip = page.locator('[data-testid="toolbar-grip"]')
    box = grip.bounding_box()
    if box is None:
        raise AssertionError("no grip on screen")
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + 6, cy + 6)
    page.mouse.move(x, y, steps=steps)
    page.mouse.up()
    page.wait_for_timeout(250)


def float_in_middle(page) -> None:
    stage = stage_box(page)
    drag_grip_to(page, stage["x"] + stage["width"] * 0.25, stage["y"] + stage["height"] * 0.45)
    equals(tb_attr(page, "data-mode"), "floating", "the toolbar floats")


def tool_pressed(page, name: str) -> bool:
    return page.locator(f'[data-testid="tool-{name}"]').get_attribute("aria-pressed") == "true"


def active_testid(page) -> str | None:
    return page.evaluate("() => document.activeElement && document.activeElement.dataset"
                         " ? document.activeElement.dataset.testid || null : null")


def focus_in_toolbar(page) -> bool:
    return page.evaluate(
        "() => document.querySelector('[data-testid=\"editor-toolbar\"]').contains(document.activeElement)"
    )


def depth(page, which: str) -> int:
    return int(page.locator(f'[data-testid="{which}-depth"]').inner_text().strip())


def page_rect(page) -> dict:
    box = page.locator('.pdf-page[data-page-number="1"]').bounding_box()
    if box is None:
        raise AssertionError("page 1 has no box")
    return box


def marks(page) -> list[dict]:
    return page.evaluate(
        """() => [...document.querySelectorAll('.swm-ann')].map((el) => {
          const id = el.dataset.annId;
          const text = document.querySelector(`.swm-ann__text[data-ann-id="${id}"]`);
          const path = document.querySelector(`.swm-ann__mark[data-ann-id="${id}"] path`);
          return {
            id, kind: el.dataset.kind, page: Number(el.dataset.page),
            x: Number(el.dataset.xPt), y: Number(el.dataset.yPt),
            color: el.dataset.color, text: text ? text.textContent : null,
            fill: text ? getComputedStyle(text).fill : null,
            stroke: path ? getComputedStyle(path).stroke : null,
          };
        })"""
    )


def wait_for_marks(page, count: int) -> None:
    page.wait_for_function(
        "(n) => document.querySelectorAll('.swm-ann').length === n", arg=count, timeout=15000
    )


def place_text(page, value: str, x_px: float, y_px: float, expect: int) -> dict:
    arm_tool(page, "text")
    box = page_rect(page)
    page.mouse.click(box["x"] + x_px, box["y"] + y_px)
    page.wait_for_selector('[data-testid="ann-input"]', timeout=10000)
    page.keyboard.type(value)
    page.keyboard.press("Enter")
    wait_for_marks(page, expect)
    page.wait_for_timeout(120)
    return next(m for m in marks(page) if m["text"] == value)


def pick_color(page, name: str) -> None:
    page.locator('[data-testid="ann-color"]').click()
    page.wait_for_selector('[data-testid="ann-color-palette"]', timeout=5000)
    page.locator(f'[data-testid="ann-color-{name}"]').click()
    page.wait_for_timeout(120)


def rgb(hex_value: str) -> str:
    value = hex_value.lstrip("#")
    r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgb({r}, {g}, {b})"


def finalize_body(page, mock: MockApi) -> dict:
    apply_dates_to_all(page)
    page.locator('[data-testid="editor-finalize"]').click()
    page.wait_for_function("() => true")
    for _ in range(100):
        if "finalize" in mock.bodies:
            break
        page.wait_for_timeout(100)
    body = mock.bodies.get("finalize")
    truthy(body is not None, "finalize was sent")
    return body


# --------------------------------------------------------------------------
# Docked by default
# --------------------------------------------------------------------------

@test("the_toolbar_opens_docked_on_top_with_every_old_hook")
def _(page, mock: MockApi) -> None:
    """Default `docked-top`: in the flow, one row, above the viewer, all hooks kept."""
    open_edit(page, mock)
    equals(tb_attr(page, "data-mode"), "docked-top", "docked by default")
    equals(tb_attr(page, "data-orientation"), "horizontal", "horizontal")
    equals(tb_attr(page, "data-overflow"), "0", "nothing under «More ▾» at 1600 px")
    for hook in KEPT_HOOKS:
        equals(page.locator(f'[data-testid="{hook}"]').count(), 1, f"{hook} is on the docked toolbar")
    equals(page.locator('[data-testid="ann-color"]').count(), 1, "and the new colour button")
    position = toolbar(page).evaluate("(el) => getComputedStyle(el).position")
    truthy(position in ("relative", "static"), f"in the column's flow ({position})")
    box = tb_box(page)
    truthy(box["height"] < 64, f"one row ({box['height']} px)")
    viewer = page.locator(".pdf-viewer").bounding_box()
    truthy(box["y"] + box["height"] <= viewer["y"] + 1, "above the viewer, not over it")
    for name, letter in SHORTCUTS.items():
        equals(page.locator(f'[data-testid="tool-{name}"]').get_attribute("aria-keyshortcuts"),
               letter, f"{name} announces its key")
    order = page.evaluate(
        "() => [...document.querySelectorAll('.ws-toolbar__tools .editor__tool')]"
        ".map((b) => b.dataset.testid.replace(/^tool-/, ''))"
    )
    equals(order, TOOL_ORDER, "the registry's order (group, then order), one button per drop-down")
    members = page.evaluate(
        "() => [...document.querySelectorAll('.ws-toolbar__tools [role=\"menu\"] [data-testid^=\"tool-\"]')]"
        ".map((b) => b.dataset.testid.replace(/^tool-/, ''))"
    )
    equals(members, MENU_MEMBERS, "the drop-downs hold the rest, in registry order")
    equals(stored_placement(page), None, "nothing is written until the user moves it")
    clean(mock)
    shot(page, "docked")


# --------------------------------------------------------------------------
# Dragging, docking, snapping, persistence, clamping
# --------------------------------------------------------------------------

@test("dragging_the_grip_floats_it_and_a_reload_keeps_it_there")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    stage = stage_box(page)
    target_x = stage["x"] + stage["width"] * 0.25
    target_y = stage["y"] + stage["height"] * 0.45
    grip = page.locator('[data-testid="toolbar-grip"]').bounding_box()
    grab_dx = grip["x"] + grip["width"] / 2 - tb_box(page)["x"]
    drag_grip_to(page, target_x, target_y)
    equals(tb_attr(page, "data-mode"), "floating", "released in the middle: floating")
    equals(tb_attr(page, "data-orientation"), "horizontal", "still a row")
    position = toolbar(page).evaluate("(el) => getComputedStyle(el).position")
    equals(position, "absolute", "out of the flow")
    box = tb_box(page)
    approx(box["x"], target_x - grab_dx, 3, "followed the pointer (x)")
    stored = stored_placement(page)
    equals(stored["mode"], "floating", "stored as floating")
    shot(page, "floating_middle")

    page.reload()
    wait_ready(page)
    equals(tb_attr(page, "data-mode"), "floating", "still floating after a reload")
    after = tb_box(page)
    approx(after["x"], box["x"], 1.5, "same x after a reload")
    approx(after["y"], box["y"], 1.5, "same y after a reload")
    clean(mock)


@test("a_broken_stored_placement_is_ignored")
def _(page, mock: MockApi) -> None:
    goto_blank(page, mock)
    page.evaluate("(key) => localStorage.setItem(key, '{not json')", TOOLBAR_KEY)
    page.goto(f"{BASE}/contracts/{mock.contract_id}/edit")
    wait_ready(page)
    equals(tb_attr(page, "data-mode"), "docked-top", "unreadable JSON: the default")
    page.evaluate(
        "(key) => localStorage.setItem(key, JSON.stringify({mode: 'sideways', x: 'NaN', y: null,"
        " orientation: 'diagonal', collapsed: 'yes'}))",
        TOOLBAR_KEY,
    )
    page.reload()
    wait_ready(page)
    equals(tb_attr(page, "data-mode"), "docked-top", "an unknown mode: docked")
    equals(tb_attr(page, "data-collapsed"), "false", "'yes' is not true")
    page.evaluate(
        "(key) => localStorage.setItem(key, JSON.stringify({mode: 'floating', x: 99999, y: -99999,"
        " orientation: 'horizontal', collapsed: false}))",
        TOOLBAR_KEY,
    )
    page.reload()
    wait_ready(page)
    page.wait_for_timeout(300)
    equals(tb_attr(page, "data-mode"), "floating", "a floating one is honoured")
    stage = stage_box(page)
    box = tb_box(page)
    truthy(box["x"] <= stage["x"] + stage["width"] - 48 + 1, f"but pulled back into the column ({box})")
    truthy(box["y"] >= stage["y"] - 1, "and not above it")
    clean(mock)


@test("a_floating_toolbar_is_pulled_back_when_the_window_shrinks")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    page.locator('[data-testid="toolbar-collapse"]').click()
    page.wait_for_timeout(150)
    stage = stage_box(page)
    drag_grip_to(page, stage["x"] + stage["width"] * 0.7, stage["y"] + stage["height"] * 0.85)
    equals(tb_attr(page, "data-mode"), "floating", "floating low on the right")
    before = stored_placement(page)
    page.set_viewport_size({"width": 1100, "height": 640})
    page.wait_for_timeout(600)
    stage = stage_box(page)
    box = tb_box(page)
    truthy(box["x"] <= stage["x"] + stage["width"] - 48 + 1,
           f"≥ 48 px still inside horizontally ({box} in {stage})")
    truthy(box["y"] <= stage["y"] + stage["height"] - 48 + 1,
           f"≥ 48 px still inside vertically ({box} in {stage})")
    truthy(box["x"] + box["width"] >= stage["x"] + 48 - 1, "and not lost off the left")
    after = stored_placement(page)
    truthy(after["y"] < before["y"], f"the clamp was stored ({before} → {after})")
    clean(mock)


@test("dropping_on_the_edges_docks_or_stands_it_up")
def _(page, mock: MockApi) -> None:
    """Top edge → docked; left / right edge → vertical on that edge."""
    open_edit(page, mock)
    float_in_middle(page)
    stage = stage_box(page)

    drag_grip_to(page, stage["x"] + 4, stage["y"] + stage["height"] * 0.3)
    equals(tb_attr(page, "data-orientation"), "vertical", "left edge: vertical")
    equals(tb_attr(page, "data-edge"), "left", "on the left edge")
    box = tb_box(page)
    approx(box["x"], stage["x"] + 8, 2.5, "8 px from the left edge")
    truthy(box["height"] > box["width"], f"taller than wide ({box})")
    equals(page.locator('[data-testid="ann-font"]').count(), 0, "properties folded away")
    page.locator('[data-testid="toolbar-properties"]').click()
    page.wait_for_selector('[data-testid="toolbar-properties-popover"]', timeout=5000)
    truthy(page.locator('[data-testid="ann-font"]').is_visible(), "and one click opens them")
    shot(page, "vertical_left")
    page.keyboard.press("Escape")

    drag_grip_to(page, stage["x"] + stage["width"] - 6, stage["y"] + stage["height"] * 0.5)
    equals(tb_attr(page, "data-edge"), "right", "right edge: vertical on the right")
    box = tb_box(page)
    approx(box["x"] + box["width"], stage["x"] + stage["width"] - 8, 3, "8 px from the right edge")

    drag_grip_to(page, stage["x"] + stage["width"] * 0.4, stage["y"] + 3)
    equals(tb_attr(page, "data-mode"), "docked-top", "top edge: docked again")
    equals(tb_attr(page, "data-orientation"), "horizontal", "and a row again")
    equals(stored_placement(page)["mode"], "docked-top", "stored")
    clean(mock)


@test("collapse_folds_it_to_the_armed_tool_and_stays_folded")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm_tool(page, "check")
    collapse =page.locator('[data-testid="toolbar-collapse"]')
    equals(collapse.get_attribute("aria-expanded"), "true", "expanded at first")
    collapse.click()
    page.wait_for_timeout(150)
    equals(tb_attr(page, "data-collapsed"), "true", "collapsed")
    equals(collapse.get_attribute("aria-expanded"), "false", "and says so")
    equals(page.locator(".ws-toolbar__tools .editor__tool").count(), 1, "one tool left")
    truthy(tool_pressed(page, "check"), "the armed one")
    equals(page.locator('[data-testid="ann-font"]').count(), 0, "no properties")
    truthy(tb_box(page)["width"] < 160, "small")
    float_in_middle(page)
    shot(page, "collapsed")
    page.reload()
    wait_ready(page)
    equals(tb_attr(page, "data-collapsed"), "true", "still folded after a reload")
    page.locator('[data-testid="toolbar-collapse"]').click()
    page.wait_for_timeout(150)
    equals(page.locator(".ws-toolbar__tools .editor__tool").count(), len(TOOL_ORDER),
           "all of them back")
    clean(mock)


# --------------------------------------------------------------------------
# Keyboard
# --------------------------------------------------------------------------

@test("one_letter_shortcuts_arm_tools_but_never_while_typing")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    for key, name in [("t", "text"), ("k", "check"), ("x", "cross"), ("d", "date"),
                      ("s", "signature"), ("v", "select"), ("T", "text"), ("e", "edit-text"),
                      ("p", "ink"), ("r", "rect"), ("o", "ellipse"), ("l", "line"), ("a", "arrow"),
                      ("n", "note"), ("m", "image"), ("i", "initials"), ("w", "whiteout"),
                      ("v", "select")]:
        page.keyboard.press(key)
        page.wait_for_timeout(60)
        truthy(tool_pressed(page, name), f"'{key}' arms {name}")

    # Alt+letter is not a shortcut.
    page.keyboard.press("Alt+x")
    page.wait_for_timeout(60)
    truthy(tool_pressed(page, "select"), "Alt+X arms nothing")

    # In the search box the letters are text.
    search = page.locator('[data-testid="pdf-search-input"]')
    search.click()
    page.keyboard.type("tkx")
    page.wait_for_timeout(100)
    truthy(tool_pressed(page, "select"), "typing in the search box arms nothing")
    equals(search.input_value(), "tkx", "and the letters went into the box")
    search.fill("")

    # In the inline text box, too.
    arm_tool(page, "text")
    box = page_rect(page)
    page.mouse.click(box["x"] + 200, box["y"] + 300)
    page.wait_for_selector('[data-testid="ann-input"]', timeout=10000)
    page.keyboard.type("vdks")
    equals(page.locator('[data-testid="ann-input"]').input_value(), "vdks", "the run got every letter")
    truthy(tool_pressed(page, "text"), "and the tool stayed Text")
    page.keyboard.press("Enter")
    wait_for_marks(page, 1)
    clean(mock)


@test("roving_focus_moves_between_the_tools_and_never_nudges_a_mark")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    mark = place_text(page, "Roving", 200, 300, 1)
    page.locator('[data-testid="tool-select"]').click()
    page.locator(f'[data-testid="ann-{mark["id"]}"]').click()
    page.wait_for_timeout(120)

    page.keyboard.press("Alt+t")
    page.wait_for_timeout(80)
    equals(active_testid(page), "tool-select", "Alt+T lands on the armed tool")
    tab_stops = page.evaluate(
        "() => [...document.querySelectorAll('.ws-toolbar__tools .editor__tool')]"
        ".filter((b) => b.tabIndex === 0).length"
    )
    equals(tab_stops, 1, "the tools are ONE tab stop")

    page.keyboard.press("ArrowRight")
    equals(active_testid(page), tid(TOOL_ORDER[1]), "→ the next stop (F1a: «Text ▾»)")
    page.keyboard.press("End")
    equals(active_testid(page), tid(TOOL_ORDER[-1]), "End → the last")
    page.keyboard.press("Home")
    equals(active_testid(page), "tool-select", "Home → the first")
    page.keyboard.press("ArrowLeft")
    equals(active_testid(page), tid(TOOL_ORDER[-1]), "← wraps round")
    page.keyboard.press("ArrowUp")
    equals(active_testid(page), tid(TOOL_ORDER[-2]), "↑ works in a row, too")
    after = next(m for m in marks(page) if m["id"] == mark["id"])
    equals((after["x"], after["y"]), (mark["x"], mark["y"]),
           "the selected annotation did not move under the arrows")

    page.keyboard.press("Home")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")
    page.wait_for_timeout(80)
    # F1a: Home → → is «Forms ▾», whose main half arms the member it shows (Check).
    equals(active_testid(page), "toolgroup-forms", "Home → → lands on «Forms ▾»")
    truthy(tool_pressed(page, "check"), "Enter arms the focused tool")

    page.keyboard.press("Alt+t")
    truthy(focus_in_toolbar(page), "back in the toolbar")
    page.keyboard.press("Escape")
    page.wait_for_timeout(80)
    truthy(not focus_in_toolbar(page), "Esc hands the focus back to the document")
    clean(mock)


@test("the_grip_moves_with_the_arrows_and_docks_with_enter")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    grip = page.locator('[data-testid="toolbar-grip"]')
    grip.focus()
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(120)
    equals(tb_attr(page, "data-mode"), "floating", "an arrow floats it")
    first = tb_box(page)
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(120)
    approx(tb_box(page)["x"], first["x"] + 10, 1, "→ 10 px")
    page.keyboard.press("Shift+ArrowDown")
    page.wait_for_timeout(120)
    approx(tb_box(page)["y"], first["y"] + 50, 1, "Shift+↓ 50 px")
    page.keyboard.press("Enter")
    page.wait_for_timeout(120)
    equals(tb_attr(page, "data-mode"), "docked-top", "Enter docks it")
    page.keyboard.press("Enter")
    page.wait_for_timeout(120)
    equals(tb_attr(page, "data-mode"), "floating", "and floats it again")
    clean(mock)


# --------------------------------------------------------------------------
# Narrow
# --------------------------------------------------------------------------

@test("at_640_px_it_is_a_bar_at_the_bottom")
def _(page, mock: MockApi) -> None:
    page.set_viewport_size({"width": 600, "height": 900})
    page.goto(f"{BASE}/contracts/{mock.contract_id}/edit")
    page.wait_for_selector('[data-testid="editor-toolbar"]', timeout=30000)
    page.wait_for_timeout(700)
    equals(tb_attr(page, "data-mode"), "mobile", "the bottom bar")
    equals(toolbar(page).evaluate("(el) => getComputedStyle(el).position"), "fixed", "fixed")
    box = tb_box(page)
    approx(box["y"] + box["height"], 900, 1.5, "on the bottom edge")
    approx(box["width"], 600, 1.5, "full width")
    truthy(page.locator('[data-testid="toolbar-grip"]').is_disabled(), "no dragging")
    heights = page.evaluate(
        "() => [...document.querySelectorAll('.ws-toolbar__tools .editor__tool')]"
        ".map((b) => b.getBoundingClientRect().height)"
    )
    truthy(all(h >= 44 for h in heights), f"44 px targets ({heights})")
    equals(toolbar(page).evaluate("(el) => getComputedStyle(el).overflowX"), "auto", "scrolls sideways")
    arm_tool(page, "check")
    truthy(tool_pressed(page, "check"), "a tool works")
    page.locator('[data-testid="toolbar-properties"]').click()
    popover = page.locator('[data-testid="toolbar-properties-popover"]')
    popover.wait_for(timeout=5000)
    truthy(page.locator('[data-testid="ann-size"]').is_visible(), "properties in a pop-over")
    pop = popover.bounding_box()
    truthy(pop["y"] >= 0 and pop["y"] + pop["height"] <= box["y"] + 1, f"above the bar ({pop})")
    shot(page, "mobile_640")
    clean(mock)


@test("when_room_runs_out_the_last_groups_go_under_more")
def _(page, mock: MockApi) -> None:
    page.set_viewport_size({"width": 1280, "height": 1000})
    open_edit(page, mock)
    more = page.locator('[data-testid="toolbar-more"]')
    equals(more.count(), 1, "«More ▾» appears")
    hidden = int(tb_attr(page, "data-overflow") or "0")
    truthy(hidden >= 1, f"{hidden} group(s) under it")
    equals(page.locator('[data-testid="ann-count"]').count(), 0, "the counts moved under it")
    truthy(tb_box(page)["height"] < 64, "still one row")
    more.click()
    menu = page.locator('[data-testid="toolbar-more-menu"]')
    menu.wait_for(timeout=5000)
    contains(menu.inner_text(), "0 / 500", "the counts are in the menu")
    # F1a: Undo / Redo are part of the tools row («… | Shapes ▾ | Undo | Redo»),
    # so they never go under «More ▾» -- still there, still ONE each.
    equals(menu.locator('[data-testid="editor-undo"]').count(), 0, "Undo is not under «More ▾»")
    equals(page.locator('[data-testid="editor-undo"]').count(), 1, "ONE Undo")
    truthy(page.locator('.ws-toolbar__main [data-testid="editor-undo"]').is_visible(),
           "it stays in the row, right after the tools")
    truthy(page.locator('.ws-toolbar__main [data-testid="editor-redo"]').is_visible(), "so does Redo")
    page.keyboard.press("Escape")
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.wait_for_timeout(600)
    equals(page.locator('[data-testid="toolbar-more"]').count(), 0, "wide again: no «More ▾»")
    equals(page.locator('[data-testid="ann-count"]').count(), 1, "the counts are back in the row")
    clean(mock)


@test("a_disabled_tool_says_why_without_a_second_row")
def _(page, mock: MockApi) -> None:
    mock.review_status = "pending"
    mock.status = "analyzed"
    open_review(page, mock)
    wait_ready(page)
    reason = page.locator('[data-testid="tool-disabled-reason"]')
    contains(reason.inner_text(), NOT_APPROVED, "the reason is there in words")
    contains(reason.get_attribute("title") or "", NOT_APPROVED, "and in full on hover")
    truthy(reason.bounding_box()["height"] < 30, "one line")
    truthy(tb_box(page)["height"] < 64, f"the toolbar stayed one row ({tb_box(page)['height']})")
    truthy(page.locator('[data-testid="tool-signature"]').is_disabled(), "Signature is off")
    page.keyboard.press("s")
    page.wait_for_timeout(80)
    truthy(not tool_pressed(page, "signature"), "and 's' does not arm it")
    page.keyboard.press("t")
    truthy(tool_pressed(page, "text"), "a local tool still arms")
    clean(mock)


# --------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------

@test("the_palette_paints_the_backend_hex_and_travels_in_the_finalize_body")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm_tool(page, "text")
    equals(page.locator('[data-testid="ann-color"]').get_attribute("data-color"), "black", "black first")
    page.locator('[data-testid="ann-color"]').click()
    page.wait_for_selector('[data-testid="ann-color-palette"]', timeout=5000)
    options = page.evaluate(
        "() => [...document.querySelectorAll('.ws-color__option')].map((b) =>"
        " [b.dataset.testid.replace('ann-color-', ''), b.dataset.hex, getComputedStyle(b).backgroundColor])"
    )
    equals([o[0] for o in options], list(PALETTE), "seven colours, no white")
    for name, hex_value, background in options:
        equals(hex_value, PALETTE[name], f"{name} is the backend's hex")
        equals(background, rgb(PALETTE[name]), f"{name} swatch is painted with it")
    shot(page, "palette_open")
    page.locator('[data-testid="ann-color-red"]').click()
    equals(page.locator('[data-testid="ann-color-palette"]').count(), 0, "a pick closes the palette")

    red = place_text(page, "Red", 200, 300, 1)
    equals(red["color"], "red", "the run is red")
    equals(red["fill"], rgb(PALETTE["red"]), "painted #DC2626")

    arm_tool(page, "check")
    equals(page.locator('[data-testid="ann-color"]').get_attribute("data-color"), "black",
           "each tool keeps its own colour")
    pick_color(page, "blue")
    box = page_rect(page)
    page.mouse.click(box["x"] + 300, box["y"] + 420)
    wait_for_marks(page, 2)
    check = next(m for m in marks(page) if m["kind"] == "check")
    equals(check["color"], "blue", "the tick is blue")
    equals(check["stroke"], rgb(PALETTE["blue"]), "stroked #1D4ED8")

    arm_tool(page, "text")
    equals(page.locator('[data-testid="ann-color"]').get_attribute("data-color"), "red",
           "Text remembers red")
    pick_color(page, "black")
    plain = place_text(page, "Plain", 200, 500, 3)
    equals(plain["fill"], rgb(PALETTE["black"]), "black is #000000")

    # Recolouring a selected annotation is one step of the history.
    page.locator('[data-testid="tool-select"]').click()
    page.locator(f'[data-testid="ann-{red["id"]}"]').click()
    page.wait_for_timeout(120)
    equals(page.locator('[data-testid="ann-color"]').get_attribute("data-color"), "red",
           "the palette shows the selection's colour")
    before = depth(page, "undo")
    pick_color(page, "green")
    equals(next(m for m in marks(page) if m["id"] == red["id"])["color"], "green", "recoloured")
    equals(depth(page, "undo"), before + 1, "one step")
    page.locator('[data-testid="ann-count"]').click()  # the focus away from the palette
    page.keyboard.press("Control+z")
    page.wait_for_timeout(120)
    equals(next(m for m in marks(page) if m["id"] == red["id"])["color"], "red", "Ctrl+Z: red again")

    stored = json.loads(page.evaluate(
        "(key) => localStorage.getItem(key)", f"swm.annotations.{mock.contract_id}"))
    by_text = {entry.get("text"): entry for entry in stored if entry.get("text")}
    equals(by_text["Red"].get("color"), "red", "stored red")
    truthy("color" not in by_text["Plain"], "black is stored without the key")

    body = finalize_body(page, mock)
    sent = body["annotations"]
    sent_text = {entry.get("text"): entry for entry in sent if entry.get("text")}
    equals(sent_text["Red"].get("color"), "red", "the body carries red")
    equals(next(e for e in sent if e["kind"] == "check").get("color"), "blue", "and blue")
    truthy("color" not in sent_text["Plain"], "black leaves the key out (the v4 entry)")
    equals(sorted(sent_text["Plain"].keys()),
           ["font", "id", "kind", "page", "size_pt", "text", "x_pt", "y_pt"], "exactly the v4 keys")
    clean(mock)


@test("a_stored_colour_outside_the_palette_is_dropped")
def _(page, mock: MockApi) -> None:
    seed = json.dumps([
        {"id": "c1", "kind": "text", "page": 1, "x_pt": 100, "y_pt": 200, "text": "Purple",
         "font": "Helvetica", "size_pt": 12, "color": "purple"},
        {"id": "c2", "kind": "text", "page": 1, "x_pt": 100, "y_pt": 240, "text": "White",
         "font": "Helvetica", "size_pt": 12, "color": "white"},
        {"id": "c3", "kind": "check", "page": 1, "x_pt": 300, "y_pt": 240, "size_pt": 16,
         "color": "#DC2626"},
        {"id": "c4", "kind": "cross", "page": 1, "x_pt": 340, "y_pt": 240, "size_pt": 16, "color": 42},
    ])
    open_edit(page, mock, seed)
    wait_for_marks(page, 4)
    colors = {m["id"]: m["color"] for m in marks(page)}
    equals(colors, {"c1": "purple", "c2": "black", "c3": "black", "c4": "black"},
           "only a palette NAME survives")
    body = finalize_body(page, mock)
    sent = {entry["id"]: entry for entry in body["annotations"]}
    equals(sent["c1"].get("color"), "purple", "purple is sent")
    for ann_id in ("c2", "c3", "c4"):
        truthy("color" not in sent[ann_id], f"{ann_id} goes without a colour key")
    clean(mock)


# --------------------------------------------------------------------------
# One history
# --------------------------------------------------------------------------

@test("ctrl_z_takes_back_a_slot_drag_and_ctrl_y_puts_it_back")
def _(page, mock: MockApi) -> None:
    open_sign(page, mock, expect_images=2)
    equals(depth(page, "undo"), 0, "a clean history")
    drag_slot(page, "sig_1", 60, -40)
    layout = layout_now(page, mock)
    dx = layout["slot_offsets_x_pt"].get("sig_1", 0)
    dy = layout["slot_offsets_pt"].get("sig_1", 0)
    truthy(dx > 0 and dy > 0, f"the slot moved right and up ({dx}, {dy})")
    equals(depth(page, "undo"), 1, "one step for the whole drag")
    contains(page.locator('[data-testid="editor-undo"]').get_attribute("title") or "", "Move slot",
             "and Undo names it")

    page.keyboard.press("Control+z")
    page.wait_for_timeout(200)
    layout = layout_now(page, mock)
    equals(layout["slot_offsets_x_pt"].get("sig_1", 0), 0, "Ctrl+Z: dx back to 0")
    equals(layout["slot_offsets_pt"].get("sig_1", 0), 0, "and dy")
    approx(by_field(snapshot(page)["slots"], "sig_1")["leftPt"], 72.0, 0.01, "the box is home")
    equals(depth(page, "redo"), 1, "one step ahead")

    page.keyboard.press("Control+y")
    page.wait_for_timeout(200)
    layout = layout_now(page, mock)
    equals(layout["slot_offsets_x_pt"].get("sig_1"), dx, "Ctrl+Y: dx again")
    equals(layout["slot_offsets_pt"].get("sig_1"), dy, "and dy")
    approx(by_field(snapshot(page)["slots"], "sig_1")["leftPt"], 72.0 + dx, 0.01, "the box moved again")

    page.keyboard.press("Control+Shift+z")
    page.wait_for_timeout(100)
    equals(depth(page, "redo"), 0, "nothing further ahead")
    clean(mock)


@test("the_layout_panel_controls_are_steps_and_a_slider_is_one")
def _(page, mock: MockApi) -> None:
    open_sign(page, mock, expect_images=2)
    slot_stepper(page, "sig_2", "up").click()
    equals(depth(page, "undo"), 1, "a stepper press is a step")
    equals(layout_now(page, mock)["slot_offsets_pt"].get("sig_2"), 0.5, "up 0.5 pt")

    page.get_by_role("button", name="Everything together: move right").click()
    equals(depth(page, "undo"), 2, "«Move together» is a step")

    slider = page.get_by_label("Signature size")
    slider.focus()
    for _ in range(4):
        page.keyboard.press("ArrowRight")
    page.wait_for_timeout(150)
    approx(layout_now(page, mock)["signature_scale"], 1.2, 0.001, "four clicks of the slider")
    equals(depth(page, "undo"), 3, "a slider run is ONE step")

    page.locator('[data-testid="layout-panel"]').get_by_role("button", name="Reset", exact=True).click()
    page.wait_for_timeout(150)
    equals(depth(page, "undo"), 4, "Reset is a step")
    equals(layout_now(page, mock)["signature_scale"], 1, "reset")

    page.locator("body").click(position={"x": 5, "y": 5})
    for expected_scale, what in [(1.2, "undo Reset"), (1.0, "undo the slider run")]:
        page.keyboard.press("Control+z")
        page.wait_for_timeout(150)
        approx(layout_now(page, mock)["signature_scale"], expected_scale, 0.001, what)
    page.keyboard.press("Control+z")
    page.keyboard.press("Control+z")
    page.wait_for_timeout(150)
    layout = layout_now(page, mock)
    equals(layout["slot_offsets_pt"].get("sig_2", 0), 0, "and the stepper press, last of all")
    equals(depth(page, "undo"), 0, "back to the start")
    clean(mock)


@test("the_page_edge_clamp_is_not_an_undo_step")
def _(page, mock: MockApi) -> None:
    seed_layout(page, mock, {
        "signature_offset_pt": 0, "date_offset_pt": 0,
        "slot_offsets_pt": {}, "slot_offsets_x_pt": {"date_1": 200},
        "signature_scale": 1, "date_font": "Helvetica", "date_font_size": 11,
    })
    open_sign(page, mock, expect_images=2)
    page.wait_for_selector('[data-testid="clamp-note"]', timeout=15000)
    approx(layout_now(page, mock)["slot_offsets_x_pt"]["date_1"], 142.0, 0.01, "clamped")
    equals(depth(page, "undo"), 0, "the clamp is not a step")
    truthy(page.locator('[data-testid="editor-undo"]').is_disabled(), "Undo has nothing to take back")
    page.keyboard.press("Control+z")
    page.wait_for_timeout(200)
    approx(layout_now(page, mock)["slot_offsets_x_pt"]["date_1"], 142.0, 0.01,
           "Ctrl+Z does not push the slot back off the page")

    # A user step after the clamp undoes to the CLAMPED layout, never past it.
    slot_stepper(page, "date_1", "left").click()
    equals(depth(page, "undo"), 1, "the user's own step")
    page.keyboard.press("Control+z")
    page.wait_for_timeout(200)
    approx(layout_now(page, mock)["slot_offsets_x_pt"]["date_1"], 142.0, 0.01,
           "undone to the clamped value")
    clean(mock)


@test("a_server_write_never_enters_the_history")
def _(page, mock: MockApi) -> None:
    """Slot drag (a step) → free signature (not a step) → Ctrl+Z undoes the drag only."""
    open_edit(page, mock)
    drag_slot(page, "sig_2", 40, 0)
    equals(depth(page, "undo"), 1, "the drag is a step")

    arm_tool(page, "signature")
    box = page_rect(page)
    page.mouse.click(box["x"] + 420, box["y"] + 120)
    page.wait_for_selector(".modal__panel", timeout=15000)
    pad = page.locator(".signature-pad__canvas").bounding_box()
    mid = pad["y"] + pad["height"] / 2
    page.mouse.move(pad["x"] + 30, mid)
    page.mouse.down()
    page.mouse.move(pad["x"] + 140, mid - 20, steps=8)
    page.mouse.move(pad["x"] + 230, mid + 15, steps=8)
    page.mouse.up()
    page.get_by_role("button", name="Apply signature").click()
    page.wait_for_function("() => document.querySelectorAll('.swm-sig').length === 1", timeout=20000)
    equals(depth(page, "undo"), 1, "the placed signature added no step")

    page.keyboard.press("Control+z")
    page.wait_for_timeout(250)
    equals(layout_now(page, mock)["slot_offsets_x_pt"].get("sig_2", 0), 0, "Ctrl+Z undid the drag")
    equals(page.locator(".swm-sig").count(), 1, "and left the signature alone")
    truthy(not any(call.startswith("DELETE") for call in mock.calls), "no hidden DELETE")
    clean(mock)


# --------------------------------------------------------------------------
# The memo
# --------------------------------------------------------------------------

def reset_renders(page) -> None:
    page.evaluate("() => { window.__swmOverlayRenders = {}; }")


def renders(page) -> dict[int, int]:
    raw = page.evaluate("() => window.__swmOverlayRenders || null")
    if raw is None:
        raise AssertionError("no render counter: is this the dev server?")
    return {int(k): v for k, v in raw.items()}


@test("a_drag_re_renders_only_its_own_page")
def _(page, mock: MockApi) -> None:
    """`PageOverlay` of the other pages is skipped while a mark or a slot moves."""
    mock.set_pdf(synthetic_multipage_pdf(3, [rule_line(s) for s in mock.slots]))
    seed = json.dumps([
        {"id": "m1", "kind": "text", "page": 1, "x_pt": 100, "y_pt": 300, "text": "Memo",
         "font": "Helvetica", "size_pt": 14},
        {"id": "m2", "kind": "check", "page": 2, "x_pt": 100, "y_pt": 300, "size_pt": 16},
    ])
    open_edit(page, mock, seed)
    page.wait_for_selector('[data-testid="ann-m1"]', timeout=15000)
    page.locator('[data-testid="ann-m1"]').click()
    page.wait_for_timeout(200)

    reset_renders(page)
    handle = page.locator('[data-testid="ann-m1"]').bounding_box()
    cx = handle["x"] + handle["width"] / 2
    cy = handle["y"] + handle["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + 4, cy + 4)
    page.mouse.move(cx + 90, cy + 40, steps=20)
    during = renders(page)
    page.mouse.up()
    page.wait_for_timeout(250)
    after = renders(page)
    print(f"        annotation drag: during {during}, incl. release {after}")
    truthy(during.get(1, 0) >= 10, f"page 1 followed the pointer ({during})")
    equals(during.get(2, 0) + during.get(3, 0), 0, f"pages 2 and 3 were never rendered ({during})")
    equals(after.get(2, 0) + after.get(3, 0), 0,
           f"not even by the commit on release (page 2 kept its list) ({after})")

    reset_renders(page)
    handle = page.locator('.pdf-page .slot[data-field-id="sig_1"]').bounding_box()
    cx = handle["x"] + handle["width"] / 2
    cy = handle["y"] + handle["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + 4, cy + 4)
    page.mouse.move(cx + 60, cy + 30, steps=20)
    during = renders(page)
    page.mouse.up()
    page.wait_for_timeout(250)
    after = renders(page)
    print(f"        slot drag: during {during}, incl. release {after}")
    truthy(during.get(1, 0) >= 10, f"page 1 followed the slot ({during})")
    equals(during.get(2, 0) + during.get(3, 0), 0, f"pages 2 and 3 skipped while it moved ({during})")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
