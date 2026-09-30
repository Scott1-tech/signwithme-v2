"""
W3: marking up the document's own text -- Highlight (H), Underline (U),
Strikeout (C) -- by SELECTING it, «Highlight all matches» from the search, the
annotation ceiling of 500, and the slot date's font (six families + B / I).

What a user does (Sejda-style): arm Highlight, drag across words of the page
(or double-click one); the selection turns into a mark and the browser's own
selection goes. Each line of the selection is one box, in PDF points.

These checks pin, in PDF points (±0.5 pt against the synthetic document's
known metrics):

  * the text layer is in SELECT mode (spans take the pointer, no line buttons)
    only while a markup tool is armed, never on a turned page;
  * a word's box: its x / width from the PDF font's own advance widths, its
    y / height = the line's ink box (the same box edit-text reads);
  * a two-line drag = two boxes (one per line); a selection across a page
    break = one annotation per page, ONE undo step;
  * the finalize body: `{id, kind, page, rects: [{x_pt, y_pt, w_pt, h_pt}],
    color}` -- colour always named (default yellow / red), no `x_pt` on top;
  * the preview is the print: highlight alpha 0.35 in the palette hex,
    underline / strikeout bar `max(0.75, h × 0.08)` at the bottom / middle;
  * select, drag (every box moves together), Delete, Objects list, undo/redo;
  * «Highlight all matches»: one step, the search's own boxes, no second copy,
    and ALL-or-nothing at the 500 ceiling (which the counter now shows);
  * the slot date: six families and B / I in «Position & appearance»; `false`
    is never sent, so an untouched layout body is the old one byte for byte.

Everything is synthetic (the document is built byte by byte in
`test_edit_text.py`) and every `/api/*` call is answered by `MockApi`.
Screenshots: `c:\\PTG\\.playwright-mcp\\swm_markup\\`.
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
    cli_main,
    contains,
    equals,
    goto_blank,
    open_section,
    stored_layout,
    truthy,
)
from test_edit_text import (  # noqa: E402
    PAGE_H,
    PAGE_W,
    STORAGE_KEY,
    install,
    line,
    text_width,
    wait_layer,
)

test = Registry("markup")

#: `lib/annotations.ts` (and the backend's `pdf_builder.py`).
MAX_ANNOTATIONS = 500
HIGHLIGHT_ALPHA = 0.35
PALETTE = {"yellow": (250, 204, 21), "red": (220, 38, 38), "blue": (29, 78, 216), "green": (22, 163, 74)}
OLD_LAYOUT_KEYS = {
    "signature_offset_pt", "date_offset_pt", "slot_offsets_pt", "signature_scale",
    "date_font", "date_font_size",
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def shot(page, name: str) -> None:
    folder = os.path.join("c:\\", "PTG", ".playwright-mcp", "swm_markup")
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, f"{name}.png"))


def clean(mock: MockApi) -> None:
    equals(mock.page_errors, [], "no uncaught error")
    equals(mock.unmocked, [], "no unmocked API call")


def open_doc(page, mock: MockApi, extra_pages: int = 0, seed: str | None = None,
             panel: str = "edit") -> None:
    install(mock, extra_pages)
    if seed is not None:
        goto_blank(page, mock)
        page.evaluate("([k, v]) => localStorage.setItem(k, v)", [STORAGE_KEY, seed])
    page.goto(f"{BASE}/contracts/{mock.contract_id}/{panel}")
    page.wait_for_selector('[data-testid="editor-toolbar"]', timeout=30000)
    page.wait_for_function(
        "() => { const p = document.querySelector('.pdf-page');"
        " return !!p && p.getBoundingClientRect().width > 300; }",
        timeout=30000,
    )
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_timeout(350)


def arm(page, tool: str) -> None:
    # W4: highlight / underline / strikeout live in the «Markup ▾» drop-down;
    # `arm_tool` opens it first (a plain tool is still one click).
    arm_tool(page, tool)
    page.wait_for_timeout(100)


def scale_of(page, page_number: int = 1) -> float:
    return page.evaluate(
        "(n) => document.querySelector(`.pdf-page[data-page-number=\"${n}\"]`)"
        ".getBoundingClientRect().width",
        page_number,
    ) / PAGE_W


def stored(page) -> list:
    raw = page.evaluate("(key) => localStorage.getItem(key)", STORAGE_KEY)
    return json.loads(raw) if raw else []


def wait_stored(page, count: int) -> None:
    page.wait_for_function(
        "([key, n]) => { const raw = localStorage.getItem(key);"
        " return (raw ? JSON.parse(raw).length : 0) === n; }",
        arg=[STORAGE_KEY, count],
        timeout=10000,
    )


def depth(page, which: str) -> int:
    return int(page.locator(f'[data-testid="{which}-depth"]').inner_text().strip())


SELECT_JS = """
([fromPage, fromText, fromOffset, toPage, toText, toOffset]) => {
  const node = (pageNumber, text) => {
    const layer = document.querySelector(`[data-testid="pdf-text-layer-${pageNumber}"] .textLayer`);
    if (!layer) return null;
    const span = [...layer.querySelectorAll('span')].find((s) => s.textContent === text);
    return span ? span.firstChild : null;
  };
  const start = node(fromPage, fromText);
  const end = node(toPage, toText);
  if (!start || !end) return false;
  const range = document.createRange();
  range.setStart(start, fromOffset);
  range.setEnd(end, toOffset === null ? end.length : toOffset);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  document.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
  return true;
}
"""


def select_text(page, span_text: str, start: int = 0, end: int | None = None,
                page_number: int = 1, to_text: str | None = None, to_page: int | None = None) -> None:
    """Select characters [start, end) of one span -- or from one span to another --
    exactly as a drag would leave them, then release the pointer."""
    ok = page.evaluate(
        SELECT_JS,
        [page_number, span_text, start, to_page or page_number, to_text or span_text, end],
    )
    truthy(ok, f"the span {span_text!r} / {to_text!r} is in the text layer")


def selection_empty(page) -> bool:
    return page.evaluate("() => window.getSelection().isCollapsed")


def markups(items: list) -> list:
    return [item for item in items if item.get("kind") in ("highlight", "underline", "strikeout")]


def finalize_body(page, mock: MockApi) -> dict:
    apply_dates_to_all(page)
    page.locator('[data-testid="editor-finalize"]').click()
    page.wait_for_function("() => window.location.pathname.endsWith('/final')", timeout=20000)
    body = mock.bodies.get("finalize")
    truthy(body is not None, "finalize was sent")
    return body


def pick_color(page, name: str) -> None:
    page.locator('[data-testid="ann-color"]').click()
    page.wait_for_selector('[data-testid="ann-color-palette"]', timeout=5000)
    page.locator(f'[data-testid="ann-color-{name}"]').click()
    page.wait_for_timeout(120)


def ink_line(page, text: str) -> dict:
    """The line's ink box as the edit-text layer reads it (the ONE geometry source)."""
    arm(page, "edit-text")
    wait_layer(page)
    found = line(page, text)
    return found


