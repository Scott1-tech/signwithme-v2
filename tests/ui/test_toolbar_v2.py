"""
F1a (EDITOR-V2-PLAN §7, §9): the Sejda-style toolbar and the editor's top bar
-- a SHELL: no new backend function, every tool is one that already existed.

WHAT THESE CHECKS PIN
---------------------
  * the row is the spec's order: Select | Text ▼ | Forms ▼ | Images ▼ |
    Sign ▼ | Whiteout | Annotate ▼ | Shapes ▼ | Undo | Redo -- and nothing
    that does not work yet is on it (no «Links», no Filled square, no form
    fields, no «Detect», no «coming soon» item);
  * every drop-down holds exactly its tools, in order, each with its letter;
  * the Sejda look, read from computed styles: #0099FF accent, #E9E9E9
    canvas under white pages, 34 ± 1 px controls, 3–4 px radius, 13–14 px
    text, white menus with a 1 px #D0D0D0 border and a shadow;
  * every drop-down opens INSIDE the viewport -- also from a toolbar stood up
    on the right edge, on the left edge, after the window shrinks with a list
    open, and above the ≤ 640 px bottom bar;
  * every button of the toolbar and the top bar has an accessible name and a
    tooltip; every icon is an inline SVG hidden from assistive tech;
  * 1600 px: nothing under «More ▾», no horizontal overflow, whatever is armed;
  * the keyboard: roving stops, Alt+↓ / F4 open a list onto its armed member,
    ↑ ↓ Home End move, Enter arms, Esc closes (focus back on the button);
  * the top bar: the contract's name, «Download PDF» (the uploaded PDF, or
    the signed final one once it exists), «Generate final contract»
    (`editor-finalize`, moved here from the panel -- same gate, same body),
    and NO «Edit PDF» / «Save changes» / «Cancel editing» yet (B1 / F1b);
  * the other pages keep their own theme: the Sejda tokens exist only inside
    `.workspace` (Dashboard's primary button is the app's #1F6FEB, as before).

Screenshots (synthetic, MockApi): `c:\\PTG\\.playwright-mcp\\swm_f1a\\`.
"""

from __future__ import annotations

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
    truthy,
)

test = Registry("toolbar v2")

#: The row, left to right (spec §7; «Links» comes with its tool in F2).
ROW = ["tool-select", "toolgroup-text", "toolgroup-forms", "toolgroup-images", "toolgroup-sign",
       "tool-whiteout", "toolgroup-annotate", "toolgroup-shapes"]
#: What each drop-down holds today, in order (only tools that exist).
MENUS = {
    "text": ["text", "edit-text", "date"],
    "forms": ["check", "cross"],
    "images": ["image"],
    "sign": ["signature", "initials"],
    "annotate": ["highlight", "underline", "strikeout", "ink", "note"],
    "shapes": ["rect", "ellipse", "line", "arrow"],
}
LETTERS = {"select": "V", "text": "T", "edit-text": "E", "date": "D", "check": "K", "cross": "X",
           "image": "M", "signature": "S", "initials": "I", "whiteout": "W", "highlight": "H",
           "underline": "U", "strikeout": "C", "ink": "P", "note": "N", "rect": "R", "ellipse": "O",
           "line": "L", "arrow": "A"}
ACCENT = "rgb(0, 153, 255)"
ACCENT_STRONG = "rgb(0, 122, 204)"
CANVAS = "rgb(233, 233, 233)"
BORDER = "rgb(208, 208, 208)"
WHITE = "rgb(255, 255, 255)"
#: `index.css` `--accent` -- the app's own primary colour, untouched outside the workspace.
APP_ACCENT = "rgb(31, 111, 235)"
#: Never on the toolbar in F1a (no tool behind them yet).
NOT_YET = ["Links", "Filled square", "Text field", "Multiline", "Dropdown", "Radio", "Checkbox",
           "Signature box", "Detect form fields", "Stamp", "Redact", "Freehand highlight",
           "Delete original image", "My signatures", "Coming"]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def shot(page, name: str, full: bool = False) -> None:
    folder = os.path.join("c:\\", "PTG", ".playwright-mcp", "swm_f1a")
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, f"{name}.png"), full_page=full)


def shot_of(locator, name: str) -> None:
    folder = os.path.join("c:\\", "PTG", ".playwright-mcp", "swm_f1a")
    os.makedirs(folder, exist_ok=True)
    locator.screenshot(path=os.path.join(folder, f"{name}.png"))


def clean(mock: MockApi) -> None:
    equals(mock.page_errors, [], "no uncaught error")
    equals(mock.unmocked, [], "no unmocked API call")
    warnings = [text for text in mock.console_errors if "Warning:" in text and "React Router" not in text]
    equals(warnings, [], "no React warning")


