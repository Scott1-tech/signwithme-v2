"""
W5: Whiteout (W), Initials (I), and the clamp of STORED annotations.

WHITEOUT
--------
Arm it and drag a box: an opaque white box, in PDF points (±0.5 pt), ONE undo
step; the live preview is white with a thin dashed grey edge, and so is the
placed box -- the edge is a SCREEN-ONLY aid (the finalize body carries only
`{id, kind, page, x_pt, y_pt, w_pt, h_pt}`: the PDF gets the plain white
box). Selected, a free whiteout has 8 handles, moves, is deleted and undone.
The first whiteout ever says, once (`localStorage`), that it only HIDES --
the original text stays in the file. A signature printed where a whiteout is
stays ON TOP (server z-order whiteout → signatures → the rest), and the
preview shows the same (measured in pixels). The whiteout of an edited line
(WE) stays locked, without handles.

INITIALS
--------
The ordinary free-signature endpoint (`field_id: null`): off on a contract
nobody approved (with the reason), never in the undo history. The chooser is
the signature dialog; the box is fitted inside 60 × 24 pt at the image's
ratio, centred on the click. «Put on every page» = one POST per other page,
three at a time, from the page's bottom-right corner (a page of another size
still gets it in its corner), with progress and a report of what failed.
«Remove all initials» deletes the initials and nothing else. An initial is
told from a signature by its box (≤ 60 × 24 pt) or by the ids this browser
recorded -- so one placed on another computer is still labelled "Initials".
Saved to the library, it is labelled "Initials" and marked in My signatures.

THE CLAMP OF STORED ANNOTATIONS
-------------------------------
An annotation stored before its page's size was known (or for a page of
another size) is pulled onto its page as soon as the size arrives -- NOT an
undo step (W2b) -- the user is told once, and the finalize body is inside the
page (the server would answer 422 `annotation_outside_page`).

Everything is synthetic; every `/api/*` call is answered by `MockApi`.
Screenshots: `c:\\PTG\\.playwright-mcp\\swm_redact\\`.
"""

from __future__ import annotations

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (  # noqa: E402
    BASE,
    PIXEL_BANDS_JS,
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
    synthetic_multipage_pdf,
    truthy,
)
from test_geometry import server_stored_size  # noqa: E402

test = Registry("redact")

PAGE_W = 612.0
PAGE_H = 792.0
NOT_APPROVED = "Contract not approved yet"
WHITEOUT_NOTICE_KEY = "swm.whiteout.noticed"
INITIALS_W = 60.0
INITIALS_H = 24.0
#: The backend's whiteout shape (`WhiteoutAnnotationIn`), no group.
WHITEOUT_KEYS = ["h_pt", "id", "kind", "page", "w_pt", "x_pt", "y_pt"]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def shot(page, name: str) -> None:
    folder = os.path.join("c:\\", "PTG", ".playwright-mcp", "swm_redact")
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, f"{name}.png"))


def clean(mock: MockApi) -> None:
    equals(mock.page_errors, [], "no uncaught error")
    equals(mock.unmocked, [], "no unmocked API call")
    warnings = [text for text in mock.console_errors if "Warning:" in text and "React Router" not in text]
    equals(warnings, [], "no React warning")


def storage_key(mock: MockApi) -> str:
    return f"swm.annotations.{mock.contract_id}"


def open_doc(page, mock: MockApi, seed: list | None = None, panel: str = "edit",
             storage: dict[str, str] | None = None) -> None:
    if seed is not None or storage:
        goto_blank(page, mock)
        if seed is not None:
            page.evaluate("([k, v]) => localStorage.setItem(k, v)", [storage_key(mock), json.dumps(seed)])
        for key, value in (storage or {}).items():
            page.evaluate("([k, v]) => localStorage.setItem(k, v)", [key, value])
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
    # F1a: the top bar stands above the toolbar, so the pages start ~25 px
    # lower than before; a point that would now fall below the visible page
    # area is scrolled into view first (every answer is still in PDF points).
    bottom = page.evaluate(
        "() => document.querySelector('.pdf-viewer .pdf-stage').getBoundingClientRect().bottom")
    if box["y"] + y_pt * scale > bottom - 24:
        page.evaluate("(dy) => { document.querySelector('.pdf-viewer .pdf-stage').scrollTop += dy; }",
                      box["y"] + y_pt * scale - (bottom - 80))
        page.wait_for_timeout(200)
        box = origin(page, number)
        scale = box["width"] / PAGE_W
    return box["x"] + x_pt * scale, box["y"] + y_pt * scale


def drag_pt(page, x0: float, y0: float, x1: float, y1: float, *, steps: int = 10, release: bool = True) -> None:
    ax, ay = px(page, x0, y0)
    bx, by = px(page, x1, y1)
    page.mouse.move(ax, ay)
    page.mouse.down()
    page.mouse.move(bx, by, steps=steps)
    if release:
        page.mouse.up()
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