def seed_texts(count: int) -> str:
    return json.dumps([
        {"id": f"s{i}", "kind": "text", "page": 1, "x_pt": 72, "y_pt": 420 + (i % 20) * 12,
         "text": f"n{i}", "font": "Helvetica", "size_pt": 8}
        for i in range(count)
    ])


# --------------------------------------------------------------------------
# The tools and the layer
# --------------------------------------------------------------------------

@test("the_three_tools_are_on_the_toolbar_and_arm_a_selectable_layer")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    for name, key in (("highlight", "H"), ("underline", "U"), ("strikeout", "C")):
        tool = page.locator(f'[data-testid="tool-{name}"]')
        equals(tool.count(), 1, f"tool-{name} is on the toolbar")
        equals(tool.get_attribute("aria-keyshortcuts"), key, f"{name} announces {key}")
    equals(page.locator(".pdf-text-layer").count(), 0, "no text layer with Select")
    page.keyboard.press("h")
    page.wait_for_timeout(120)
    equals(page.locator('[data-testid="tool-highlight"]').get_attribute("aria-pressed"), "true",
           "H arms Highlight")
    wait_layer(page)
    classes = page.locator('[data-testid="workspace"]').get_attribute("class") or ""
    contains(classes, "workspace--text-select", "the workspace says text is selectable")
    contains(classes, "workspace--placing", "the slots step aside")
    equals(page.locator('[data-testid="pdf-text-layer-1"]').get_attribute("data-mode"), "select",
           "the layer is in select mode")
    equals(page.locator(".pdf-text-line").count(), 0, "no edit-text line buttons in the way")
    events = page.evaluate(
        "() => getComputedStyle(document.querySelector('[data-testid=\"pdf-text-layer-1\"] .textLayer span'))"
        ".pointerEvents"
    )
    equals(events, "auto", "the spans take the pointer")
    # The PropertiesBar: only a colour, the tool's own default.
    equals(page.locator('[data-testid="ann-color"]').get_attribute("data-color"), "yellow",
           "a highlight starts yellow")
    truthy(page.locator('[data-testid="ann-font"]').is_disabled(), "no font for a highlight")
    truthy(page.locator('[data-testid="ann-size"]').is_disabled(), "no size for a highlight")
    page.keyboard.press("u")
    page.wait_for_timeout(120)
    equals(page.locator('[data-testid="ann-color"]').get_attribute("data-color"), "red",
           "an underline starts red")
    page.keyboard.press("c")
    page.wait_for_timeout(120)
    equals(page.locator('[data-testid="tool-strikeout"]').get_attribute("aria-pressed"), "true",
           "C arms Strikeout")
    page.keyboard.press("s")
    page.wait_for_timeout(120)
    equals(page.locator('[data-testid="tool-signature"]').get_attribute("aria-pressed"), "true",
           "S is still Signature")
    equals(page.locator(".pdf-text-layer").count(), 0, "the layer goes with the tool")
    shot(page, "tools")
    clean(mock)