def open_doc(page, mock: MockApi, panel: str = "edit") -> None:
    page.goto(f"{BASE}/contracts/{mock.contract_id}/{panel}")
    page.wait_for_selector('[data-testid="editor-toolbar"]', timeout=30000)
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_function(
        "() => { const p = document.querySelector('.pdf-page');"
        " return !!p && p.getBoundingClientRect().width > 300; }",
        timeout=30000,
    )
    page.wait_for_timeout(400)


def toolbar(page):
    return page.locator('[data-testid="editor-toolbar"]')


def style(page, selector: str, prop: str) -> str:
    return page.evaluate(
        "([s, p]) => { const el = document.querySelector(s);"
        " return el ? getComputedStyle(el).getPropertyValue(p).trim() : null; }",
        [selector, prop],
    )


def px_value(value: str | None) -> float:
    truthy(value is not None and value.endswith("px"), f"a pixel value ({value})")
    return float(value[:-2])


def open_menu(page, menu: str):
    page.locator(f'[data-testid="toolgroup-{menu}-toggle"]').click()
    listing = page.locator(f'[data-testid="toolgroup-{menu}-menu"]')
    listing.wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(60)
    return listing


def close_menus(page) -> None:
    page.keyboard.press("Escape")
    page.wait_for_timeout(80)


def viewport(page) -> dict:
    return page.evaluate("() => ({w: document.documentElement.clientWidth, h: window.innerHeight})")


def inside_viewport(page, locator, what: str) -> dict:
    box = locator.bounding_box()
    truthy(box is not None, f"{what}: on screen")
    view = viewport(page)
    truthy(box["x"] >= -0.5, f"{what}: left edge inside ({box})")
    truthy(box["y"] >= -0.5, f"{what}: top edge inside ({box})")
    truthy(box["x"] + box["width"] <= view["w"] + 0.5, f"{what}: right edge inside ({box}, {view})")
    truthy(box["y"] + box["height"] <= view["h"] + 0.5, f"{what}: bottom edge inside ({box}, {view})")
    return box


