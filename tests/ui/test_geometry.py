"""
Fix 2026-09-27: two preview ↔ final-PDF mismatches found by the live E2E.

1. THE PAGE NEVER MOVES UNDER A DRAG
------------------------------------
A drag maps the pointer through the page's CURRENT box on every move
(`ToolCaptureLayer.pointOf`). The passing note used to stand in the document
column, above the pages; a new drag clears it (`startGesture` →
`ctx.notify(null)`), so the pages slid up 74 px under a pointer that had not
moved and a 19 pt whiteout was saved 60.5 pt tall -- all four drag tools.
Now the note floats over the pages (`EditorNote`, out of the layout) and the
lasting banners' block is frozen while a press on a page is held
(`useGestureLayoutLock`). Checked: with a note on screen, each of whiteout /
rect / line / ink keeps the page's `getBoundingClientRect().top` still from
press to release and stores exactly the geometry under the pointer (±0.5 pt);
a finalize-style error that ARRIVES mid-drag (a failed remove, answered late)
does not move the page until the press is over.

2. A FREE SIGNATURE / INITIAL IS PREVIEWED WHERE THE PDF PRINTS IT
------------------------------------------------------------------
The PDF builder stands a free signature's INK bottom on `bbox bottom − 1 pt −
signature_offset_pt`, left-aligned, contain-fitted into `bbox × signature_scale`
(`pdf_builder._draw_signature`, contract v1.1). The preview used to centre the
image in the box. Checked against the builder's own numbers: `_ink_metrics`
of the served image (run in the backend's interpreter), with a non-zero
global offset and scale. And an initial's box now has the ratio of the image
the SERVER keeps (`signature_image.prepare_signature_image`, imported from the
backend), not the raw pad's 3:1.

Everything is synthetic; every `/api/*` call is answered by `MockApi`.
"""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from _harness import (  # noqa: E402
    BASE,
    MockApi,
    Registry,
    approx,
    arm_tool,
    cli_main,
    equals,
    goto_blank,
    synthetic_signature_png,
    truthy,
)

test = Registry("geometry")

PAGE_W = 612.0
WHITEOUT_NOTICE_KEY = "swm.whiteout.noticed"
PAGE_TOP_JS = "() => document.querySelector('.pdf-page[data-page-number=\"1\"]').getBoundingClientRect().top"


# --------------------------------------------------------------------------
# The backend's own rules (read, never changed)
# --------------------------------------------------------------------------

def backend_dir() -> str:
    folder = HERE
    for _ in range(6):
        candidate = os.path.join(folder, "backend")
        if os.path.isfile(os.path.join(candidate, "app", "services", "pdf_builder.py")):
            return candidate
        folder = os.path.dirname(folder)
    raise AssertionError("the backend folder (with app/services/pdf_builder.py) was not found above tests/ui")


