"""
F2: Annotate ▾ / Forms ▾ / Whiteout ▾ additions (EDITOR-V2-PLAN §7.2).

  * Freehand highlight (F): a translucent marker (`ink2`, `freehand_highlight`,
    opacity left to the server's 0.35), yellow, ≥ 8 pt;
  * Show / hide annotations: ON SCREEN ONLY -- the objects are still there,
    the export dialog still counts them;
  * Filled square (Q): a v7 `square_mark` centred on the click;
  * Whiteout ▾ Redact: a REAL redaction (`redact` op, black), worded apart from
    the whiteout («permanently removes» vs «hides»).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _f2 import clean, click_pt, drag_pt, install, open_doc, select_tool, shot, wait_markup, wait_ops  # noqa: E402
from _harness import MockApi, Registry, approx, cli_main, contains, equals, truthy  # noqa: E402

test = Registry("annotate v2 (F2)")


def kinds(elements, kind):
    return [element for element in elements if element.get("kind") == kind]


@test("freehand_highlight_is_a_translucent_ink2_marker")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    page.locator("body").click(position={"x": 3, "y": 3})
    page.keyboard.press("f")
    equals(page.locator('[data-testid="tool-highlight-ink"]').get_attribute("aria-pressed"), "true", "F arms it")
    drag_pt(page, 80, 200, 260, 205, steps=14)
    ink = kinds(wait_markup(page, doc, lambda els: len(kinds(els, "ink2")) == 1), "ink2")[0]
    equals(ink["freehand_highlight"], True, "a freehand highlight")
    truthy("opacity" not in ink, "the server's own 0.35")
    equals(ink["color"], "yellow", "yellow by default")
    truthy(ink["stroke_pt"] >= 8, f"a marker, not a pen ({ink['stroke_pt']})")
    drawn = page.locator(f'[data-testid="ann-ink-{ink["id"]}"]')
    approx(float(drawn.get_attribute("data-opacity")), 0.35, 0.001, "drawn at 35 %")
    clean(mock)


@test("hide_annotations_is_on_screen_only_and_the_export_still_counts_them")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    select_tool(page, "square")
    click_pt(page, 200, 200)
    click_pt(page, 240, 200)
    wait_markup(page, doc, lambda els: len(els) == 2)
    page.locator("body").click(position={"x": 3, "y": 3})
    page.locator('[data-testid="toolgroup-annotate-toggle"]').click()
    toggle = page.locator('[data-testid="menu-toggle-annotations"]')
    contains(toggle.get_attribute("title") or "", "On screen only", "says it is a view setting")
    toggle.click()
    equals(page.locator('[data-testid="workspace"]').get_attribute("data-markup-hidden"), "true", "hidden")
    layer = page.locator('[data-testid="ann-layer-1"]')
    equals(layer.evaluate("(el) => getComputedStyle(el).visibility"), "hidden", "not drawn on screen")
    equals(page.locator('[data-testid="section-objects-badge"]').inner_text(), "2", "still two objects")
    page.locator('[data-testid="editor-download"]').click()
    page.locator('[data-testid="export-dialog"]').wait_for()
    equals(page.locator('[data-testid="export-include-label"]').inner_text(), "Include annotations (2)",
           "the hidden ones are counted")
    page.locator('[data-testid="export-hidden-note"]').wait_for()
    page.locator('[data-testid="export-cancel"]').click()
    page.locator('[data-testid="toolgroup-annotate-toggle"]').click()
    page.locator('[data-testid="menu-toggle-annotations"]').click()
    equals(layer.evaluate("(el) => getComputedStyle(el).visibility"), "visible", "shown again")
    equals(len(doc.markup), 2, "nothing was removed from the server")
    clean(mock)


@test("filled_square_is_a_square_mark_centred_on_the_click")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    page.locator("body").click(position={"x": 3, "y": 3})
    page.keyboard.press("q")
    page.locator('[data-testid="ann-color"]').click()
    page.locator('[data-testid="ann-color-green"]').click()
    click_pt(page, 300, 300)
    square = kinds(wait_markup(page, doc, lambda els: len(kinds(els, "square_mark")) == 1), "square_mark")[0]
    approx(square["x_pt"], 300, 0.6, "centred x")
    approx(square["y_pt"], 300, 0.6, "centred y")
    equals(square["color"], "green", "green")
    truthy(4 <= square["size_pt"] <= 72, "a size the server takes")
    shot(page, "annotate_square")
    clean(mock)


@test("redact_permanently_removes_and_says_so_apart_from_whiteout")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    page.locator("body").click(position={"x": 3, "y": 3})
    page.locator('[data-testid="toolgroup-whiteout-toggle"]').click()
    listing = page.locator('[data-testid="toolgroup-whiteout-menu"]')
    listing.wait_for(state="visible")
    contains(page.locator('[data-testid="tool-redact"]').get_attribute("aria-label") or "", "permanently removes",
             "Redact: permanently removes")
    contains(page.locator('[data-testid="tool-whiteout"]').get_attribute("aria-label") or "", "only hides",
             "Whiteout: only hides")
    page.locator('[data-testid="tool-redact"]').click()
    drag_pt(page, 70, 135, 260, 158)
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=8000)
    ops = wait_ops(page, doc, lambda list_: any(op["kind"] == "redact" for op in list_))
    op = [op for op in ops if op["kind"] == "redact"][0]
    equals((op["page_uid"], op["fill"], len(op["rects"])), (doc.uids[0], "black", 1), "a black redaction on page 1")
    approx(op["rects"][0]["w_pt"], 190, 0.6, "its width")
    box = page.locator(f'[data-testid="redact-{op["id"]}"]')
    box.wait_for()
    equals(box.evaluate("(el) => getComputedStyle(el).backgroundColor"), "rgb(0, 0, 0)", "black until saved")
    equals([element for element in doc.markup if element.get("kind") == "whiteout"], [], "not a whiteout")
    page.locator("body").click(position={"x": 3, "y": 3})
    page.keyboard.press("Control+z")
    wait_ops(page, doc, lambda list_: not any(item["kind"] == "redact" for item in list_))
    clean(mock)


@test("with_the_editor_at_1600_nothing_overflows_and_every_button_is_named")
def _(page, mock: MockApi) -> None:
    install(mock)
    open_doc(page, mock)
    for armed in ("select", "textbox", "links", "square", "stamp", "image-remove", "redact", "highlight-ink",
                  "rect", "arrow", "ink", "edit-text", "date", "signature"):
        select_tool(page, armed)
        page.wait_for_timeout(200)
        toolbar = page.locator('[data-testid="editor-toolbar"]')
        equals(toolbar.get_attribute("data-overflow"), "0", f"{armed}: nothing under «More ▾»")
        truthy(toolbar.bounding_box()["height"] < 64, f"{armed}: one row")
        overflow = toolbar.evaluate("(el) => el.scrollWidth - el.clientWidth")
        truthy(overflow <= 0, f"{armed}: no horizontal overflow ({overflow})")
    for menu in ("text", "forms", "images", "sign", "whiteout", "annotate", "shapes"):
        page.locator("body").click(position={"x": 3, "y": 3})
        page.locator(f'[data-testid="toolgroup-{menu}-toggle"]').click()
        listing = page.locator(f'[data-testid="toolgroup-{menu}-menu"]')
        listing.wait_for(state="visible")
        box = listing.bounding_box()
        view = page.evaluate("() => ({w: document.documentElement.clientWidth, h: window.innerHeight})")
        truthy(box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= view["w"] + 0.5
               and box["y"] + box["height"] <= view["h"] + 0.5, f"{menu} ▼ inside the viewport ({box})")
        page.keyboard.press("Escape")
    report = page.evaluate(
        """() => [...document.querySelectorAll('[data-testid="editor-toolbar"] button, [data-testid="editor-topbar"] button')]
             .map((b) => ({ id: b.dataset.testid || b.className,
                            name: (b.getAttribute('aria-label') || b.textContent || '').trim(),
                            title: (b.getAttribute('title') || '').trim() }))""")
    for button in report:
        truthy(len(button["name"]) > 1, f"{button['id']}: an accessible name")
        truthy(len(button["title"]) > 1, f"{button['id']}: a tooltip")
    shot(page, "toolbar_f2_1600")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