def drag_grip_to(page, x: float, y: float) -> None:
    grip = page.locator('[data-testid="toolbar-grip"]').bounding_box()
    cx = grip["x"] + grip["width"] / 2
    cy = grip["y"] + grip["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + 6, cy + 6)
    page.mouse.move(x, y, steps=14)
    page.mouse.up()
    page.wait_for_timeout(250)


# --------------------------------------------------------------------------
# Order and content
# --------------------------------------------------------------------------

@test("the_row_is_the_spec_order_with_undo_and_redo_last")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    row = page.evaluate(
        "() => [...document.querySelectorAll('.ws-toolbar__tools .editor__tool')].map((b) => b.dataset.testid)"
    )
    equals(row, ROW, "Select | Text ▼ | Forms ▼ | Images ▼ | Sign ▼ | Whiteout | Annotate ▼ | Shapes ▼")
    labels = page.evaluate(
        "() => [...document.querySelectorAll('.ws-toolbar__tools > * .editor__tool-label,"
        " .ws-toolbar__tools > .editor__tool > .editor__tool-label')].map((l) => l.textContent)"
    )
    equals(labels, ["Select", "Text", "Forms", "Images", "Sign", "Whiteout", "Annotate", "Shapes"],
           "the row's words")
    # Undo / Redo come right after the last tool, in that order.
    order = page.evaluate(
        """() => {
          const main = document.querySelector('.ws-toolbar__main');
          const ids = [...main.querySelectorAll('[data-testid]')].map((e) => e.dataset.testid);
          return ids.filter((id) => id === 'toolgroup-shapes-menu' || id === 'editor-undo' || id === 'editor-redo');
        }"""
    )
    equals(order, ["toolgroup-shapes-menu", "editor-undo", "editor-redo"], "Shapes ▼, then Undo, then Redo")
    shapes = page.locator('[data-testid="toolgroup-shapes"]').bounding_box()
    undo = page.locator('[data-testid="editor-undo"]').bounding_box()
    redo = page.locator('[data-testid="editor-redo"]').bounding_box()
    truthy(shapes["x"] < undo["x"] < redo["x"], f"left to right on screen ({shapes['x']}, {undo['x']}, {redo['x']})")
    approx(undo["y"], shapes["y"], 2, "on the same row")
    equals(page.locator('[data-testid="editor-undo"]').count(), 1, "ONE Undo")
    equals(page.locator('[data-testid="editor-redo"]').count(), 1, "ONE Redo")
    # Nothing on the row that does not work yet.
    equals(page.locator('[data-testid="tool-links"], [data-testid="toolgroup-links"]').count(), 0, "no Links button")
    text = page.evaluate("() => document.querySelector('[data-testid=\"editor-toolbar\"]').textContent")
    for word in NOT_YET:
        truthy(word not in text, f"no «{word}» on the toolbar yet")
    equals(page.locator('[data-testid="editor-toolbar"] .editor__tool:disabled,'
                        ' [data-testid="editor-toolbar"] .ws-toolmenu__item:disabled').count(), 0,
           "no placeholder tool: every tool button and list item works on an approved contract")
    clean(mock)
    shot_of(toolbar(page), "toolbar")


@test("every_drop_down_holds_its_tools_in_order_with_their_letters")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    for menu, members in MENUS.items():
        listing = open_menu(page, menu)
        items = listing.evaluate(
            "(m) => [...m.querySelectorAll('[data-testid^=\"tool-\"]')].map((b) => b.dataset.testid.slice(5))"
        )
        equals(items, members, f"{menu} ▼ holds {members}")
        for name in members:
            item = page.locator(f'[data-testid="tool-{name}"]')
            equals(item.get_attribute("aria-keyshortcuts"), LETTERS[name], f"{name} keeps {LETTERS[name]}")
            equals(item.get_attribute("role"), "menuitemradio", f"{name} is a menu item")
            truthy(item.is_enabled(), f"{name} works (no «coming soon» item)")
            truthy(item.is_visible(), f"{name} is shown in the open list")
        text = listing.inner_text()
        for word in NOT_YET:
            truthy(word not in text, f"{menu} ▼ has no «{word}» yet")
        inside_viewport(page, listing, f"{menu} ▼")
        shot(page, f"menu_{menu}")
        close_menus(page)
        truthy(not listing.is_visible(), f"{menu} ▼ closed again")
    # Whiteout is a plain button (its ▾ Redact comes with the redact tool).
    equals(page.locator('[data-testid="toolgroup-whiteout"]').count(), 0, "no Whiteout drop-down yet")
    truthy(page.locator('[data-testid="tool-whiteout"]').is_visible(), "Whiteout is a button of the row")
    # The Annotate list is sectioned with light-grey headings.
    listing = open_menu(page, "annotate")
    headings = listing.evaluate(
        "(m) => [...m.querySelectorAll('.ws-toolmenu__section')].map((h) => h.textContent)"
    )
    equals(headings, ["Text markup", "Freehand", "Comment"], "the Annotate sections")
    heading_color = style(page, '[data-testid="toolgroup-annotate-menu"] .ws-toolmenu__section', "color")
    truthy(heading_color not in (ACCENT, "rgb(0, 0, 0)"), f"a light-grey heading ({heading_color})")
    close_menus(page)
    clean(mock)


# --------------------------------------------------------------------------
# The Sejda look
# --------------------------------------------------------------------------

@test("the_sejda_tokens_are_what_the_computed_styles_say")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    equals(style(page, ".workspace", "--sj-accent").lower(), "#0099ff", "the accent token")
    equals(style(page, ".workspace", "--sj-canvas").lower(), "#e9e9e9", "the canvas token")
    equals(style(page, ".workspace", "--sj-ctl-h"), "34px", "the control height token")
    # An idle row button: white, blue label and icon, 34 px, 4 px radius, 13 px text.
    main = '[data-testid="toolgroup-text"]'
    equals(style(page, main, "color"), ACCENT, "blue label")
    equals(style(page, main, "background-color"), WHITE, "white button")
    equals(style(page, f"{main} svg", "stroke"), ACCENT, "blue icon (inline SVG, currentColor)")
    for selector in ('[data-testid="tool-select"]', main, '[data-testid="toolgroup-shapes-toggle"]',
                     '[data-testid="tool-whiteout"]', '[data-testid="editor-undo"]'):
        height = page.locator(selector).bounding_box()["height"]
        approx(height, 34, 1, f"{selector}: 34 ± 1 px")
    for selector in ('[data-testid="tool-select"]', '[data-testid="tool-whiteout"]',
                     '[data-testid="editor-undo"]', '[data-testid="editor-finalize"]'):
        radius = px_value(style(page, selector, "border-top-left-radius"))
        truthy(3 <= radius <= 4, f"{selector}: 3–4 px radius ({radius})")
        size = px_value(style(page, selector, "font-size"))
        truthy(13 <= size <= 14, f"{selector}: 13–14 px text ({size})")
    # The armed tool: light blue with a blue border.
    armed = '[data-testid="tool-select"]'
    equals(style(page, armed, "border-top-color"), ACCENT, "armed: blue border")
    equals(style(page, armed, "background-color"), "rgb(214, 238, 255)", "armed: light blue")
    equals(style(page, armed, "color"), ACCENT_STRONG, "armed: a deeper blue label")
    # The toolbar itself: white, 1 px #D0D0D0, 4 px.
    equals(style(page, '[data-testid="editor-toolbar"]', "background-color"), WHITE, "white bar")
    # (its top edge is the shared, lighter seam under the top bar while docked)
    equals(style(page, '[data-testid="editor-toolbar"]', "border-bottom-color"), BORDER, "#D0D0D0 border")
    equals(style(page, '[data-testid="editor-toolbar"]', "border-bottom-width"), "1px", "1 px")
    equals(style(page, '[data-testid="editor-topbar"]', "border-top-color"), BORDER, "top bar: #D0D0D0 border")
    # The canvas and the pages.
    equals(style(page, ".pdf-viewer .pdf-stage", "background-color"), CANVAS, "#E9E9E9 behind the pages")
    equals(style(page, '.pdf-page[data-page-number="1"]', "background-color"), WHITE, "white pages")
    page_shadow = style(page, '.pdf-page[data-page-number="1"]', "box-shadow")
    truthy(page_shadow and page_shadow != "none", f"a restrained page shadow ({page_shadow})")
    # A drop-down: white panel, 1 px #D0D0D0, 4 px, a soft shadow, blue icons, dark labels.
    open_menu(page, "shapes")
    listing = '[data-testid="toolgroup-shapes-menu"]'
    equals(style(page, listing, "background-color"), WHITE, "white menu")
    equals(style(page, listing, "border-top-color"), BORDER, "menu border #D0D0D0")
    equals(style(page, listing, "border-top-width"), "1px", "menu border 1 px")
    radius = px_value(style(page, listing, "border-top-left-radius"))
    truthy(3 <= radius <= 4, f"menu radius 3–4 px ({radius})")
    truthy(style(page, listing, "box-shadow") not in (None, "none"), "a shadow under the menu")
    equals(style(page, '[data-testid="tool-ellipse"] svg', "stroke"), ACCENT, "blue item icon")
    item_size = px_value(style(page, '[data-testid="tool-ellipse"]', "font-size"))
    truthy(13 <= item_size <= 14, f"13–14 px items ({item_size})")
    page.locator('[data-testid="tool-ellipse"]').hover()
    equals(style(page, '[data-testid="tool-ellipse"]', "background-color"), "rgb(232, 245, 255)",
           "light blue hover")
    close_menus(page)
    # The top bar: the same controls.
    approx(page.locator('[data-testid="editor-finalize"]').bounding_box()["height"], 34, 1, "top bar: 34 px")
    equals(style(page, '[data-testid="editor-finalize"]', "background-color"), ACCENT, "primary = the accent")
    equals(style(page, '[data-testid="editor-download"]', "color"), ACCENT, "Download: blue on white")
    equals(style(page, '[data-testid="editor-download"]', "background-color"), WHITE, "white")
    clean(mock)


@test("the_other_pages_keep_their_own_theme")
def _(page, mock: MockApi) -> None:
    page.goto(f"{BASE}/")
    page.wait_for_selector(".button--primary", timeout=30000)
    page.wait_for_timeout(300)
    loaded = page.evaluate(
        """() => [...document.styleSheets].some((sheet) => {
             try { return [...sheet.cssRules].some((r) => r.cssText.includes('--sj-accent')); }
             catch (_) { return false; } })"""
    )
    truthy(loaded, "the Sejda sheet is loaded on this page too (one bundle) ...")
    equals(page.evaluate("() => getComputedStyle(document.body).getPropertyValue('--sj-accent')"), "",
           "... but its tokens do not exist outside the workspace")
    equals(style(page, ".button--primary", "background-color"), APP_ACCENT, "Dashboard's primary button as before")
    equals(page.locator(".sj-topbar, .ws-toolbar").count(), 0, "no editor chrome on the dashboard")
    for path in ("/signatures", "/contracts/new"):
        page.goto(f"{BASE}{path}")
        page.wait_for_timeout(800)
        equals(page.evaluate("() => getComputedStyle(document.body).getPropertyValue('--sj-accent')"), "",
               f"{path}: no Sejda token")
        primary = page.locator(".button--primary")
        if primary.count():
            equals(primary.first.evaluate("(b) => getComputedStyle(b).backgroundColor"), APP_ACCENT,
                   f"{path}: the app's own primary colour")
    equals(mock.page_errors, [], "no uncaught error")


# --------------------------------------------------------------------------
# Placement of the drop-downs
# --------------------------------------------------------------------------

@test("every_drop_down_opens_inside_the_viewport_wherever_the_toolbar_is")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    # Docked on top: below its button, left-aligned with it.
    for menu in MENUS:
        listing = open_menu(page, menu)
        box = inside_viewport(page, listing, f"docked {menu} ▼")
        anchor = page.locator(f'[data-testid="toolgroup-{menu}"]').bounding_box()
        approx(box["x"], anchor["x"], 1.5, f"{menu} ▼ starts under its button")
        truthy(box["y"] >= anchor["y"] + anchor["height"], f"{menu} ▼ hangs below its button")
        close_menus(page)

    # The window shrinks while a list is open: it is placed again, still inside.
    listing = open_menu(page, "shapes")
    page.set_viewport_size({"width": 1024, "height": 520})
    page.wait_for_timeout(500)
    if listing.is_visible():
        box = inside_viewport(page, listing, "shapes ▼ after the window shrank")
        anchor = page.locator('[data-testid="toolgroup-shapes"]').bounding_box()
        if anchor["x"] + box["width"] <= viewport(page)["w"]:
            approx(box["x"], anchor["x"], 1.5, "and still under its button")
        close_menus(page)
    page.set_viewport_size({"width": 1600, "height": 1200})
    page.wait_for_timeout(500)

    # Stood up on the RIGHT edge: the lists open to the left, inside the window.
    stage = page.locator(".workspace__stage").bounding_box()
    drag_grip_to(page, stage["x"] + stage["width"] - 6, stage["y"] + stage["height"] * 0.35)
    equals(toolbar(page).get_attribute("data-edge"), "right", "vertical on the right edge")
    for menu in ("annotate", "shapes", "text"):
        listing = open_menu(page, menu)
        box = inside_viewport(page, listing, f"right edge {menu} ▼")
        anchor = page.locator(f'[data-testid="toolgroup-{menu}"]').bounding_box()
        truthy(box["x"] + box["width"] <= anchor["x"] + 0.5, f"{menu} ▼ opens to the LEFT of the bar")
        close_menus(page)
    shot(page, "menus_right_edge")
    listing = open_menu(page, "annotate")
    shot(page, "menu_annotate_right_edge")
    close_menus(page)

    # ... and on the LEFT edge: to the right.
    drag_grip_to(page, stage["x"] + 4, stage["y"] + stage["height"] * 0.3)
    equals(toolbar(page).get_attribute("data-edge"), "left", "vertical on the left edge")
    listing = open_menu(page, "shapes")
    box = inside_viewport(page, listing, "left edge shapes ▼")
    anchor = page.locator('[data-testid="toolgroup-shapes"]').bounding_box()
    truthy(box["x"] >= anchor["x"] + anchor["width"] - 0.5, "opens to the RIGHT of the bar")
    close_menus(page)

    # Floated near the bottom-right: a tall list turns up and stays in.
    drag_grip_to(page, stage["x"] + stage["width"] * 0.5, stage["y"] + 3)
    equals(toolbar(page).get_attribute("data-mode"), "docked-top", "docked again")
    page.set_viewport_size({"width": 1600, "height": 420})
    page.wait_for_timeout(500)
    listing = open_menu(page, "annotate")
    box = inside_viewport(page, listing, "annotate ▼ in a short window")
    scrolls = listing.evaluate("(m) => getComputedStyle(m).overflowY")
    truthy(scrolls in ("auto", "scroll"), f"a list taller than the room scrolls ({scrolls})")
    close_menus(page)
    clean(mock)


@test("the_page_scrolling_or_zooming_keeps_an_open_list_on_its_button")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    listing = open_menu(page, "text")
    before = listing.bounding_box()
    # Zoom the document with the viewer's own control, then scroll it: the
    # toolbar does not move, so neither does the list.
    page.locator('.pdf-viewer .pdf-stage').evaluate("(s) => { s.scrollTop = 300; s.dispatchEvent(new Event('scroll')); }")
    page.wait_for_timeout(200)
    after = listing.bounding_box() if listing.is_visible() else before
    approx(after["x"], before["x"], 0.5, "x kept while the pages scroll")
    approx(after["y"], before["y"], 0.5, "y kept while the pages scroll")
    # Browser zoom (Ctrl + / −) changes the viewport in CSS px: simulated by a resize.
    page.set_viewport_size({"width": 1280, "height": 900})
    page.wait_for_timeout(400)
    if listing.is_visible():
        inside_viewport(page, listing, "text ▼ after a zoom-like resize")
        anchor = page.locator('[data-testid="toolgroup-text"]').bounding_box()
        approx(listing.bounding_box()["x"], anchor["x"], 1.5, "and still under its button")
    clean(mock)


# --------------------------------------------------------------------------
# Accessibility: names, tooltips, icons, keyboard
# --------------------------------------------------------------------------

@test("every_button_has_a_name_and_a_tooltip_and_every_icon_is_inline_svg")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    for menu in MENUS:  # the list items are in the DOM while closed; check them open too
        open_menu(page, menu)
        close_menus(page)
    report = page.evaluate(
        """() => {
          const scopes = [...document.querySelectorAll('[data-testid="editor-toolbar"], [data-testid="editor-topbar"]')];
          const out = [];
          scopes.forEach((scope) => scope.querySelectorAll('button').forEach((b) => {
            const name = (b.getAttribute('aria-label') || b.textContent || '').trim();
            out.push({ id: b.dataset.testid || b.className, name, title: (b.getAttribute('title') || '').trim() });
          }));
          const svgs = scopes.flatMap((s) => [...s.querySelectorAll('svg')]);
          return { buttons: out,
                   svgs: svgs.length,
                   hiddenSvgs: svgs.filter((s) => s.getAttribute('aria-hidden') === 'true').length,
                   imgs: scopes.flatMap((s) => [...s.querySelectorAll('img')]).length };
        }"""
    )
    truthy(len(report["buttons"]) >= 30, f"the toolbar and the top bar have their buttons ({len(report['buttons'])})")
    for button in report["buttons"]:
        truthy(len(button["name"]) > 1, f"{button['id']}: an accessible name")
        truthy(len(button["title"]) > 1, f"{button['id']}: a tooltip")
    truthy(report["svgs"] >= 25, f"the icons are inline SVG ({report['svgs']})")
    equals(report["hiddenSvgs"], report["svgs"], "every icon is decorative (aria-hidden)")
    equals(report["imgs"], 0, "no icon file")
    clean(mock)


@test("the_keyboard_opens_moves_arms_and_closes_the_new_lists")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    page.keyboard.press("Alt+t")
    page.wait_for_timeout(80)
    equals(page.evaluate("() => document.activeElement.dataset.testid"), "tool-select", "Alt+T: the armed tool")
    stops = []
    for _ in range(len(ROW)):
        page.keyboard.press("ArrowRight")
        stops.append(page.evaluate("() => document.activeElement.dataset.testid"))
    equals(stops, ROW[1:] + ROW[:1], "→ walks the row, one stop per drop-down, and wraps")
    equals(page.evaluate(
        "() => [...document.querySelectorAll('.ws-toolbar__tools .editor__tool')].filter((b) => b.tabIndex === 0).length"),
        1, "the row is ONE tab stop")
    # Text ▼: Alt+↓ opens onto the member it shows (Add text), ↓ Edit text, End Date, Enter arms.
    page.keyboard.press("Home")
    page.keyboard.press("ArrowRight")
    equals(page.evaluate("() => document.activeElement.dataset.testid"), "toolgroup-text", "on Text ▼")
    page.keyboard.press("Alt+ArrowDown")
    page.wait_for_timeout(100)
    truthy(page.locator('[data-testid="toolgroup-text-menu"]').is_visible(), "Alt+↓ opens Text ▼")
    equals(page.evaluate("() => document.activeElement.dataset.testid"), "tool-text", "onto Add text")
    page.keyboard.press("ArrowDown")
    equals(page.evaluate("() => document.activeElement.dataset.testid"), "tool-edit-text", "↓ Edit text")
    page.keyboard.press("End")
    equals(page.evaluate("() => document.activeElement.dataset.testid"), "tool-date", "End: Date")
    page.keyboard.press("Home")
    equals(page.evaluate("() => document.activeElement.dataset.testid"), "tool-text", "Home: Add text")
    page.keyboard.press("ArrowUp")
    equals(page.evaluate("() => document.activeElement.dataset.testid"), "tool-date", "↑ wraps to Date")
    page.keyboard.press("Enter")
    page.wait_for_timeout(100)
    equals(page.locator('[data-testid="tool-date"]').get_attribute("aria-pressed"), "true", "Enter arms Date")
    truthy(not page.locator('[data-testid="toolgroup-text-menu"]').is_visible(), "and closes the list")
    equals(page.locator('[data-testid="toolgroup-text"]').get_attribute("data-tool"), "date",
           "the button now shows Date")
    equals(page.evaluate("() => document.activeElement.dataset.testid"), "toolgroup-text", "focus on the button")
    # F4 opens too; Esc closes and gives the focus back to the button.
    page.keyboard.press("ArrowRight")
    equals(page.evaluate("() => document.activeElement.dataset.testid"), "toolgroup-forms", "→ Forms ▼")
    page.keyboard.press("F4")
    page.wait_for_timeout(100)
    truthy(page.locator('[data-testid="toolgroup-forms-menu"]').is_visible(), "F4 opens Forms ▼")
    equals(page.evaluate("() => document.activeElement.dataset.testid"), "tool-check", "onto Check")
    page.keyboard.press("Escape")
    page.wait_for_timeout(100)
    truthy(not page.locator('[data-testid="toolgroup-forms-menu"]').is_visible(), "Esc closes it")
    equals(page.evaluate("() => document.activeElement.dataset.testid"), "toolgroup-forms", "focus back on the button")
    # The letters arm members of every list directly; the buttons follow.
    for key, menu, tool in (("e", "text", "edit-text"), ("x", "forms", "cross"), ("m", "images", "image"),
                            ("i", "sign", "initials"), ("n", "annotate", "note"), ("a", "shapes", "arrow"),
                            ("w", None, "whiteout")):
        page.locator("body").click(position={"x": 3, "y": 3})
        page.keyboard.press(key)
        page.wait_for_timeout(60)
        equals(page.locator(f'[data-testid="tool-{tool}"]').get_attribute("aria-pressed"), "true", f"'{key}' arms {tool}")
        if menu:
            group = page.locator(f'[data-testid="toolgroup-{menu}"]')
            equals(group.get_attribute("aria-pressed"), "true", f"{menu} ▼ is pressed")
            equals(group.get_attribute("data-tool"), tool, f"{menu} ▼ shows {tool}")
    clean(mock)


# --------------------------------------------------------------------------
# Widths
# --------------------------------------------------------------------------

@test("at_1600_nothing_overflows_whatever_is_armed")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    for armed in ("select", "text", "date", "check", "image", "signature", "whiteout", "highlight", "ink",
                  "note", "rect", "arrow", "edit-text"):
        arm_tool(page, armed)
        page.wait_for_timeout(250)
        equals(toolbar(page).get_attribute("data-overflow"), "0", f"{armed}: nothing under «More ▾»")
        truthy(toolbar(page).bounding_box()["height"] < 64, f"{armed}: one row")
        overflow = page.evaluate(
            "() => ['editor-toolbar', 'editor-topbar'].map((id) => {"
            " const el = document.querySelector(`[data-testid=\"${id}\"]`);"
            " return el.scrollWidth - el.clientWidth; })"
        )
        truthy(all(value <= 0 for value in overflow), f"{armed}: no horizontal overflow ({overflow})")
    page.keyboard.press("v")
    page.wait_for_timeout(200)
    shot(page, "workspace_1600")
    clean(mock)


@test("at_640_the_bottom_bar_stays_and_its_lists_open_above_it")
def _(page, mock: MockApi) -> None:
    page.set_viewport_size({"width": 600, "height": 900})
    page.goto(f"{BASE}/contracts/{mock.contract_id}/edit")
    page.wait_for_selector('[data-testid="editor-toolbar"]', timeout=30000)
    page.wait_for_timeout(800)
    equals(toolbar(page).get_attribute("data-mode"), "mobile", "the bottom bar")
    equals(toolbar(page).evaluate("(el) => getComputedStyle(el).position"), "fixed", "fixed")
    bar = toolbar(page).bounding_box()
    approx(bar["y"] + bar["height"], 900, 1.5, "on the bottom edge")
    heights = page.evaluate(
        "() => [...document.querySelectorAll('.ws-toolbar__tools .editor__tool')].map((b) => b.getBoundingClientRect().height)"
    )
    truthy(all(h >= 44 for h in heights), f"44 px targets ({heights})")
    listing = open_menu(page, "annotate")
    box = inside_viewport(page, listing, "annotate ▼ at 600 px")
    truthy(box["y"] + box["height"] <= bar["y"] + 0.5, f"above the bar ({box}, bar {bar})")
    shot(page, "workspace_640_menu")
    page.locator('[data-testid="tool-underline"]').click()
    equals(page.locator('[data-testid="tool-underline"]').get_attribute("aria-pressed"), "true", "a pick arms it")
    truthy(not listing.is_visible(), "and closes the list")
    # The top bar fits the narrow column.
    topbar = page.locator('[data-testid="editor-topbar"]')
    overflow = topbar.evaluate("(el) => el.scrollWidth - el.clientWidth")
    truthy(overflow <= 0, f"the top bar does not overflow ({overflow})")
    truthy(page.locator('[data-testid="editor-finalize"]').is_visible(), "Generate final contract is reachable")
    shot(page, "workspace_640")
    clean(mock)


# --------------------------------------------------------------------------
# The top bar
# --------------------------------------------------------------------------

@test("the_top_bar_names_the_contract_and_holds_the_finalize_button")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock, panel="sign")
    topbar = page.locator('[data-testid="editor-topbar"]')
    equals(topbar.count(), 1, "one top bar")
    equals(page.locator('[data-testid="editor-topbar-title"]').inner_text(), "ZZ UI Baseline", "the contract's name")
    finalize = topbar.locator('[data-testid="editor-finalize"]')
    equals(finalize.count(), 1, "«Generate final contract» is in the top bar")
    equals(page.locator('[data-testid="editor-finalize"]').count(), 1, "and only there")
    equals(page.get_by_role("button", name="Generate final contract").count(), 1, "ONE button by that name")
    equals(page.locator('[data-testid="finalize-bar"] button[data-testid="editor-finalize"]').count(), 0,
           "the panel's bar keeps the readiness line only")
    truthy(page.locator('[data-testid="finalize-bar"]').is_visible(), "which is still there")
    # Top bar above the toolbar, above the pages.
    bar = topbar.bounding_box()
    tools = toolbar(page).bounding_box()
    truthy(bar["y"] + bar["height"] <= tools["y"] + 0.5, "the top bar sits above the toolbar")
    # No edit-session controls yet (B1 / F1b): not shown, not even disabled.
    equals(page.locator('[data-testid="editor-session"]').count(), 0, "no session slot drawn")
    for name in ("Edit PDF", "Save changes", "Cancel editing"):
        equals(page.get_by_role("button", name=name).count(), 0, f"no «{name}» button yet")
    equals(page.locator('[data-testid="editor-save-status"]').count(), 0, "no save status yet")
    # Finalize still sends the one body and goes to the final page (same gate as before).
    apply_dates_to_all(page)
    truthy(finalize.is_enabled(), "ready: on")
    finalize.click()
    page.wait_for_url(f"**/contracts/{mock.contract_id}/final", timeout=15000)
    body = mock.bodies.get("finalize")
    truthy(body is not None, "finalize was sent from the top bar")
    truthy("dates" in body and "layout" in body, f"with the dates and the layout ({sorted(body)})")
    clean(mock)