def handle_drag(page, testid: str, dx_pt: float, dy_pt: float) -> None:
    handle = page.locator(f'[data-testid="{testid}"]')
    box = handle.bounding_box()
    truthy(box is not None, f"{testid} is on screen")
    scale = scale_of(page)
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + dx_pt * scale, cy + dy_pt * scale, steps=8)
    page.mouse.up()
    page.wait_for_timeout(150)


def drag_ann(page, ann_id: str, dx_pt: float, dy_pt: float) -> None:
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


def ann_box(page, ann_id: str) -> dict:
    element = page.locator(f'[data-testid="ann-{ann_id}"]')
    return {name: float(element.get_attribute(f"data-{name}-pt")) for name in ("x", "y", "w", "h")}


def dark_pixels_in(page, x_pt: float, y_pt: float, w_pt: float, h_pt: float, number: int = 1) -> int:
    """Ink (r, g, b < 120) inside a rectangle of page `number`, in PDF points, from a real screenshot."""
    ax, ay = px(page, x_pt, y_pt, number)
    scale = scale_of(page)
    shot_bytes = page.screenshot(clip={"x": ax, "y": ay, "width": w_pt * scale, "height": h_pt * scale})
    bands = page.evaluate(PIXEL_BANDS_JS, {"png": base64.b64encode(shot_bytes).decode("ascii"), "strips": 1})
    return int(bands["dark"])


def draw_on_pad(page) -> None:
    pad = page.locator(".signature-pad__canvas").bounding_box()
    truthy(pad is not None, "the drawing pad is open")
    mid = pad["y"] + pad["height"] / 2
    page.mouse.move(pad["x"] + 40, mid)
    page.mouse.down()
    page.mouse.move(pad["x"] + 160, mid - 30, steps=8)
    page.mouse.move(pad["x"] + 280, mid + 20, steps=8)
    page.mouse.up()


def free_row(row_id: str, page_number: int, x: float, y: float, w: float, h: float) -> dict:
    return {
        "id": row_id, "contract_id": "ZZ-UI-1", "signer_name": None, "signer_email": None,
        "signer_role": "contractor", "page_number": page_number,
        "bbox": {"x": x, "y": y, "width": w, "height": h},
        "field_id": None, "signed_at": "2026-09-23T08:00:00+00:00",
    }


def sig_kinds(page) -> dict[str, str]:
    return page.evaluate(
        "() => Object.fromEntries([...document.querySelectorAll('.swm-sig')]"
        ".map((el) => [el.dataset.signatureId, el.dataset.signatureKind]))"
    )


def mixed_size_pdf(sizes: list[tuple[float, float]]) -> bytes:
    """Pages of their own size (w, h pt), each with a short rule so it is not blank."""
    objs: list[str] = ["", ""]
    kids: list[str] = []
    for index, (width, height) in enumerate(sizes):
        page_obj = 3 + index * 2
        kids.append(f"{page_obj} 0 R")
        content = "0 0 0 RG 1 w 72 100 m 300 100 l S"
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.0f} {height:.0f}] "
            f"/Contents {page_obj + 1} 0 R /Resources << >> >>"
        )
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    objs[0] = "<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(sizes)} >>"
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


#: Counts the placement POSTs in flight, and holds each answer back a little so
#: the pool's lanes really overlap (`BULK_CONCURRENCY` = 3).
FETCH_PROBE = """
(() => {
  const original = window.fetch.bind(window);
  window.__swmPlaceInFlight = 0;
  window.__swmPlaceMaxInFlight = 0;
  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input.url;
    const method = (init && init.method) || 'GET';
    const placing = method === 'POST' && /\\/api\\/contracts\\/[^/]+\\/signatures$/.test(url);
    if (!placing) return original(input, init);
    window.__swmPlaceInFlight += 1;
    window.__swmPlaceMaxInFlight = Math.max(window.__swmPlaceMaxInFlight, window.__swmPlaceInFlight);
    try {
      const response = await original(input, init);
      await new Promise((resolve) => setTimeout(resolve, 250));
      return response;
    } finally {
      window.__swmPlaceInFlight -= 1;
    }
  };
})();
"""


# --------------------------------------------------------------------------
# The toolbar
# --------------------------------------------------------------------------