# --------------------------------------------------------------------------
# Selection -> boxes
# --------------------------------------------------------------------------

@test("a_selected_word_is_one_box_at_its_known_coordinates")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    ink = ink_line(page, "Payment terms: net thirty days")
    arm(page, "highlight")
    wait_layer(page)
    # "terms" inside the pdf.js item "Payment terms:" (Times-Roman 12 at x = 72).
    select_text(page, "Payment terms:", 8, 13)
    wait_stored(page, 1)
    item = stored(page)[0]
    equals(item["kind"], "highlight", "a highlight")
    equals(item["page"], 1, "on page 1")
    equals(item["color"], "yellow", "yellow by default")
    equals(len(item["rects"]), 1, "one box")
    box = item["rects"][0]
    approx(box["x_pt"], 72 + text_width("Payment ", "Times-Roman", 12), 0.5, "x of 'terms' (pt)")
    approx(box["w_pt"], text_width("terms", "Times-Roman", 12), 0.5, "width of 'terms' (pt)")
    approx(box["y_pt"], ink["y"], 0.5, "top = the line's ink top (pt)")
    approx(box["h_pt"], ink["h"], 0.5, "height = the line's ink box (pt)")
    truthy(box["y_pt"] <= 150 - 0.66 * 12 and box["y_pt"] + box["h_pt"] >= 150 + 0.1 * 12,
           f"covers caps and descenders of the baseline at 150 ({box})")
    truthy(selection_empty(page), "the browser's selection is cleared")
    # A whole item, and leading / trailing blanks are not marked.
    select_text(page, "TOTAL DUE")
    wait_stored(page, 2)
    whole = stored(page)[1]["rects"][0]
    approx(whole["x_pt"], 72, 0.5, "starts at the line (pt)")
    approx(whole["w_pt"], text_width("TOTAL DUE", "Helvetica-Bold", 16), 0.5, "the whole run (pt)")
    shot(page, "word")
    clean(mock)


@test("a_mouse_drag_over_two_lines_is_one_box_per_line")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm(page, "underline")
    wait_layer(page)
    scale = scale_of(page)
    origin = page.locator('.pdf-page[data-page-number="1"]').bounding_box()
    # From inside "Carrier" (line at baseline 100) to the middle of "Payment terms:" (baseline 150).
    x0 = origin["x"] + (72 + 3) * scale
    y0 = origin["y"] + (100 - 4) * scale
    x1 = origin["x"] + (72 + text_width("Payment ter", "Times-Roman", 12)) * scale
    y1 = origin["y"] + (150 - 4) * scale
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move((x0 + x1) / 2, (y0 + y1) / 2, steps=6)
    page.mouse.move(x1, y1, steps=6)
    page.mouse.up()
    wait_stored(page, 1)
    item = stored(page)[0]
    equals(item["kind"], "underline", "an underline")
    equals(item["color"], "red", "red by default")
    rects = sorted(item["rects"], key=lambda r: r["y_pt"])
    equals(len(rects), 2, f"two lines, two boxes ({rects})")
    first, second = rects
    truthy(first["y_pt"] < 100 < first["y_pt"] + first["h_pt"], f"box 1 on line 1 ({first})")
    truthy(second["y_pt"] < 150 < second["y_pt"] + second["h_pt"], f"box 2 on line 2 ({second})")
    approx(first["x_pt"] + first["w_pt"],
           72 + text_width("Carrier agrees to deliver the load", "Helvetica", 14), 1.0,
           "line 1 runs to its end: its words were merged into one box (pt)")
    approx(second["x_pt"], 72, 0.5, "line 2 starts at its start (pt)")
    truthy(second["x_pt"] + second["w_pt"] <= 72 + text_width("Payment terms", "Times-Roman", 12) + 0.5,
           f"line 2 stops where the drag did ({second})")
    truthy(selection_empty(page), "the selection is gone")
    equals(depth(page, "undo"), 1, "one step")
    shot(page, "two_lines")
    clean(mock)