@test("finalize_in_the_top_bar_keeps_the_signing_gate")
def _(page, mock: MockApi) -> None:
    mock.review_status = "pending"
    mock.status = "analyzed"
    open_doc(page, mock, panel="sign")
    finalize = page.locator('[data-testid="editor-finalize"]')
    truthy(finalize.is_disabled(), "unapproved: off")
    contains(finalize.get_attribute("title") or "", "Contract not approved yet", "the tooltip says why")
    contains(page.locator('[data-testid="finalize-blocked-reason"]').inner_text(), "Contract not approved yet",
             "and so does the panel's bar")
    clean(mock)


@test("download_pdf_fetches_the_uploaded_pdf_until_there_is_a_final_one")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    button = page.locator('[data-testid="editor-download"]')
    equals(button.get_attribute("data-source"), "original", "no final yet: the uploaded PDF")
    contains(button.get_attribute("title") or "", "annotations are not included", "and says what it is")
    files_before = sum(1 for call in mock.calls if call.endswith("/file"))
    with page.expect_download(timeout=15000) as info:
        button.click()
    download = info.value
    truthy(download.suggested_filename.endswith(".pdf"), f"a .pdf ({download.suggested_filename})")
    with open(download.path(), "rb") as handle:
        head = handle.read(5)
    equals(head, b"%PDF-", "the bytes are a PDF")
    equals(sum(1 for call in mock.calls if call.endswith("/file")), files_before + 1,
           "one authorised GET of the uploaded file")
    shot_of(page.locator('[data-testid="editor-topbar"]'), "topbar")
    clean(mock)