@test("the_toolbar_still_fits_one_row_at_1600_with_whiteout_and_initials")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    toolbar = page.locator('[data-testid="editor-toolbar"]')
    for name, key in (("whiteout", "W"), ("initials", "I")):
        tool = page.locator(f'[data-testid="tool-{name}"]')
        equals(tool.count(), 1, f"tool-{name} exists")
        equals(tool.get_attribute("aria-keyshortcuts"), key, f"{name} announces {key}")
    # F1a: Whiteout is a button of the row; Initials is an item of «Sign ▼»
    # (after Signature), shown when the list opens.
    truthy(page.locator('[data-testid="tool-whiteout"]').is_visible(), "whiteout is a button of the row")
    equals(page.locator('[data-testid="toolgroup-sign-menu"] [data-testid="tool-initials"]').count(), 1,
           "initials is in «Sign ▼»")
    page.locator('[data-testid="toolgroup-sign-toggle"]').click()
    truthy(page.locator('[data-testid="tool-initials"]').is_visible(), "and shown when it opens")
    page.keyboard.press("Escape")
    for armed in ("select", "whiteout", "initials", "rect", "text"):
        arm_tool(page, armed)
        page.wait_for_timeout(300)
        equals(toolbar.get_attribute("data-overflow"), "0", f"{armed} armed: nothing under «More ▾»")
        truthy(toolbar.bounding_box()["height"] < 64, f"{armed} armed: one row")
    overflow = page.evaluate(
        "() => { const t = document.querySelector('[data-testid=\"editor-toolbar\"]');"
        " return t.scrollWidth - t.clientWidth; }"
    )
    truthy(overflow <= 0, f"no horizontal overflow ({overflow} px)")
    contains(page.locator('[data-testid="tool-whiteout"]').get_attribute("title") or "",
             "original text stays inside the file", "the tool's own description says it only hides")
    contains(page.locator('[data-testid="tool-initials"]').get_attribute("title") or "",
             "Undo does not reach", "the initials tool says it is not undoable")
    page.keyboard.press("v")
    page.keyboard.press("w")
    equals(page.locator('[data-testid="tool-whiteout"]').get_attribute("aria-pressed"), "true", "W arms Whiteout")
    page.keyboard.press("i")
    equals(page.locator('[data-testid="tool-initials"]').get_attribute("aria-pressed"), "true", "I arms Initials")
    shot(page, "toolbar")
    # With a free-signature count on the row too (twelve initials: "12 signatures").
    mock.free_rows = [free_row(f"zz-f{i}", 1, 300, 500 + i * 20, 48, 16) for i in range(12)]
    open_doc(page, mock, panel="sign")
    page.wait_for_selector('[data-testid="sig-count"]', timeout=10000)
    for armed in ("signature", "initials", "whiteout", "select"):
        arm_tool(page, armed)
        page.wait_for_timeout(300)
        equals(toolbar.get_attribute("data-overflow"), "0", f"{armed} armed + 12 signatures: nothing under «More ▾»")
        truthy(page.locator('[data-testid="sig-count"]').is_visible(), f"{armed}: the count is on the row")
        truthy(toolbar.bounding_box()["height"] < 64, f"{armed}: one row")
    clean(mock)


# --------------------------------------------------------------------------
# Whiteout
# --------------------------------------------------------------------------