@test("a_double_click_on_a_word_marks_that_word")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm(page, "highlight")
    wait_layer(page)
    scale = scale_of(page)
    origin = page.locator('.pdf-page[data-page-number="1"]').bounding_box()
    # "DUE" in "TOTAL DUE" (Helvetica-Bold 16, x = 72, baseline 200).
    x = 72 + text_width("TOTAL D", "Helvetica-Bold", 16)
    page.mouse.dblclick(origin["x"] + x * scale, origin["y"] + (200 - 5) * scale)
    wait_stored(page, 1)
    rect = stored(page)[0]["rects"][0]
    approx(rect["x_pt"], 72 + text_width("TOTAL ", "Helvetica-Bold", 16), 0.5, "x of 'DUE' (pt)")
    approx(rect["w_pt"], text_width("DUE", "Helvetica-Bold", 16), 0.5, "width of 'DUE', no blank (pt)")
    truthy(selection_empty(page), "the selection is gone")
    clean(mock)


@test("sixty_selected_lines_are_split_into_marks_of_at_most_fifty_boxes")
def _(page, mock: MockApi) -> None:
    from test_edit_text import _blocks, _pdf  # the same byte-by-byte document builder

    blocks = []
    for row in range(60):
        blocks.extend(_blocks("Helvetica", 8, 72, 40 + row * 12, f"Row {row + 1} text", None))
    install(mock)
    mock.set_pdf(_pdf([" ".join(blocks)]))
    page.goto(f"{BASE}/contracts/{mock.contract_id}/edit")
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_timeout(350)
    arm(page, "highlight")
    wait_layer(page)
    select_text(page, "Row 1 text", 0, None, to_text="Row 60 text")
    wait_stored(page, 2)
    items = stored(page)
    equals(sorted(len(item["rects"]) for item in items), [10, 50], "50 + 10 boxes, one per line")
    equals(depth(page, "undo"), 1, "still ONE undo step")
    clean(mock)


@test("two_columns_on_one_line_stay_two_boxes")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm(page, "highlight")
    wait_layer(page)
    select_text(page, "Left column", 0, None, to_text="Right column")
    wait_stored(page, 1)
    rects = stored(page)[0]["rects"]
    equals(len(rects), 2, f"the gutter is not highlighted ({rects})")
    approx(rects[1]["x_pt"], 350, 0.5, "the second column's box starts at 350 (pt)")
    clean(mock)


@test("a_selection_across_a_page_break_is_one_mark_per_page_and_one_step")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock, extra_pages=1)
    arm(page, "strikeout")
    wait_layer(page, 1)
    page.evaluate(
        "() => document.querySelector('.pdf-page[data-page-number=\"2\"]').scrollIntoView({block: 'start'})"
    )
    wait_layer(page, 2)
    select_text(page, "Name", 0, 2, page_number=1, to_text="Page", to_page=2)
    wait_stored(page, 2)
    items = stored(page)
    equals(sorted(item["page"] for item in items), [1, 2], "one strikeout on each page")
    truthy(all(item["kind"] == "strikeout" for item in items), "both strikeouts")
    equals(depth(page, "undo"), 1, "ONE undo step for the whole selection")
    page.keyboard.press("Control+z")
    wait_stored(page, 0)
    equals(depth(page, "redo"), 1, "one redo step")
    page.keyboard.press("Control+y")
    wait_stored(page, 2)
    clean(mock)


# --------------------------------------------------------------------------
# What is sent and what is drawn
# --------------------------------------------------------------------------