@test("download_pdf_fetches_the_signed_final_pdf_once_it_exists")
def _(page, mock: MockApi) -> None:
    import json

    final_pdf = b"%PDF-1.4\n% ZZ synthetic final\n%%EOF\n"
    seen: list[str] = []

    def contract(route) -> None:
        payload = mock.contract_payload()
        payload["final_available"] = True
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    def final(route) -> None:
        seen.append(route.request.headers.get("authorization", ""))
        route.fulfill(status=200, content_type="application/pdf", body=final_pdf)

    # Registered after MockApi, so these win for their two paths.
    page.route(f"**/api/contracts/{mock.contract_id}", contract)
    page.route(f"**/api/download/{mock.contract_id}/final", final)
    open_doc(page, mock)
    button = page.locator('[data-testid="editor-download"]')
    page.wait_for_function(
        "() => document.querySelector('[data-testid=\"editor-download\"]').dataset.source === 'final'",
        timeout=10000,
    )
    contains(button.get_attribute("title") or "", "signed final PDF", "it says it is the signed one")
    with page.expect_download(timeout=15000) as info:
        button.click()
    download = info.value
    contains(download.suggested_filename, "signed", f"named as the signed PDF ({download.suggested_filename})")
    with open(download.path(), "rb") as handle:
        equals(handle.read(), final_pdf, "the final PDF's bytes")
    equals(len(seen), 1, "one GET of the final PDF")
    truthy(seen[0].startswith("Bearer "), "with the bearer token")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