@test("a_whiteout_drag_is_its_box_in_points_one_step_and_the_backend_shape")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    arm_tool(page, "whiteout")
    classes = page.locator('[data-testid="workspace"]').get_attribute("class") or ""
    contains(classes, "workspace--drawing", "a drag tool")
    contains(classes, "workspace--placing", "the slots step aside")
    before = depth(page)
    # From ON the sig_1 slot box (72,160 180x34): it still draws.
    drag_pt(page, 80, 170, 180.2, 220.1, release=False)
    preview = page.locator('[data-testid="ws-capture-1"] [data-testid="capture-preview"]')
    equals(preview.count(), 1, "a live preview on page 1")
    equals(preview.get_attribute("data-kind"), "whiteout", "of a whiteout")
    equals(preview.get_attribute("data-box-pt"), "80,170,100,50", "its box (pt)")
    style = preview.evaluate(
        "(el) => { const s = getComputedStyle(el); return {fill: s.fill, stroke: s.stroke, dash: s.strokeDasharray}; }"
    )
    equals(style["fill"], "rgb(255, 255, 255)", "the preview is white")
    truthy(style["dash"] not in ("", "none"), f"with a dashed edge ({style})")
    shot(page, "whiteout_preview")
    page.mouse.up()
    wait_stored(page, mock, 1)
    item = stored(page, mock)[0]
    equals(item["kind"], "whiteout", "a whiteout")
    approx(item["x_pt"], 80, 0.5, "x (pt)")
    approx(item["y_pt"], 170, 0.5, "y (pt)")
    approx(item["w_pt"], 100, 0.5, "w (pt)")
    approx(item["h_pt"], 50, 0.5, "h (pt)")
    truthy("group_id" not in item, "a free whiteout has no group")
    equals(depth(page), before + 1, "ONE undo step")
    rect = page.locator(f'[data-testid="ann-whiteout-{item["id"]}"]')
    equals(rect.count(), 1, "drawn")
    painted = rect.evaluate(
        "(el) => { const s = getComputedStyle(el); return {fill: s.fill, dash: s.strokeDasharray,"
        " cls: el.getAttribute('class')}; }"
    )
    equals(painted["fill"], "rgb(255, 255, 255)", "opaque white")
    truthy(painted["dash"] not in ("", "none"), "a dashed screen-only edge")
    contains(painted["cls"], "swm-ann__whiteout--free", "marked as a free whiteout")
    # Objects: «Whiteout · p.1».
    open_section(page, "objects")
    row = page.locator(f'[data-testid="object-ann-{item["id"]}"]')
    equals(row.get_attribute("data-kind"), "whiteout", "an Objects row")
    contains(row.inner_text(), "Whiteout", "named Whiteout")
    contains(row.inner_text(), "p.1", "with its page")
    # Undo / redo.
    page.keyboard.press("Control+z")
    wait_stored(page, mock, 0)
    page.keyboard.press("Control+y")
    wait_stored(page, mock, 1)
    # The finalize body: exactly the backend's keys -- no colour, no stroke, no edge.
    body = finalize_body(page, mock)
    entries = [entry for entry in body["annotations"] if entry["kind"] == "whiteout"]
    equals(len(entries), 1, "one whiteout sent")
    entry = entries[0]
    equals(sorted(entry), WHITEOUT_KEYS, "exactly the backend's keys")
    approx(entry["x_pt"], 80, 0.5, "body x")
    approx(entry["y_pt"], 170, 0.5, "body y")
    approx(entry["w_pt"], 100, 0.5, "body w")
    approx(entry["h_pt"], 50, 0.5, "body h")
    out = os.environ.get("SWM_REDACT_BODY_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(body, handle, indent=2)
    clean(mock)


@test("a_free_whiteout_has_eight_handles_moves_resizes_and_is_deleted")
def _(page, mock: MockApi) -> None:
    seed = [{"id": "w1", "kind": "whiteout", "page": 1, "x_pt": 100, "y_pt": 250, "w_pt": 120, "h_pt": 40}]
    open_doc(page, mock, seed)
    arm_tool(page, "select")
    page.locator('[data-testid="ann-w1"]').click()
    page.wait_for_timeout(150)
    for handle in ("nw", "n", "ne", "e", "se", "s", "sw", "w"):
        equals(page.locator(f'[data-testid="ann-handle-{handle}"]').count(), 1, f"handle {handle}")
    equals(page.locator('[data-testid="ann-handle"]').count(), 0, "no size grip (a whiteout has no size)")
    truthy(page.locator('[data-testid="ann-color"]').is_disabled(), "no colour")
    contains(page.locator('[data-testid="ann-color"]').get_attribute("title") or "", "whiteout",
             "and it says why")
    shot(page, "whiteout_handles")
    steps = depth(page)
    handle_drag(page, "ann-handle-se", 20, 10)
    box = ann_box(page, "w1")
    approx(box["x"], 100, 0.5, "se: left edge stays")
    approx(box["y"], 250, 0.5, "se: top edge stays")
    approx(box["w"], 140, 0.5, "se: +20 pt wide")
    approx(box["h"], 50, 0.5, "se: +10 pt tall")
    equals(depth(page), steps + 1, "one step for the resize")
    handle_drag(page, "ann-handle-nw", -10, -20)
    box = ann_box(page, "w1")
    approx(box["x"], 90, 0.5, "nw: left edge follows")
    approx(box["y"], 230, 0.5, "nw: top edge follows")
    approx(box["w"], 150, 0.5, "nw: the right edge stays")
    approx(box["h"], 70, 0.5, "nw: the bottom edge stays")
    drag_ann(page, "w1", 30, 40)
    box = ann_box(page, "w1")
    approx(box["x"], 120, 0.5, "moved right")
    approx(box["y"], 270, 0.5, "moved down")
    approx(box["w"], 150, 0.01, "a move keeps the size")
    equals(depth(page), steps + 3, "one step each")
    # Pulled against the page edge: stays whole on the page.
    handle_drag(page, "ann-handle-e", 800, 0)
    box = ann_box(page, "w1")
    approx(box["x"] + box["w"], PAGE_W, 0.5, "the right edge stops at the page")
    page.locator('[data-testid="ann-w1"]').click()
    page.keyboard.press("Delete")
    wait_stored(page, mock, 0)
    page.keyboard.press("Control+z")
    wait_stored(page, mock, 1)
    equals(stored(page, mock)[0]["id"], "w1", "undo brings it back")
    clean(mock)


@test("a_signature_prints_over_a_whiteout_and_the_preview_shows_it")
def _(page, mock: MockApi) -> None:
    """z-order whiteout → signatures (server): the slot signature stays visible ON the white box."""
    open_doc(page, mock, panel="sign")
    page.wait_for_function(
        "() => [...document.querySelectorAll('img.placed-signature')].some((i) => i.complete && i.naturalWidth > 0)",
        timeout=20000,
    )
    image = page.locator('img.placed-signature[data-field-id="sig_1"]').bounding_box()
    truthy(image is not None, "sig_1 carries its signature")
    scale = scale_of(page)
    page_box = origin(page)
    sig = {
        "x": (image["x"] - page_box["x"]) / scale, "y": (image["y"] - page_box["y"]) / scale,
        "w": image["width"] / scale, "h": image["height"] / scale,
    }
    line = rule_line(mock.slot_by_id("sig_1"))
    # The page's own rule under sig_1, right of the signature image (x 256…298).
    rule_before = dark_pixels_in(page, 256, line - 3, 42, 6)
    sig_before = dark_pixels_in(page, sig["x"], sig["y"], sig["w"], sig["h"])
    truthy(rule_before > 20, f"the page's rule is ink before ({rule_before})")
    truthy(sig_before > 50, f"the signature is ink before ({sig_before})")

    arm_tool(page, "whiteout")
    drag_pt(page, 60, 150, 320, 215)
    wait_stored(page, mock, 1)
    arm_tool(page, "select")
    # Esc in the toolbar hands the focus back to the page; the next one drops
    # the selection (no selection frame over the pixels read below).
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    page.wait_for_function("() => !document.querySelector('.swm-ann.is-selected')", timeout=5000)
    page.mouse.move(5, 5)
    page.wait_for_timeout(300)
    rule_after = dark_pixels_in(page, 256, line - 3, 42, 6)
    sig_after = dark_pixels_in(page, sig["x"], sig["y"], sig["w"], sig["h"])
    equals(rule_after, 0, "the whiteout hides the page's own ink")
    truthy(sig_after >= sig_before * 0.9, f"the signature is still on top ({sig_before} → {sig_after})")
    # Paint order: the whiteout layer comes before every signature (same stacking context).
    order = page.evaluate(
        """() => {
          const overlay = document.querySelector('.pdf-page[data-page-number="1"] .pdf-overlay');
          const kids = [...overlay.children];
          const at = (sel) => kids.findIndex((el) => el.matches(sel));
          return { whiteouts: at('[data-testid="ann-whiteouts-1"]'),
                   slotSig: at('.placed-signature'), slot: at('.slot'),
                   annotations: at('[data-testid="ann-layer-1"]') };
        }"""
    )
    truthy(0 <= order["whiteouts"] < order["slotSig"], f"whiteouts under the slot signatures ({order})")
    truthy(order["whiteouts"] < order["slot"], f"and under the slot boxes ({order})")
    truthy(order["annotations"] > order["slotSig"], f"the other annotations stay above ({order})")
    shot(page, "whiteout_under_signature")
    clean(mock)


@test("the_first_whiteout_says_once_that_it_only_hides")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    equals(page.evaluate("(k) => localStorage.getItem(k)", WHITEOUT_NOTICE_KEY), None, "never said yet")
    arm_tool(page, "whiteout")
    equals(note_text(page), "", "arming it says nothing yet")
    drag_pt(page, 100, 300, 200, 340)
    wait_stored(page, mock, 1)
    note = note_text(page)
    contains(note, "only hides", "the first whiteout says what it is")
    contains(note, "original text stays inside the file", "and that the text is still in the file")
    truthy(page.evaluate("(k) => localStorage.getItem(k)", WHITEOUT_NOTICE_KEY), "remembered")
    shot(page, "whiteout_notice")
    drag_pt(page, 100, 400, 200, 440)
    wait_stored(page, mock, 2)
    equals(note_text(page), "", "the second one says nothing")
    page.reload()
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_timeout(400)
    arm_tool(page, "whiteout")
    drag_pt(page, 100, 500, 200, 540)
    wait_stored(page, mock, 3)
    equals(note_text(page), "", "nor after a reload")
    clean(mock)


@test("an_edited_lines_whiteout_stays_locked_without_handles")
def _(page, mock: MockApi) -> None:
    seed = [
        {"id": "gw", "kind": "whiteout", "page": 1, "x_pt": 99.5, "y_pt": 289.5, "w_pt": 101, "h_pt": 13,
         "group_id": "g1"},
        {"id": "gt", "kind": "text", "page": 1, "x_pt": 100, "y_pt": 300, "text": "Edited",
         "font": "LiberationSans", "size_pt": 11, "group_id": "g1"},
    ]
    open_doc(page, mock, seed)
    arm_tool(page, "select")
    whiteout = page.locator('[data-testid="ann-gw"]')
    equals(whiteout.get_attribute("data-locked"), "true", "locked")
    whiteout.click(position={"x": 90, "y": 4})
    page.wait_for_timeout(150)
    equals(page.locator('[data-testid="ann-handle-se"]').count(), 0, "no box handles on a grouped whiteout")
    cls = page.locator('[data-testid="ann-whiteout-gw"]').get_attribute("class") or ""
    truthy("swm-ann__whiteout--free" not in cls, f"no screen edge on an edited line ({cls})")
    drag_ann(page, "gw", 40, 40)
    box = ann_box(page, "gw")
    approx(box["x"], 99.5, 0.01, "never moves (x)")
    approx(box["y"], 289.5, 0.01, "never moves (y)")
    clean(mock)


# --------------------------------------------------------------------------
# Initials
# --------------------------------------------------------------------------

@test("initials_go_through_the_free_signature_endpoint_small_and_outside_the_undo")
def _(page, mock: MockApi) -> None:
    mock.place_echo = True
    open_doc(page, mock, panel="sign")
    undo_before = depth(page)
    arm_tool(page, "initials")
    equals(page.locator('[data-testid="workspace"]').get_attribute("data-tool"), "initials", "armed")
    ax, ay = px(page, 450, 600)
    page.mouse.click(ax, ay)
    page.wait_for_selector(".modal__panel", timeout=15000)
    contains(page.locator(".modal__title").inner_text(), "initials", "the chooser is for initials")
    draw_on_pad(page)
    page.get_by_role("button", name="Apply initials").click()
    page.wait_for_function(
        "() => document.querySelectorAll('.swm-sig[data-signature-kind=\"initials\"]').length === 1",
        timeout=20000,
    )
    equals(len(mock.place_bodies), 1, "ONE POST")
    body = mock.place_bodies[0]
    equals(body.get("field_id"), None, "a free placement (field_id: null)")
    equals(body.get("page"), 1, "on the clicked page")
    truthy(str(body.get("image_data", "")).startswith("data:image/png;base64,"), "the drawn image")
    bbox = body["bbox"]
    truthy(bbox["width"] <= INITIALS_W + 0.01 and bbox["height"] <= INITIALS_H + 0.01,
           f"inside 60 × 24 pt ({bbox})")
    # Fix 2026-09-27: the ratio of the image the SERVER keeps (cropped to its
    # ink by `signature_image.prepare_signature_image`), not the raw pad's
    # 480 × 160 -- that 3:1 box printed the ink as a thin strip inside it.
    stored_w, stored_h = server_stored_size(body["image_data"])
    approx(bbox["width"] / bbox["height"], stored_w / stored_h, 0.02 * stored_w / stored_h,
           f"the stored image's ratio ({stored_w} × {stored_h}) kept")
    approx(bbox["width"], 60, 0.01, "as large as the box allows")
    approx(bbox["x"] + bbox["width"] / 2, 450, 0.6, "centred on the click (x)")
    approx(bbox["y"] + bbox["height"] / 2, 600, 0.6, "centred on the click (y)")
    element = page.locator('.swm-sig[data-signature-kind="initials"]')
    contains(element.get_attribute("class") or "", "swm-sig--initials", "drawn apart from a signature")
    approx(float(element.get_attribute("data-w-pt")), bbox["width"], 0.01, "drawn at its box")
    equals(depth(page), undo_before, "NOT an undo step")
    truthy(page.locator('[data-testid="editor-undo"]').is_disabled() or depth(page) == undo_before,
           "undo has nothing new")
    contains(note_text(page), "Undo does not reach", "the reason is said")
    page.keyboard.press("Control+z")
    page.wait_for_timeout(250)
    equals(page.locator('.swm-sig[data-signature-kind="initials"]').count(), 1, "Ctrl+Z leaves it")
    truthy(not mock.deleted_signatures, "no hidden DELETE")
    open_section(page, "objects")
    row = page.locator(f'[data-testid="object-sig-{mock.free_rows[0]["id"]}"]')
    equals(row.get_attribute("data-kind"), "initials", "Objects: an initials row")
    contains(row.inner_text(), "Initials", "named Initials")
    recorded = page.evaluate("(k) => JSON.parse(localStorage.getItem(k) || '[]')",
                             f"swm.initials.{mock.contract_id}")
    equals(recorded, [mock.free_rows[0]["id"]], "this browser recorded the id")
    shot(page, "initials_placed")
    clean(mock)


@test("initials_are_off_on_a_contract_nobody_approved")
def _(page, mock: MockApi) -> None:
    mock.review_status = "pending"
    mock.status = "analyzed"
    open_doc(page, mock)
    initials = page.locator('[data-testid="tool-initials"]')
    truthy(initials.is_disabled(), "Initials is off")
    equals(initials.get_attribute("data-disabled-reason"), NOT_APPROVED, "with the reason")
    page.keyboard.press("i")
    truthy(page.locator('[data-testid="workspace"]').get_attribute("data-tool") != "initials",
           "the key does not arm it either")
    whiteout = page.locator('[data-testid="tool-whiteout"]')
    truthy(not whiteout.is_disabled(), "Whiteout is local: it works")
    arm_tool(page, "whiteout")
    drag_pt(page, 100, 300, 200, 340)
    wait_stored(page, mock, 1)
    equals(mock.place_bodies, [], "nothing was posted")
    clean(mock)


@test("every_page_is_one_post_per_page_three_at_a_time_with_a_partial_failure_report")
def _(page, mock: MockApi) -> None:
    mock.set_pdf(synthetic_multipage_pdf(5, [rule_line(s) for s in mock.slots]))
    mock.place_echo = True
    mock.place_fail_pages = {3: 500}
    mock.free_rows = [free_row("zz-init-1", 1, 500, 740, 48, 16)]
    page.add_init_script(FETCH_PROBE)
    open_doc(page, mock, panel="sign")
    open_section(page, "objects")
    row = page.locator('[data-testid="object-sig-zz-init-1"]')
    equals(row.get_attribute("data-kind"), "initials", "the seeded row is an initial (by its box)")
    page.locator('[data-testid="initials-every-page-zz-init-1"]').click()
    page.wait_for_selector('[data-testid="initials-report"]', timeout=20000)
    pages = sorted(body["page"] for body in mock.place_bodies)
    equals(pages, [2, 3, 4, 5], "N = 4 POSTs: every page but its own")
    for body in mock.place_bodies:
        equals(body.get("field_id"), None, f"page {body['page']}: free placement")
        equals(body["bbox"], {"x": 500, "y": 740, "width": 48, "height": 16},
               f"page {body['page']}: the same corner (same page size)")
        truthy(str(body.get("image_data", "")).startswith("data:image/"), "the initial's own image")
    peak = page.evaluate("() => window.__swmPlaceMaxInFlight")
    truthy(2 <= peak <= 3, f"three at a time ({peak} in flight at most)")
    report = page.locator('[data-testid="initials-report"]').inner_text()
    contains(report, "3 of 4", "three placed")
    contains(report, "p.3", "the failed page is named")
    page.wait_for_function(
        "() => document.querySelectorAll('.swm-sig[data-signature-kind=\"initials\"]').length === 4",
        timeout=10000,
    )
    shot(page, "initials_every_page")
    # A second press adds none where one already sits: only page 3 is tried again.
    mock.place_fail_pages = {}
    before = len(mock.place_bodies)
    page.locator('[data-testid="initials-every-page-zz-init-1"]').click()
    page.wait_for_function(
        "(n) => document.querySelectorAll('.swm-sig[data-signature-kind=\"initials\"]').length === n",
        arg=5, timeout=20000,
    )
    equals([body["page"] for body in mock.place_bodies[before:]], [3], "only the missing page")
    clean(mock)


@test("every_page_keeps_the_bottom_right_corner_on_pages_of_another_size")
def _(page, mock: MockApi) -> None:
    mock.set_slots(mock.slots, signed=[])
    mock.set_pdf(mixed_size_pdf([(612, 792), (595, 842), (792, 612)]))
    mock.place_echo = True
    mock.free_rows = [free_row("zz-init-1", 1, 500, 740, 48, 16)]
    open_doc(page, mock, panel="sign")
    open_section(page, "objects")
    page.locator('[data-testid="initials-every-page-zz-init-1"]').click()
    page.wait_for_selector('[data-testid="initials-report"]', timeout=20000)
    by_page = {body["page"]: body["bbox"] for body in mock.place_bodies}
    # Page 1: 64 pt from the right (612 - 548), 36 pt from the bottom (792 - 756).
    equals(by_page[2], {"x": 595 - 64 - 48, "y": 842 - 36 - 16, "width": 48, "height": 16}, "A4: same corner")
    equals(by_page[3], {"x": 792 - 64 - 48, "y": 612 - 36 - 16, "width": 48, "height": 16},
           "landscape: same corner")
    clean(mock)


@test("remove_all_initials_deletes_the_initials_and_nothing_else")
def _(page, mock: MockApi) -> None:
    mock.free_rows = [
        free_row("zz-init-1", 1, 500, 740, 48, 16),
        free_row("zz-init-2", 1, 500, 700, 60, 20),
        free_row("zz-sig-free", 1, 72, 600, 180, 34),
    ]
    open_doc(page, mock, panel="sign")
    kinds = sig_kinds(page)
    equals(kinds.get("zz-init-1"), "initials", "a small box is an initial (placed elsewhere: no record here)")
    equals(kinds.get("zz-init-2"), "initials", "60 × 20 too")
    equals(kinds.get("zz-sig-free"), "signature", "180 × 34 is a signature")
    open_section(page, "objects")
    page.locator('[data-testid="initials-remove-all"]').click()
    page.wait_for_function(
        "() => document.querySelectorAll('.swm-sig[data-signature-kind=\"initials\"]').length === 0",
        timeout=20000,
    )
    equals(sorted(mock.deleted_signatures), ["zz-init-1", "zz-init-2"], "exactly the initials were deleted")
    equals(page.locator('.swm-sig[data-signature-kind="signature"]').count(), 1, "the signature stays")
    confirms = page.evaluate("() => window.__swmConfirms || []")
    truthy(any("initials" in text for text in confirms), f"asked first ({confirms})")
    clean(mock)


@test("saved_initials_are_labelled_and_marked_in_my_signatures")
def _(page, mock: MockApi) -> None:
    mock.place_echo = True
    open_doc(page, mock, panel="sign")
    arm_tool(page, "initials")
    ax, ay = px(page, 450, 600)
    page.mouse.click(ax, ay)
    page.wait_for_selector(".modal__panel", timeout=15000)
    draw_on_pad(page)
    page.get_by_label("Save to my signatures").check()
    page.get_by_role("button", name="Apply initials").click()
    page.wait_for_function("() => document.querySelectorAll('.swm-sig').length === 1", timeout=20000)
    for _ in range(50):
        if mock.library_saves:
            break
        page.wait_for_timeout(100)
    equals(len(mock.library_saves), 1, "saved to the library once")
    equals(mock.library_saves[0].get("label"), "Initials", "labelled Initials")
    mock.user_signatures.append({"id": "zz-lib-sig", "label": "Full signature", "is_default": True,
                                 "signature_hash": "h", "created_at": "2026-09-23T08:00:00+00:00"})
    page.goto(f"{BASE}/signatures")
    page.wait_for_selector(".sig-card", timeout=20000)
    marks = page.evaluate(
        "() => [...document.querySelectorAll('.sig-card')].map((card) => ({"
        " title: card.querySelector('.sig-card__title').innerText,"
        " initials: !!card.querySelector('[data-testid=\"sig-card-initials\"]') }))"
    )
    by_title = {entry["title"].split("\n")[0].strip(): entry["initials"] for entry in marks}
    truthy(any(title.startswith("Initials") and flag for title, flag in by_title.items()),
           f"the initials card carries the mark ({marks})")
    truthy(any(title.startswith("Full signature") and not flag for title, flag in by_title.items()),
           f"a signature does not ({marks})")
    shot(page, "my_signatures_initials")
    clean(mock)


# --------------------------------------------------------------------------
# The clamp of stored annotations
# --------------------------------------------------------------------------

@test("stored_annotations_off_their_page_are_pulled_on_once_the_size_is_known")
def _(page, mock: MockApi) -> None:
    seed = [
        {"id": "r1", "kind": "rect", "page": 1, "x_pt": 580, "y_pt": 100, "w_pt": 100, "h_pt": 40,
         "stroke_pt": 2, "color": "black", "fill": False},
        {"id": "w1", "kind": "whiteout", "page": 1, "x_pt": 300, "y_pt": 780, "w_pt": 80, "h_pt": 40},
        {"id": "t1", "kind": "text", "page": 1, "x_pt": 700, "y_pt": 900, "text": "Far away",
         "font": "Helvetica", "size_pt": 11},
        {"id": "h1", "kind": "highlight", "page": 1, "rects": [{"x_pt": 590, "y_pt": 300, "w_pt": 60, "h_pt": 12}],
         "color": "yellow"},
        {"id": "l1", "kind": "line", "page": 1, "x1_pt": 500, "y1_pt": 780, "x2_pt": 640, "y2_pt": 820,
         "stroke_pt": 2, "color": "black"},
        {"id": "ok", "kind": "check", "page": 1, "x_pt": 100, "y_pt": 100, "size_pt": 16},
    ]
    open_doc(page, mock, seed)
    page.wait_for_function(
        "(k) => { const s = JSON.parse(localStorage.getItem(k) || '[]');"
        " const r = s.find((e) => e.id === 'r1'); return r && r.x_pt + r.w_pt <= 612; }",
        arg=storage_key(mock), timeout=10000,
    )
    items = {item["id"]: item for item in stored(page, mock)}
    approx(items["r1"]["x_pt"] + items["r1"]["w_pt"], PAGE_W, 0.01, "rect: pulled in, right edge on the page")
    approx(items["r1"]["w_pt"], 100, 0.01, "rect: size kept")
    approx(items["w1"]["y_pt"] + items["w1"]["h_pt"], PAGE_H, 0.01, "whiteout: bottom edge on the page")
    approx(items["w1"]["h_pt"], 40, 0.01, "whiteout: size kept (a free one moves)")
    truthy(items["t1"]["x_pt"] <= PAGE_W and items["t1"]["y_pt"] <= PAGE_H, f"text anchor on the page ({items['t1']})")
    rect = items["h1"]["rects"][0]
    truthy(rect["x_pt"] + rect["w_pt"] <= PAGE_W + 0.01, f"highlight box on the page ({rect})")
    truthy(max(items["l1"]["x1_pt"], items["l1"]["x2_pt"]) <= PAGE_W + 0.01
           and max(items["l1"]["y1_pt"], items["l1"]["y2_pt"]) <= PAGE_H + 0.01, f"line on the page ({items['l1']})")
    equals(items["ok"]["x_pt"], 100, "an entry already on its page is untouched")
    equals(depth(page), 0, "NOT an undo step")
    truthy(page.locator('[data-testid="editor-undo"]').is_disabled(), "undo has nothing")
    note = note_text(page)
    contains(note, "5 annotations were moved to stay on the page", "said once")
    # Said once: a later change (a new drawing clears the note line) does not repeat it.
    page.evaluate("(k) => localStorage.setItem(k, '1')", WHITEOUT_NOTICE_KEY)
    arm_tool(page, "rect")
    drag_pt(page, 100, 400, 200, 440)
    wait_stored(page, mock, 7)
    equals(note_text(page), "", "not said again")
    equals(depth(page), 1, "the drawing is the only step")
    shot(page, "clamp_note")
    body = finalize_body(page, mock)
    for entry in body["annotations"]:
        if "w_pt" in entry and "x_pt" in entry:
            truthy(entry["x_pt"] >= 0 and entry["x_pt"] + entry["w_pt"] <= PAGE_W + 0.01, f"{entry['id']}: x inside")
            truthy(entry["y_pt"] >= 0 and entry["y_pt"] + entry["h_pt"] <= PAGE_H + 0.01, f"{entry['id']}: y inside")
        if "rects" in entry:
            for box in entry["rects"]:
                truthy(box["x_pt"] + box["w_pt"] <= PAGE_W + 0.01, f"{entry['id']}: box inside")
        if "x1_pt" in entry:
            truthy(max(entry["x1_pt"], entry["x2_pt"]) <= PAGE_W + 0.01, f"{entry['id']}: ends inside (x)")
            truthy(max(entry["y1_pt"], entry["y2_pt"]) <= PAGE_H + 0.01, f"{entry['id']}: ends inside (y)")
        if entry["kind"] == "text":
            truthy(entry["x_pt"] <= PAGE_W and entry["y_pt"] <= PAGE_H, "text anchor inside")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