@test("each_tool_sends_rects_and_color_in_the_finalize_body")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm(page, "highlight")
    wait_layer(page)
    select_text(page, "TOTAL DUE")
    wait_stored(page, 1)
    arm(page, "underline")
    pick_color(page, "blue")
    select_text(page, "REF-2026-0042")
    wait_stored(page, 2)
    arm(page, "strikeout")
    select_text(page, "Signed in good faith")
    wait_stored(page, 3)
    arm(page, "select")
    body = finalize_body(page, mock)
    sent = markups(body.get("annotations") or [])
    equals([item["kind"] for item in sent], ["highlight", "underline", "strikeout"], "all three, in order")
    equals([item["color"] for item in sent], ["yellow", "blue", "red"],
           "the colour is ALWAYS named (default yellow / red, picked blue)")
    for item in sent:
        equals(sorted(item.keys()), ["color", "id", "kind", "page", "rects"],
               f"exactly the v5 markup shape, no x_pt / y_pt on top ({item})")
        truthy(1 <= len(item["rects"]) <= 50, "1…50 boxes")
        for rect in item["rects"]:
            equals(sorted(rect.keys()), ["h_pt", "w_pt", "x_pt", "y_pt"], "a box is x/y/w/h in pt")
            truthy(rect["w_pt"] >= 1 and rect["h_pt"] >= 1, f"≥ 1 pt ({rect})")
            truthy(0 <= rect["x_pt"] and rect["x_pt"] + rect["w_pt"] <= PAGE_W, "inside the page (x)")
            truthy(0 <= rect["y_pt"] and rect["y_pt"] + rect["h_pt"] <= PAGE_H, "inside the page (y)")
    ref = sent[1]["rects"][0]
    approx(ref["x_pt"], 72, 0.5, "REF starts at 72 (pt)")
    approx(ref["w_pt"], text_width("REF-2026-0042", "Courier", 10), 0.5, "Courier width (pt)")
    clean(mock)


@test("the_preview_is_what_the_pdf_prints")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm(page, "highlight")
    wait_layer(page)
    select_text(page, "TOTAL DUE")
    arm(page, "underline")
    select_text(page, "REF-2026-0042")
    arm(page, "strikeout")
    select_text(page, "Signed in good faith")
    wait_stored(page, 3)
    arm(page, "select")
    scale = scale_of(page)
    drawn = page.evaluate(
        """() => [...document.querySelectorAll('.swm-ann__markup')].map((g) => ({
          kind: g.dataset.kind, color: g.dataset.color,
          fill: getComputedStyle(g).fill, opacity: Number(getComputedStyle(g).fillOpacity),
          rects: JSON.parse(g.dataset.annRects),
          ink: [...g.querySelectorAll('rect')].map((r) => ({
            x: Number(r.getAttribute('x')), y: Number(r.getAttribute('y')),
            w: Number(r.getAttribute('width')), h: Number(r.getAttribute('height')) })),
        }))"""
    )
    by_kind = {entry["kind"]: entry for entry in drawn}
    equals(sorted(by_kind), ["highlight", "strikeout", "underline"], "three markups drawn")

    high = by_kind["highlight"]
    equals(high["fill"], "rgb(250, 204, 21)", "highlight in the palette's yellow #FACC15")
    approx(high["opacity"], HIGHLIGHT_ALPHA, 0.001, "at alpha 0.35 (normal blend)")
    x, y, w, h = high["rects"][0]
    approx(high["ink"][0]["x"], x * scale, 0.05, "fills the box: x (px)")
    approx(high["ink"][0]["y"], y * scale, 0.05, "fills the box: y (px)")
    approx(high["ink"][0]["h"], h * scale, 0.05, "fills the box: h (px)")

    under = by_kind["underline"]
    equals(under["fill"], "rgb(220, 38, 38)", "underline in red #DC2626")
    approx(under["opacity"], 1.0, 0.001, "opaque")
    x, y, w, h = under["rects"][0]
    thickness = max(0.75, h * 0.08)
    approx(under["ink"][0]["h"], thickness * scale, 0.05, "bar = max(0.75, h × 0.08) (px)")
    approx(under["ink"][0]["y"] + under["ink"][0]["h"], (y + h) * scale, 0.05,
           "its lower edge is the box's lower edge (px)")

    strike = by_kind["strikeout"]
    x, y, w, h = strike["rects"][0]
    thickness = max(0.75, h * 0.08)
    approx(strike["ink"][0]["y"], (y + (h - thickness) / 2) * scale, 0.05, "centred on the box (px)")
    approx(strike["ink"][0]["w"], w * scale, 0.05, "as wide as the box (px)")

    # Drawing order: whiteouts < markups < runs / marks (plan §2.4 b).
    order = page.evaluate(
        "() => [...document.querySelector('[data-testid=\"ann-layer-1\"]').children]"
        ".map((el) => el.getAttribute('class').split(' ')[0])"
    )
    truthy(all(entry == "swm-ann__markup" for entry in order), f"only markups here ({order})")
    shot(page, "preview")
    clean(mock)


# --------------------------------------------------------------------------
# Editing a mark
# --------------------------------------------------------------------------