def server_stored_size(image_data: str) -> tuple[int, int]:
    """The size of the image the server stores for this data URL (its real clean-up + crop)."""
    backend = backend_dir()
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from PIL import Image  # noqa: WPS433
    from app.services.signature_image import prepare_signature_image  # noqa: WPS433

    raw = base64.b64decode(image_data.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as image:
        stored = prepare_signature_image(image.convert("RGBA"))
    return stored.size


def builder_ink_metrics(png: bytes) -> tuple[int, int, int, int, int, int]:
    """`pdf_builder._ink_metrics(png)`, run by the backend's interpreter (it needs reportlab)."""
    backend = backend_dir()
    python = os.path.join(backend, ".venv", "Scripts", "python.exe")
    if not os.path.isfile(python):
        python = os.path.join(backend, ".venv", "bin", "python")
    truthy(os.path.isfile(python), f"the backend's interpreter exists ({python})")
    code = (
        "import sys, json\n"
        "from app.services.pdf_builder import _ink_metrics\n"
        "print(json.dumps(_ink_metrics(sys.stdin.buffer.read())))\n"
    )
    done = subprocess.run([python, "-c", code], input=png, cwd=backend, capture_output=True, timeout=60)
    truthy(done.returncode == 0, f"_ink_metrics ran ({done.stderr.decode(errors='replace')[-400:]})")
    return tuple(json.loads(done.stdout.decode().strip()))


def pdf_signature_rect(bbox: dict, offset: float, scale: float, metrics) -> tuple[float, float, float, float]:
    """`_draw_signature` for a FREE row: line = bbox bottom, global offset + scale, no dx."""
    width, height, _, _, _, ink_bottom = metrics
    box_w, box_h = bbox["width"] * scale, bbox["height"] * scale
    fit = min(box_w / width, box_h / height)
    draw_w, draw_h = width * fit, height * fit
    below = (height - ink_bottom) * fit
    target = bbox["y"] + bbox["height"] - 1.0 - offset
    return bbox["x"], target + below - draw_h, draw_w, draw_h


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def clean(mock: MockApi) -> None:
    equals(mock.page_errors, [], "no uncaught error")
    equals(mock.unmocked, [], "no unmocked API call")
    warnings = [text for text in mock.console_errors if "Warning:" in text and "React Router" not in text]
    equals(warnings, [], "no React warning")


def storage_key(mock: MockApi) -> str:
    return f"swm.annotations.{mock.contract_id}"


def open_doc(page, mock: MockApi, panel: str = "edit", storage: dict[str, str] | None = None) -> None:
    if storage:
        goto_blank(page, mock)
        for key, value in storage.items():
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


def origin(page) -> dict:
    box = page.locator('.pdf-page[data-page-number="1"]').bounding_box()
    truthy(box is not None, "page 1 has a box")
    return box


def px(page, x_pt: float, y_pt: float) -> tuple[float, float]:
    box = origin(page)
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
        box = origin(page)
        scale = box["width"] / PAGE_W
    return box["x"] + x_pt * scale, box["y"] + y_pt * scale


def page_top(page) -> float:
    return float(page.evaluate(PAGE_TOP_JS))


def stored(page, mock: MockApi) -> list:
    raw = page.evaluate("(k) => localStorage.getItem(k)", storage_key(mock))
    return json.loads(raw) if raw else []


def wait_stored(page, mock: MockApi, count: int) -> None:
    page.wait_for_function(
        "([k, n]) => { const raw = localStorage.getItem(k); return (raw ? JSON.parse(raw).length : 0) === n; }",
        arg=[storage_key(mock), count],
        timeout=10000,
    )


def note_count(page) -> int:
    return page.locator('[data-testid="editor-note"]').count()


def raise_note(page, mock: MockApi) -> None:
    """Put a note on screen: the once-only whiteout notice, made new again."""
    page.evaluate("(k) => localStorage.removeItem(k)", WHITEOUT_NOTICE_KEY)
    arm_tool(page, "whiteout")
    before = len(stored(page, mock))
    ax, ay = px(page, 440, 110)
    bx, by = px(page, 480, 125)
    page.mouse.move(ax, ay)
    page.mouse.down()
    page.mouse.move(bx, by, steps=4)
    page.mouse.up()
    wait_stored(page, mock, before + 1)
    page.wait_for_selector('[data-testid="editor-note"]', timeout=5000)


def watched_drag(page, start: tuple[float, float], end: tuple[float, float], *, steps: int = 10,
                 pause_ms: int = 0) -> tuple[list[float], list[int], float]:
    """Press at `start`, move to `end` in `steps`, release. Returns the page's top after
    every event up to the release, how many error banners were on screen after each
    one, and the page's top once the press is over."""
    ax, ay = px(page, *start)
    bx, by = px(page, *end)
    page.mouse.move(ax, ay)
    tops = [page_top(page)]
    banners = [page.locator('[data-testid="editor-error"]').count()]
    page.mouse.down()
    tops.append(page_top(page))
    banners.append(page.locator('[data-testid="editor-error"]').count())
    for step in range(1, steps + 1):
        t = step / steps
        page.mouse.move(ax + (bx - ax) * t, ay + (by - ay) * t)
        if pause_ms:
            page.wait_for_timeout(pause_ms)
        tops.append(page_top(page))
        banners.append(page.locator('[data-testid="editor-error"]').count())
    page.mouse.up()
    return tops, banners, page_top(page)


def still(tops: list[float], what: str) -> None:
    spread = max(tops) - min(tops)
    truthy(spread < 0.01, f"{what}: the page never moved (tops {sorted(set(round(t, 2) for t in tops))})")


# --------------------------------------------------------------------------
# 1. The page never moves under a drag
# --------------------------------------------------------------------------

@test("a_note_on_screen_never_moves_the_page_during_a_drag_all_four_drag_tools")
def _(page, mock: MockApi) -> None:
    open_doc(page, mock)
    top_before_note = page_top(page)
    raise_note(page, mock)
    approx(page_top(page), top_before_note, 0.01, "a note appearing does not move the page either")
    box = page.locator('[data-testid="editor-note-box"]').bounding_box()
    stage = page.locator(".pdf-viewer .pdf-stage").bounding_box()
    truthy(box is not None and stage is not None and box["y"] >= stage["y"] - 1,
           f"the note floats over the pages ({box}, stage {stage})")

    cases = [
        # (tool, start pt, end pt) -- the live case first: a 19 pt whiteout.
        ("whiteout", (80, 200), (180, 219)),
        ("rect", (80, 260), (200, 300)),
        ("line", (250, 200), (400, 240)),
        ("ink", (250, 300), (400, 330)),
    ]
    for tool, start, end in cases:
        if note_count(page) == 0:
            raise_note(page, mock)
        equals(note_count(page), 1, f"{tool}: a note is on screen before the drag")
        arm_tool(page, tool)
        page.wait_for_timeout(150)
        count = len(stored(page, mock))
        tops, _, after = watched_drag(page, start, end)
        wait_stored(page, mock, count + 1)
        equals(note_count(page), 0, f"{tool}: the drag cleared the note (the case that used to jump)")
        still(tops + [after], tool)
        item = stored(page, mock)[-1]
        equals(item["kind"], tool, f"{tool}: stored")
        (x0, y0), (x1, y1) = start, end
        if tool in ("whiteout", "rect"):
            approx(item["x_pt"], x0, 0.5, f"{tool}: x under the pointer")
            approx(item["y_pt"], y0, 0.5, f"{tool}: y under the pointer")
            approx(item["w_pt"], x1 - x0, 0.5, f"{tool}: w as dragged")
            approx(item["h_pt"], y1 - y0, 0.5, f"{tool}: h as dragged (was +41.5 pt at 178 %)")
        elif tool == "line":
            approx(item["x1_pt"], x0, 0.5, "line: start x")
            approx(item["y1_pt"], y0, 0.5, "line: start y")
            approx(item["x2_pt"], x1, 0.5, "line: end x")
            approx(item["y2_pt"], y1, 0.5, "line: end y")
        else:
            points = [point for path in item["paths"] for point in path]
            approx(points[0]["x_pt"], x0, 0.5, "ink: first point x")
            approx(points[0]["y_pt"], y0, 0.5, "ink: first point y")
            approx(points[-1]["x_pt"], x1, 0.5, "ink: last point x")
            approx(points[-1]["y_pt"], y1, 0.5, "ink: last point y")
    clean(mock)


#: Holds every DELETE of a signature back ~0.9 s in the browser, so its answer
#: lands while the next drag is still held.
SLOW_DELETE = """
(() => {
  const original = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input.url;
    const method = (init && init.method) || (typeof input === 'string' ? 'GET' : input.method) || 'GET';
    const response = await original(input, init);
    if (method === 'DELETE' && /\\/signatures\\//.test(url)) {
      await new Promise((resolve) => setTimeout(resolve, 900));
    }
    return response;
  };
})();
"""


@test("an_error_banner_arriving_mid_drag_waits_until_the_press_is_over")
def _(page, mock: MockApi) -> None:
    mock.free_rows = [{
        "id": "zz-geo-free", "contract_id": mock.contract_id, "signer_name": None, "signer_email": None,
        "signer_role": "contractor", "page_number": 1,
        "bbox": {"x": 300, "y": 420, "width": 180, "height": 34},
        "field_id": None, "signed_at": "2026-09-23T08:00:00+00:00",
    }]
    page.add_init_script(SLOW_DELETE)

    def refuse(route):
        if route.request.method == "DELETE":
            return route.fulfill(status=500, content_type="application/json",
                                 body=json.dumps({"error": "zz_refused", "message": "ZZ remove refused",
                                                  "details": {}}))
        return route.fallback()

    open_doc(page, mock, storage={WHITEOUT_NOTICE_KEY: "1"})
    page.route(f"**/api/contracts/{mock.contract_id}/signatures/zz-geo-free", refuse)
    arm_tool(page, "select")
    page.locator('[data-testid="ann-sig-remove-zz-geo-free"]').click()
    arm_tool(page, "whiteout")
    equals(page.locator('[data-testid="editor-error"]').count(), 0, "no error yet (the answer is held back)")
    count = len(stored(page, mock))
    tops, banners, _ = watched_drag(page, (80, 200), (180, 219), steps=12, pause_ms=120)
    truthy(banners[0] == 0 and max(banners) >= 1, f"the error arrived DURING the drag ({banners})")
    still(tops, "error mid-drag")
    wait_stored(page, mock, count + 1)
    item = stored(page, mock)[-1]
    approx(item["y_pt"], 200, 0.5, "y under the pointer")
    approx(item["h_pt"], 19, 0.5, "h as dragged")
    page.wait_for_timeout(200)
    error = page.locator('[data-testid="editor-error"]')
    equals(error.count(), 1, "the error is shown once the press is over")
    truthy(error.is_visible(), "and visible (it was only held back)")
    truthy(page_top(page) > tops[0] + 10, f"only NOW does the page make room ({tops[0]} → {page_top(page)})")
    equals(page.locator('[data-testid="workspace-banners"]').get_attribute("data-gesture-lock"), None,
           "the freeze is lifted")
    clean_errors = [e for e in mock.console_errors if "500" not in e and "Failed to load resource" not in e]
    mock.console_errors = clean_errors
    clean(mock)


# --------------------------------------------------------------------------
# 2. Free signatures and initials are previewed where the PDF prints them
# --------------------------------------------------------------------------

FREE_JS = """
(id) => {
  const host = document.querySelector('.pdf-page[data-page-number="1"]');
  if (!host) return null;
  const page = host.getBoundingClientRect();
  const scale = page.width / 612;
  const img = document.querySelector(`.swm-sig[data-signature-id="${id}"] img.swm-sig__image`);
  if (!img || !img.complete || !img.naturalWidth) return null;
  const b = img.getBoundingClientRect();
  return { x: (b.left - page.left) / scale, y: (b.top - page.top) / scale,
           w: b.width / scale, h: b.height / scale, rect: img.dataset.rectPt,
           inkBottom: Number(img.dataset.inkBottomPt) };
}
"""


@test("a_free_signature_and_an_initial_are_drawn_where_the_pdf_prints_them")
def _(page, mock: MockApi) -> None:
    offset, scale = 3.0, 1.2
    rows = {
        "zz-geo-sig": {"x": 100, "y": 500, "width": 180, "height": 34},
        "zz-geo-init": {"x": 400, "y": 600, "width": 60, "height": 20},
    }
    mock.free_rows = [{
        "id": row_id, "contract_id": mock.contract_id, "signer_name": None, "signer_email": None,
        "signer_role": "contractor", "page_number": 1, "bbox": bbox, "field_id": None,
        "signed_at": "2026-09-23T08:00:00+00:00",
    } for row_id, bbox in rows.items()]
    metrics = builder_ink_metrics(synthetic_signature_png())
    truthy(metrics[5] < metrics[1], f"the served image has padding under its ink (so the rule is tested) {metrics}")
    open_doc(page, mock, panel="sign", storage={
        f"swm.layout.{mock.contract_id}": json.dumps({"signature_offset_pt": offset, "signature_scale": scale}),
    })
    for row_id, bbox in rows.items():
        page.wait_for_function(f"() => ({FREE_JS})('{row_id}') !== null", timeout=15000)
        drawn = page.evaluate(FREE_JS, row_id)
        x, y, w, h = pdf_signature_rect(bbox, offset, scale, metrics)
        approx(drawn["x"], x, 0.05, f"{row_id}: left = the PDF's")
        approx(drawn["y"], y, 0.05, f"{row_id}: top = the PDF's")
        approx(drawn["w"], w, 0.05, f"{row_id}: width = the PDF's (× {scale})")
        approx(drawn["h"], h, 0.05, f"{row_id}: height = the PDF's")
        ink_bottom = y + metrics[5] * (h / metrics[1])
        approx(ink_bottom, bbox["y"] + bbox["height"] - 1.0 - offset, 0.05,
               f"{row_id}: the INK bottom stands on bbox bottom − 1 − offset")
        approx(drawn["inkBottom"], bbox["y"] + bbox["height"] - 1.0 - offset, 0.01, f"{row_id}: data-ink-bottom-pt")
    # A change of the global offset / size moves the preview with it (the PDF follows the same numbers).
    page.evaluate(
        "(k) => localStorage.setItem(k, JSON.stringify({signature_offset_pt: 0, signature_scale: 1}))",
        f"swm.layout.{mock.contract_id}",
    )
    page.reload()
    page.wait_for_function("() => (%s)('zz-geo-init') !== null" % FREE_JS, timeout=20000)
    drawn = page.evaluate(FREE_JS, "zz-geo-init")
    x, y, w, h = pdf_signature_rect(rows["zz-geo-init"], 0.0, 1.0, metrics)
    approx(drawn["y"], y, 0.05, "neutral layout: top = the PDF's")
    approx(drawn["h"], h, 0.05, "neutral layout: height = the PDF's")
    clean(mock)


def draw_wide_initials(page) -> None:
    """A low, wide stroke: the kind whose server crop is far from the pad's 3:1."""
    pad = page.locator(".signature-pad__canvas").bounding_box()
    truthy(pad is not None, "the drawing pad is open")
    mid = pad["y"] + pad["height"] / 2
    page.mouse.move(pad["x"] + 30, mid)
    page.mouse.down()
    page.mouse.move(pad["x"] + 150, mid - 8, steps=8)
    page.mouse.move(pad["x"] + 300, mid + 6, steps=8)
    page.mouse.move(pad["x"] + 420, mid - 4, steps=8)
    page.mouse.up()


@test("an_initials_box_has_the_ratio_of_the_image_the_server_keeps")
def _(page, mock: MockApi) -> None:
    mock.place_echo = True
    open_doc(page, mock, panel="sign")
    arm_tool(page, "initials")
    ax, ay = px(page, 450, 600)
    page.mouse.click(ax, ay)
    page.wait_for_selector(".modal__panel", timeout=15000)
    draw_wide_initials(page)
    page.get_by_role("button", name="Apply initials").click()
    page.wait_for_function(
        "() => document.querySelectorAll('.swm-sig[data-signature-kind=\"initials\"]').length === 1",
        timeout=20000,
    )
    body = mock.place_bodies[0]
    stored_w, stored_h = server_stored_size(body["image_data"])
    raw_w, raw_h = 480, 160
    bbox = body["bbox"]
    truthy(stored_w / stored_h > (raw_w / raw_h) * 1.5,
           f"the server's crop is much wider than the pad ({stored_w} × {stored_h})")
    approx(bbox["width"] / bbox["height"], stored_w / stored_h, 0.02 * stored_w / stored_h,
           f"the box has the STORED image's ratio ({stored_w} × {stored_h}), not the pad's 3:1")
    truthy(bbox["width"] <= 60.01 and bbox["height"] <= 24.01, f"inside 60 × 24 pt ({bbox})")
    truthy(abs(bbox["width"] - 60) < 0.01 or abs(bbox["height"] - 24) < 0.01, f"as large as the box allows ({bbox})")
    approx(bbox["x"] + bbox["width"] / 2, 450, 0.6, "centred on the click (x)")
    approx(bbox["y"] + bbox["height"] / 2, 600, 0.6, "centred on the click (y)")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