@test("a_mark_is_selected_moved_whole_recoloured_and_deleted")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm(page, "highlight")
    wait_layer(page)
    select_text(page, "Carrier", 0, None, to_text="load")
    wait_stored(page, 1)
    before = stored(page)[0]
    arm(page, "select")
    scale = scale_of(page)
    target = page.locator(f'[data-testid="ann-{before["id"]}"]')
    target.click()
    equals(target.get_attribute("data-selected"), "true", "a click selects it")
    equals(page.locator('[data-testid="ann-handle"]').count(), 0, "no size grip on a markup")
    box = target.bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + 20, cy + 30, steps=8)
    page.mouse.move(cx + 40 * scale, cy + 60 * scale, steps=8)
    page.mouse.up()
    page.wait_for_timeout(200)
    after = stored(page)[0]
    dx = after["rects"][0]["x_pt"] - before["rects"][0]["x_pt"]
    dy = after["rects"][0]["y_pt"] - before["rects"][0]["y_pt"]
    approx(dx, 40, 0.6, "moved right 40 pt")
    approx(dy, 60, 0.6, "moved down 60 pt")
    for old, new in zip(before["rects"], after["rects"]):
        approx(new["x_pt"] - old["x_pt"], dx, 0.011, "every box moved together (x)")
        approx(new["y_pt"] - old["y_pt"], dy, 0.011, "every box moved together (y)")
        approx(new["w_pt"], old["w_pt"], 0.001, "same width")
    equals(depth(page, "undo"), 2, "place + move")
    pick_color(page, "green")
    equals(stored(page)[0]["color"], "green", "recoloured")
    equals(depth(page, "undo"), 3, "the colour is one step")
    # Objects list.
    open_section(page, "objects")
    row = page.locator(f'[data-testid="object-ann-{before["id"]}"]')
    equals(row.count(), 1, "one row in Objects")
    contains(row.inner_text(), "Highlight", "named Highlight")
    equals(row.get_attribute("data-kind"), "highlight", "kind on the row")
    page.locator(f'[data-testid="ann-{before["id"]}"]').click()
    page.keyboard.press("Delete")
    wait_stored(page, 0)
    page.keyboard.press("Control+z")
    wait_stored(page, 1)
    equals(stored(page)[0]["color"], "green", "undo puts it back as it was")
    clean(mock)


@test("a_mark_is_kept_inside_its_page_when_dragged_past_the_edge")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm(page, "highlight")
    wait_layer(page)
    select_text(page, "Right column")
    wait_stored(page, 1)
    arm(page, "select")
    target = page.locator(f'[data-testid="ann-{stored(page)[0]["id"]}"]')
    box = target.bounding_box()
    sheet = page.locator('.pdf-page[data-page-number="1"]').bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    # Past the page's right edge (still inside the window).
    page.mouse.move(sheet["x"] + sheet["width"] + 40, cy, steps=12)
    page.mouse.up()
    page.wait_for_timeout(200)
    rect = stored(page)[0]["rects"][0]
    approx(rect["x_pt"] + rect["w_pt"], PAGE_W, 0.51, "stopped at the right edge (pt)")
    # And up past the top with the keyboard (Shift+↑ = 2 pt a press).
    target.click()
    for _ in range(int(rect["y_pt"] / 2) + 5):
        page.keyboard.press("Shift+ArrowUp")
    page.wait_for_timeout(200)
    rect = stored(page)[0]["rects"][0]
    approx(rect["y_pt"], 0, 0.001, "stopped at the top edge (pt)")
    approx(rect["x_pt"] + rect["w_pt"], PAGE_W, 0.51, "still at the right edge (pt)")
    clean(mock)


@test("a_turned_page_offers_nothing_to_mark")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm(page, "highlight")
    wait_layer(page)
    page.locator('[data-testid="pdf-rotate"]').click()
    page.wait_for_timeout(400)
    equals(page.locator('[data-testid="pdf-text-layer-1"]').count(), 0, "no text layer on a turned page")
    box = page.locator('.pdf-page[data-page-number="1"]').bounding_box()
    page.mouse.move(box["x"] + 100, box["y"] + 100)
    page.mouse.down()
    page.mouse.move(box["x"] + 400, box["y"] + 140, steps=8)
    page.mouse.up()
    page.wait_for_timeout(300)
    equals(stored(page), [], "nothing is marked on a turned page")
    for _ in range(3):
        page.locator('[data-testid="pdf-rotate"]').click()
        page.wait_for_timeout(250)
    wait_layer(page)
    select_text(page, "Name")
    wait_stored(page, 1)
    clean(mock)


@test("stored_marks_survive_a_reload_and_rubbish_is_dropped")
def _(page, mock: MockApi) -> None:
    seed = json.dumps([
        {"id": "h1", "kind": "highlight", "page": 1, "rects": [
            {"x_pt": 72, "y_pt": 90, "w_pt": 100, "h_pt": 14}, {"x_pt": 72, "y_pt": 140, "w_pt": 50, "h_pt": 12}]},
        {"id": "u1", "kind": "underline", "page": 1, "color": "purple",
         "rects": [{"x_pt": 72, "y_pt": 230, "w_pt": 60, "h_pt": 10}]},
        {"id": "bad1", "kind": "strikeout", "page": 1, "rects": []},
        {"id": "bad2", "kind": "highlight", "page": 1, "rects": [{"x_pt": 72, "y_pt": 90, "w_pt": 0.2, "h_pt": 14}]},
        {"id": "bad3", "kind": "highlight", "page": 1, "color": "white",
         "rects": [{"x_pt": 72, "y_pt": 300, "w_pt": 40, "h_pt": 12}], "x_pt": 999},
    ])
    open_doc(page, mock, seed=seed)
    items = stored(page)
    equals([item["id"] for item in items], ["h1", "u1", "bad3"], "the empty / too-thin ones are dropped")
    equals(items[0]["color"], "yellow", "no colour = the kind's default")
    equals(items[1]["color"], "purple", "a palette colour is kept")
    equals(items[2]["color"], "yellow", "white is not a colour here")
    approx(items[2]["x_pt"], 72, 0.001, "the anchor is re-derived from the boxes, not read")
    equals(page.locator(".swm-ann__markup").count(), 3, "three drawn")
    equals(page.locator('[data-testid="ann-h1-part-1"]').count(), 1, "a second hit box for line 2")
    clean(mock)


# --------------------------------------------------------------------------
# Highlight all matches, and the ceiling
# --------------------------------------------------------------------------

def search_for(page, query: str, expect: int) -> None:
    box = page.locator('[data-testid="pdf-search-input"]')
    box.fill(query)
    page.wait_for_function(
        "(n) => (document.querySelector('[data-testid=\"pdf-search-count\"]')?.textContent || '')"
        ".endsWith(`/ ${n}`)",
        arg=expect,
        timeout=15000,
    )


@test("highlight_all_matches_is_one_step_from_the_search_boxes")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    button = page.locator('[data-testid="pdf-search-highlight-all"]')
    truthy(button.is_disabled(), "off without a query")
    search_for(page, "column", 2)
    equals(page.locator(".pdf-text-layer").count(), 0, "no text layer needed (Select armed)")
    scale = scale_of(page)
    hits = page.evaluate(
        """(s) => { const p = document.querySelector('.pdf-page[data-page-number="1"]').getBoundingClientRect();
          return [...document.querySelectorAll('.pdf-page[data-page-number="1"] .pdf-search-hit')].map((m) => {
            const r = m.getBoundingClientRect();
            return { x: (r.left - p.left) / s, y: (r.top - p.top) / s, w: r.width / s, h: r.height / s }; }); }""",
        scale,
    )
    equals(len(hits), 2, "two marks")
    button.click()
    wait_stored(page, 2)
    items = stored(page)
    truthy(all(item["kind"] == "highlight" and item["color"] == "yellow" for item in items),
           "two yellow highlights")
    equals(depth(page, "undo"), 1, "ONE undo step")
    boxes = sorted((item["rects"][0] for item in items), key=lambda r: r["x_pt"])
    hits = sorted(hits, key=lambda r: r["x"])
    for rect, hit in zip(boxes, hits):
        approx(rect["x_pt"], hit["x"], 0.5, "the search's own box: x (pt)")
        approx(rect["y_pt"], hit["y"], 0.5, "y (pt)")
        approx(rect["w_pt"], hit["w"], 0.5, "w (pt)")
        approx(rect["h_pt"], hit["h"], 0.5, "h (pt)")
    # Inside the one pdf.js item "Right column": split by advance widths, not character count.
    approx(boxes[1]["x_pt"], 350 + text_width("Right ", "Helvetica", 12), 0.5, "on the word: x (pt)")
    approx(boxes[1]["w_pt"], text_width("column", "Helvetica", 12), 0.5, "on the word: width (pt)")
    button.click()
    page.wait_for_timeout(200)
    equals(len(stored(page)), 2, "pressing it again adds no second copy")
    contains(page.locator('[data-testid="editor-note"]').inner_text(), "already highlighted", "and says so")
    page.keyboard.press("Control+z")
    wait_stored(page, 0)
    shot(page, "highlight_all")
    clean(mock)


@test("highlight_all_matches_is_all_or_nothing_at_500")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock, seed=seed_texts(MAX_ANNOTATIONS - 1))
    contains(page.locator('[data-testid="ann-count"]').inner_text(), f"{MAX_ANNOTATIONS - 1} / 500",
             "the counter shows the new ceiling")
    search_for(page, "column", 2)
    page.locator('[data-testid="pdf-search-highlight-all"]').click()
    page.wait_for_timeout(300)
    equals(len(stored(page)), MAX_ANNOTATIONS - 1, "nothing was added (2 would pass 500)")
    note = page.locator('[data-testid="editor-note"]').inner_text()
    contains(note, "maximum of 500 annotations", "the note names the ceiling")
    contains(note, "(1 left)", "and what is left")
    clean(mock)


@test("a_selection_on_a_full_list_is_refused_out_loud")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock, seed=seed_texts(MAX_ANNOTATIONS))
    contains(page.locator('[data-testid="ann-count"]').inner_text(), "500 / 500", "full")
    arm(page, "highlight")
    wait_layer(page)
    select_text(page, "TOTAL DUE")
    page.wait_for_timeout(300)
    equals(len(stored(page)), MAX_ANNOTATIONS, "nothing added")
    contains(page.locator('[data-testid="editor-note"]').inner_text(),
             "already has the maximum of 500 annotations", "said out loud")
    clean(mock)


@test("the_list_now_holds_500_not_200")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock, seed=seed_texts(300))
    equals(len(stored(page)), 300, "300 stored entries are all read back (the old cap was 200)")
    arm(page, "highlight")
    wait_layer(page)
    select_text(page, "TOTAL DUE")
    wait_stored(page, 301)
    contains(page.locator('[data-testid="ann-count"]').inner_text(), "301 / 500", "the counter")
    clean(mock)


# --------------------------------------------------------------------------
# The slot date's font (Position & appearance)
# --------------------------------------------------------------------------

@test("the_slot_date_takes_six_families_and_bold_italic")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock, panel="sign")
    open_section(page, "layout")
    select = page.locator('select[aria-label="Date font"]')
    options = select.evaluate("(el) => [...el.options].map((o) => o.value)")
    equals(options, ["Helvetica", "Times-Roman", "Courier", "LiberationSans", "LiberationSerif",
                     "LiberationMono"], "the six families")
    select.select_option("LiberationSerif")
    page.locator('[data-testid="layout-date-bold"]').click()
    page.wait_for_timeout(150)
    equals(page.locator('[data-testid="layout-date-bold"]').get_attribute("aria-pressed"), "true", "B on")
    layout = stored_layout(page, mock) or {}
    equals(layout.get("date_font"), "LiberationSerif", "stored family")
    equals(layout.get("date_bold"), True, "stored bold")
    truthy("date_italic" not in layout, f"italic off = no key ({layout})")
    apply_dates_to_all(page)
    text = page.locator(".placed-dates text").first
    text.wait_for(timeout=10000)
    equals(text.get_attribute("data-font"), "LiberationSerif", "the date is drawn in it")
    equals(text.evaluate("(el) => getComputedStyle(el).fontWeight"), "700", "in bold")
    page.locator('[data-testid="layout-date-italic"]').click()
    page.wait_for_timeout(150)
    equals(text.evaluate("(el) => getComputedStyle(el).fontStyle"), "italic", "and italic")
    shot(page, "date_font")
    page.locator('[data-testid="layout-date-italic"]').click()
    page.wait_for_timeout(150)
    # The dates are already chosen: straight to finalize.
    page.locator('[data-testid="editor-finalize"]').click()
    page.wait_for_function("() => window.location.pathname.endsWith('/final')", timeout=20000)
    sent = (mock.bodies.get("finalize") or {}).get("layout") or {}
    equals(sent.get("date_font"), "LiberationSerif", "family sent")
    equals(sent.get("date_bold"), True, "bold sent as true")
    truthy("date_italic" not in sent, f"italic switched back off = no key ({sent})")
    clean(mock)


@test("an_untouched_date_style_sends_the_old_layout_body")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock, panel="sign")
    open_section(page, "layout")
    page.locator('select[aria-label="Date font"]').select_option("Courier")
    page.wait_for_timeout(120)
    # Bold on, then off again: back to the old shape.
    page.locator('[data-testid="layout-date-bold"]').click()
    page.locator('[data-testid="layout-date-bold"]').click()
    page.wait_for_timeout(120)
    truthy("date_bold" not in (stored_layout(page, mock) or {}), "no bold key stored")
    body = finalize_body(page, mock)
    sent = body.get("layout") or {}
    equals(set(sent.keys()), OLD_LAYOUT_KEYS, f"exactly the pre-v6 keys ({sorted(sent)})")
    equals(sent["date_font"], "Courier", "the family")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
