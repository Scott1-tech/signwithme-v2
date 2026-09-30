"""UI driver of the acceptance suite -- runs under the SYSTEM Python (Playwright):

    "C:\\Program Files\\Python312\\python.exe" acc_ui.py <scenario> <args.json> <out.json>

One scenario = one isolated headless Chromium (fresh context, 1 device px per
CSS px) against the LIVE UI (`SWM_UI`, default http://10.31.0.58:5173). The
token lives only inside the browser context (localStorage) -- never on disk.

A scenario never judges PDFs (that needs the backend venv's libraries): it
drives the interface, measures the DOM, saves downloads, and returns JSON:
    {"checks": [[name, ok, detail], ...], "pending": [reason, ...], "facts": {...},
     "files": {...}, "errors": [...], "http_errors": [...]}
The caller (`acc_cases.py`, venv) adds the PDF evidence.

Geometry: PDF points, top-left origin; a page's CSS box / 612 = the scale.
Pointer positions are rounded to whole CSS pixels and the EXPECTED point is
computed back from the rounded pixel, so a ±0.5 pt check never measures the
driver's own rounding.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import traceback

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from acc_api import UI_BASE, Api, login  # noqa: E402

PAGE_W = 612.0


class Out:
    def __init__(self):
        self.data = {"checks": [], "pending": [], "facts": {}, "files": {}, "errors": [], "http_errors": []}

    def check(self, name, ok, detail=None):
        self.data["checks"].append([name, bool(ok), detail])
        print(("PASS " if ok else "FAIL ") + name + (f"  {json.dumps(detail, default=str)[:300]}" if detail is not None else ""),
              flush=True)
        return bool(ok)

    def pending(self, reason):
        self.data["pending"].append(reason)
        print("PENDING " + reason, flush=True)

    def fact(self, key, value):
        self.data["facts"][key] = value


# -- browser -----------------------------------------------------------------------------------

class UI:
    def __init__(self, p, token, out: Out, shots: str, viewport=(1600, 1200)):
        self.out = out
        self.shots = shots
        self.browser = p.chromium.launch(headless=True)
        self.context = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                                device_scale_factor=1, accept_downloads=True)
        self.context.add_init_script(
            "(() => { try { localStorage.setItem('csa.token', %s); localStorage.setItem('swm.autoFill', 'false');"
            " localStorage.setItem('swm.thumbs', 'true'); } catch (_) {} })();" % json.dumps(token))
        self.page = self.new_page()

    def new_page(self):
        page = self.context.new_page()
        page.set_default_timeout(30000)
        page.on("pageerror", lambda exc: self.out.data["errors"].append(str(exc)[:300]))
        page.on("console", lambda m: self.out.data["errors"].append(m.text[:300])
                if m.type == "error" and "React Router" not in m.text else None)
        page.on("response", lambda r: self.out.data["http_errors"].append(
            f"{r.status} {r.request.method} {r.url.replace(UI_BASE, '')}")
            if "/api/" in r.url and r.status >= 400 else None)
        page.on("dialog", lambda d: d.accept())
        return page

    def close(self):
        try:
            self.browser.close()
        except Exception:  # noqa: BLE001
            pass

    def shot(self, name):
        path = os.path.join(self.shots, f"{name}.png")
        try:
            self.page.screenshot(path=path)
            self.out.data["files"][f"shot_{name}"] = path
        except Exception:  # noqa: BLE001
            pass

    # -- workspace --------------------------------------------------------------------------
    def open_ws(self, cid, panel="edit", synced=True):
        page = self.page
        page.goto(f"{UI_BASE}/contracts/{cid}/{panel}")
        page.wait_for_selector('[data-testid="workspace"][data-editor="ready"]', timeout=90000)
        page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=90000)
        if synced:
            try:
                page.wait_for_selector('[data-testid="workspace"][data-markup-sync="synced"]', timeout=30000)
            except Exception:  # noqa: BLE001
                pass
        page.wait_for_timeout(700)

    def exists(self, selector) -> bool:
        return self.page.locator(selector).count() > 0

    def close_menus(self):
        self.page.keyboard.press("Escape")
        self.page.mouse.click(3, 3)
        self.page.wait_for_timeout(100)

    def arm(self, tool, menu=None) -> bool:
        """Arm a toolbar tool; False when the live toolbar has no such tool."""
        page = self.page
        self.close_menus()
        if menu:
            toggle = page.locator(f'[data-testid="toolgroup-{menu}-toggle"]')
            if toggle.count() == 0:
                return False
            toggle.first.click()
            page.wait_for_timeout(150)
        item = page.locator(f'[data-testid="tool-{tool}"]')
        if item.count() == 0:
            self.close_menus()
            return False
        item.first.click()
        page.wait_for_timeout(200)
        return True

    def tools(self) -> list[str]:
        """Every tool id the live toolbar offers (menus opened one by one)."""
        page = self.page
        found = set()
        self.close_menus()
        for el in page.locator('[data-testid^="tool-"]').all():
            found.add(el.get_attribute("data-testid"))
        for tog in page.locator('[data-testid$="-toggle"][data-testid^="toolgroup-"]').all():
            try:
                tog.click()
                page.wait_for_timeout(150)
                for el in page.locator('[data-testid^="tool-"]').all():
                    found.add(el.get_attribute("data-testid"))
            except Exception:  # noqa: BLE001
                pass
            self.close_menus()
        return sorted(t[5:] for t in found if t and t.startswith("tool-") and t not in ("tool-disabled-reason",))

    # -- geometry ---------------------------------------------------------------------------
    def page_box(self, number=1):
        return self.page.locator(f'.pdf-page[data-page-number="{number}"]').bounding_box()

    def scale(self, number=1):
        return self.page_box(number)["width"] / PAGE_W

    def reveal(self, y_pt, number=1, margin=140):
        """Scroll the stage so the point's row is well inside the viewport."""
        page = self.page
        loc = page.locator(f'.pdf-page[data-page-number="{number}"]')
        loc.scroll_into_view_if_needed()
        for _ in range(4):
            box = loc.bounding_box()
            s = box["width"] / PAGE_W
            y = box["y"] + y_pt * s
            stage = page.evaluate("() => { const s = document.querySelector('.pdf-stage');"
                                  " const r = s.getBoundingClientRect(); return {top: r.top, bottom: r.bottom}; }")
            if stage["top"] + margin <= y <= stage["bottom"] - margin:
                return
            delta = y - (stage["top"] + stage["bottom"]) / 2
            page.evaluate("(d) => { document.querySelector('.pdf-stage').scrollTop += d; }", delta)
            page.wait_for_timeout(250)

    def to_screen(self, x_pt, y_pt, number=1):
        """Whole-pixel screen point for (x_pt, y_pt) and the pt that pixel really is."""
        box = self.page_box(number)
        s = box["width"] / PAGE_W
        sx, sy = round(box["x"] + x_pt * s), round(box["y"] + y_pt * s)
        return (sx, sy), ((sx - box["x"]) / s, (sy - box["y"]) / s)

    def drag_pt(self, x1, y1, x2, y2, number=1):
        self.reveal((y1 + y2) / 2, number)
        (ax, ay), p1 = self.to_screen(x1, y1, number)
        (bx, by), p2 = self.to_screen(x2, y2, number)
        m = self.page.mouse
        m.move(ax, ay)
        m.down()
        m.move(ax + (3 if bx >= ax else -3), ay + (3 if by >= ay else -3))
        m.move(bx, by, steps=12)
        m.up()
        self.page.wait_for_timeout(300)
        return p1, p2

    def click_pt(self, x, y, number=1):
        self.reveal(y, number)
        (sx, sy), p = self.to_screen(x, y, number)
        self.page.mouse.click(sx, sy)
        self.page.wait_for_timeout(300)
        return p

    def zoom_label(self) -> int | None:
        loc = self.page.locator(".pdf-toolbar__zoom")
        if loc.count() == 0:
            return None
        try:
            return int(loc.first.inner_text().strip().rstrip("%"))
        except ValueError:
            return None

    def set_zoom(self, target_pct: int) -> dict:
        """Fit-to-width after resizing the viewport until the zoom label reads
        the target (the label is the rounded %; the true scale is measured)."""
        page = self.page
        vw = dict(page.viewport_size)
        for _ in range(7):
            # «Fit» may be clipped out of a narrow viewer strip: a DOM click still presses it
            page.evaluate("() => { const b = [...document.querySelectorAll('.pdf-toolbar button')]"
                          ".find((x) => x.textContent.trim() === 'Fit'); if (b) b.click(); }")
            page.wait_for_timeout(450)
            cur = self.zoom_label()
            if cur is None or cur == target_pct:
                break
            vw["width"] = int(max(900, min(3200, vw["width"] + (target_pct - cur) / 100.0 * PAGE_W)))
            page.set_viewport_size(vw)
            page.wait_for_timeout(500)
        page.wait_for_timeout(400)
        return {"label": self.zoom_label(), "scale": round(self.scale(1), 5), "viewport": vw["width"]}

    # -- session ------------------------------------------------------------------------------
    def start_session(self):
        page = self.page
        if self.exists('[data-testid="session-discard"]'):
            # a draft left by an earlier tab: this scenario starts clean
            page.locator('[data-testid="session-discard"]').first.click()
            page.wait_for_timeout(1500)
        if page.locator('[data-testid="workspace"][data-session="edit"]').count() == 0:
            page.locator('[data-testid="editor-edit-pdf"]').click()
        page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=20000)
        self.wait_saved()

    def wait_saved(self, timeout=30000):
        self.page.wait_for_selector('[data-testid="editor-save-status"][data-status="saved"]', timeout=timeout)

    def save_session(self):
        page = self.page
        self.wait_saved()
        page.locator('[data-testid="editor-save"]').click()
        try:
            page.wait_for_selector('[data-testid="signatures-dialog-confirm"]', timeout=2500)
            page.locator('[data-testid="signatures-dialog-confirm"]').click()
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_selector('[data-testid="workspace"][data-session="view"]', timeout=90000)
        page.wait_for_timeout(1500)
        banner = page.locator('[data-testid="session-committed"]')
        return banner.inner_text() if banner.count() else None

    def arm_edit_text(self, number=1):
        if not self.arm("edit-text", "text"):
            raise RuntimeError("no Edit text tool")
        self.page.wait_for_selector(f'[data-testid="text-block-layer-{number}"][data-state="ready"]', timeout=30000)
        self.page.wait_for_timeout(300)

    def blocks(self, number=1):
        return self.page.evaluate(
            """(n) => { const pg = document.querySelector(`.pdf-page[data-page-number="${n}"]`).getBoundingClientRect();
                 const s = pg.width / 612;
                 return [...document.querySelectorAll(`[data-testid="text-block-layer-${n}"] .tb-block`)].map((b) => {
                   const r = b.getBoundingClientRect();
                   return {id: b.dataset.blockId, text: b.dataset.text, edited: b.dataset.edited === 'true',
                           x: +b.dataset.xPt, y: +b.dataset.yPt, w: +b.dataset.wPt, h: +b.dataset.hPt,
                           baseline: +b.dataset.baselinePt, family: b.dataset.family, size: +b.dataset.size,
                           dom: {x: (r.left - pg.left) / s, y: (r.top - pg.top) / s, w: r.width / s, h: r.height / s}}; }); }""",
            number)

    def open_block(self, needle, number=1, fraction=0.97):
        blocks = self.blocks(number)
        target = next((b for b in blocks if needle in (b["text"] or "")), None)
        if target is None:
            raise RuntimeError(f"no text block holds {needle!r}: {[b['text'] for b in blocks]}")
        loc = self.page.locator(f'[data-testid="text-block-layer-{number}"] .tb-block[data-block-id="{target["id"]}"]')
        loc.scroll_into_view_if_needed()
        box = loc.bounding_box()
        self.page.mouse.click(box["x"] + box["width"] * fraction, box["y"] + box["height"] / 2)
        self.page.wait_for_selector('[data-testid="text-block-editor"]', timeout=10000)
        return target

    def after_text_commit(self):
        page = self.page
        self.wait_saved()
        try:
            page.wait_for_function("() => !document.querySelector('[data-testid=\"text-block-pending\"]')", timeout=20000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(500)


def api_for(token, cid) -> Api:
    return Api(token, cid)


# -- scenarios ----------------------------------------------------------------------------------
# Each: fn(ui: UI, api: Api, args: dict, out: Out). Raise = FAIL with the traceback.

def sc_probe(ui, api, args, out):
    ui.open_ws(args["cid"])
    tools = ui.tools()
    out.fact("tools", tools)
    feats = {
        "edit_session": ui.exists('[data-testid="editor-edit-pdf"]'),
        "pages_panel": ui.exists('[data-testid^="page-insert-"]') or ui.exists('[data-testid^="page-item-"]'),
        "thumbs": ui.exists('[data-testid="pdf-thumbs"]') or ui.exists('[data-testid="pdf-thumbs-toggle"]'),
        "search": ui.exists('[data-testid="pdf-search-input"]'),
        "zoom": ui.zoom_label() is not None,
    }
    out.fact("features", feats)
    out.check("workspace opens with the editor toolbar", len(tools) > 5, tools)


def sc_edit_text(ui, api, args, out):
    """1: Edit PDF -> Edit text -> replace the tail of a line -> Enter -> Save.
    Also: the server's text frames sit on their bbox at 100 % and ~209 % (±0.5 pt)."""
    page = ui.page
    ui.open_ws(args["cid"])
    ui.start_session()
    ui.arm_edit_text()
    zooms = {}
    for target in args.get("zooms", [100, 209]):
        z = ui.set_zoom(target)
        page.wait_for_selector('[data-testid="text-block-layer-1"][data-state="ready"]', timeout=30000)
        page.wait_for_timeout(500)
        bl = ui.blocks(1)
        dev = max((max(abs(b["dom"]["x"] - b["x"]), abs(b["dom"]["y"] - b["y"]), abs(b["dom"]["w"] - b["w"]),
                       abs(b["dom"]["h"] - b["h"])) for b in bl), default=None)
        zooms[target] = {"zoom": z, "blocks": len(bl), "max_dev_pt": round(dev, 3) if dev is not None else None}
        out.check(f"text frames on their server bbox at {z['label']}% (±0.5 pt)", dev is not None and dev <= 0.5, zooms[target])
        ui.shot(f"t01_frames_{target}")
    out.fact("frames", zooms)
    out.fact("model_blocks", ui.blocks(1))
    ui.set_zoom(100)
    ui.arm_edit_text()
    old, new = args["old"], args["new"]
    ui.open_block(old)
    page.keyboard.press("End")
    for _ in range(len(old)):
        page.keyboard.press("Shift+ArrowLeft")
    page.keyboard.type(new)
    page.wait_for_timeout(200)
    page.keyboard.press("Enter")
    ui.after_text_commit()
    _s, draft = api.get(f"{api.D}/draft")
    ops = draft.get("ops", []) if isinstance(draft, dict) else []
    out.fact("draft_ops", ops)
    out.check("one text.replace op in the draft (no whiteout)",
              len(ops) == 1 and ops[0]["kind"] == "text.replace" and "whiteout" not in json.dumps(draft), [o["kind"] for o in ops])
    ui.shot("t01_edited")
    banner = ui.save_session()
    out.fact("banner", banner)
    out.check("Save changes -> new version", bool(banner) and "version" in banner.lower(), banner)
    doc = api.document()
    out.fact("version_no", doc["current_version"]["version_no"])


def sc_delete_text(ui, api, args, out):
    """2: an emptied block is a text.delete."""
    page = ui.page
    ui.open_ws(args["cid"])
    ui.start_session()
    ui.arm_edit_text()
    ui.open_block(args["text"])
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.keyboard.press("Enter")
    ui.after_text_commit()
    _s, draft = api.get(f"{api.D}/draft")
    kinds = [o["kind"] for o in (draft.get("ops", []) if isinstance(draft, dict) else [])]
    out.check("draft holds a text.delete", kinds == ["text.delete"], kinds)
    ui.shot("t02_deleted")
    out.fact("banner", ui.save_session())
    out.fact("version_no", api.current_no())


def sc_textbox(ui, api, args, out):
    """3: Text ▾ Add text box: a multi-line (Shift+Enter) box, wrapped."""
    page = ui.page
    ui.open_ws(args["cid"])
    if not ui.arm("textbox", "text"):
        out.pending("Text ▾ «Add text box» (textbox) is not in the live toolbar")
        return
    before = {e["id"] for e in api.markup()["elements"]}
    x, y = args["at"]
    p = ui.click_pt(x, y, args.get("page", 1))
    page.locator('[data-testid="textbox-editor"]').wait_for(timeout=10000)
    first, second = args["paragraphs"]
    page.keyboard.type(first)
    page.keyboard.press("Shift+Enter")
    page.keyboard.type(second)
    page.keyboard.press("Enter")
    page.wait_for_timeout(2500)
    page.wait_for_selector('[data-testid="workspace"][data-markup-sync="synced"]', timeout=30000)
    new = [e for e in api.markup()["elements"] if e["id"] not in before]
    out.fact("clicked_pt", p)
    out.fact("elements", new)
    tb = next((e for e in new if e["kind"] == "textbox"), None)
    out.check("one textbox element saved on the server", tb is not None, [e["kind"] for e in new])
    if tb:
        text = "".join(r["text"] for r in tb["runs"])
        out.check("both paragraphs, split by a line break", first in text and second in text and "\n" in text, text)
        out.check("placed at the click (±0.5 pt)", abs(tb["x_pt"] - p[0]) <= 0.5 and abs(tb["y_pt"] - p[1]) <= 0.5,
                  {"box": [tb["x_pt"], tb["y_pt"]], "click": p})
        lines = page.evaluate(
            """(id) => { const g = document.querySelector(`[data-testid="ann-textbox-${id}"]`);
                 return g ? [...g.querySelectorAll(':scope > g')].length : null; }""", tb["id"])
        out.fact("dom_lines", lines)
        out.check("drawn wrapped on screen (>= 3 lines for 2 paragraphs)", lines is not None and lines >= 3, lines)
    ui.shot("t03_textbox")


def sc_style(ui, api, args, out):
    """4: the floating text toolbar: bold, size, font, colour -> text.replace carries them."""
    page = ui.page
    ui.open_ws(args["cid"])
    ui.start_session()
    ui.arm_edit_text()
    ui.open_block(args["text"])
    page.locator('[data-testid="text-bold"]').click()
    page.locator('[data-testid="text-size-toggle"]').click()
    page.locator(f'[data-testid="text-size-{args["size"]}"]').click()
    page.locator('[data-testid="text-font"]').click()
    fam = page.locator(f'[data-testid="text-font-{args["font"]}"]')
    if fam.count() == 0:
        page.locator('[data-testid="text-font-search"]').fill(args["font_search"])
    page.locator(f'[data-testid="text-font-{args["font"]}"]').click()
    page.locator('[data-testid="text-color"]').click()
    page.locator(f'[data-testid="text-color-{args["color"]}"]').click()
    ed = page.locator('[data-testid="text-block-editor"]')
    attrs = {k: ed.get_attribute(f"data-{k}") for k in ("bold", "italic", "size", "family", "color")}
    out.fact("editor_attrs", attrs)
    out.check("editor shows bold / size / font / colour",
              attrs["bold"] == "true" and attrs["size"] == str(args["size"]) and attrs["family"] == args["font"], attrs)
    ui.shot("t04_toolbar")
    ed.press("Enter")
    ui.after_text_commit()
    _s, draft = api.get(f"{api.D}/draft")
    runs = draft["ops"][0]["runs"] if isinstance(draft, dict) and draft.get("ops") else []
    out.fact("runs", runs)
    out.check("op carries the whole style", bool(runs) and runs[0]["font"] == args["font"] and runs[0]["size_pt"] == args["size"]
              and runs[0].get("bold") is True, runs[:1])
    out.fact("banner", ui.save_session())
    out.fact("version_no", api.current_no())


def sc_move_dup(ui, api, args, out):
    """5: ✥ Move a block (text.replace with a moved box) and ⧉ Duplicate it."""
    page = ui.page
    ui.open_ws(args["cid"])
    ui.start_session()
    ui.arm_edit_text()
    target = ui.open_block(args["text"])
    page.locator('[data-testid="text-move"]').click()
    handle = page.locator('[data-testid="text-block-move-handle"]')
    handle.wait_for(timeout=5000)
    hb = handle.bounding_box()
    s = ui.scale(1)
    dx_px = round(args["dx_pt"] * s)
    sx, sy = round(hb["x"] + hb["width"] / 2), round(hb["y"] + hb["height"] / 2)
    page.mouse.move(sx, sy)
    page.mouse.down()
    page.mouse.move(sx + 5, sy, steps=2)
    page.mouse.move(sx + dx_px, sy, steps=12)
    page.mouse.up()
    page.wait_for_timeout(300)
    moved_pt = dx_px / s
    page.locator('[data-testid="text-block-editor"]').press("Enter")
    ui.after_text_commit()
    _s, draft = api.get(f"{api.D}/draft")
    op = draft["ops"][0] if isinstance(draft, dict) and draft.get("ops") else None
    out.fact("op", op)
    out.fact("block_before", target)
    ok = bool(op) and op["kind"] == "text.replace" and op.get("box") is not None
    out.check("move = text.replace with a moved box", ok, op and op.get("box"))
    if ok:
        out.check("box moved by the drag (±0.5 pt)", abs(op["box"]["x_pt"] - (target["x"] + moved_pt)) <= 0.5,
                  {"box_x": op["box"]["x_pt"], "expected": round(target["x"] + moved_pt, 3)})
    out.fact("moved_pt", moved_pt)
    ui.shot("t05_moved")
    banner = ui.save_session()
    out.fact("banner", banner)
    # Duplicate (view -> edit text -> block -> ⧉): a new annotation, one undo step
    ui.start_session()
    ui.arm_edit_text()
    before = {e["id"] for e in api.markup()["elements"]}
    ui.open_block(args["dup_text"])
    page.locator('[data-testid="text-duplicate"]').click()
    page.wait_for_timeout(500)
    page.keyboard.press("Escape")
    rows = page.locator('[data-testid^="object-ann-"]').count()
    out.check("duplicate: a new object in the Objects list", rows >= 1, rows)
    ui.shot("t05_duplicate")
    out.fact("banner_dup", ui.save_session())
    page.wait_for_timeout(1500)
    after = [e for e in api.markup()["elements"] if e["id"] not in before]
    out.fact("dup_elements", after)
    out.check("duplicate saved as markup (text / textbox) with the block's text",
              any(args["dup_text"] in json.dumps(e) for e in after), [e["kind"] for e in after])
    out.fact("version_no", api.current_no())


def sc_images(ui, api, args, out):
    """6: gallery picture placed + removed, a stamp placed + removed, an original image removed."""
    page = ui.page
    ui.open_ws(args["cid"])
    before = {e["id"] for e in api.markup()["elements"]}
    # picture from the gallery
    ui.close_menus()
    tog = page.locator('[data-testid="toolgroup-images-toggle"]')
    placed = {}
    if tog.count() and page.locator('[data-testid="gallery-file"]').count() == 0:
        tog.click()
        page.wait_for_timeout(200)
    if page.locator('[data-testid="gallery-file"]').count():
        n0 = page.locator('[data-testid="gallery-thumbs"] .ws-thumb__pick').count()
        page.locator('[data-testid="gallery-file"]').set_input_files(args["png"])
        page.wait_for_function(
            "(n) => document.querySelectorAll('[data-testid=\"gallery-thumbs\"] .ws-thumb__pick').length > n", arg=n0, timeout=20000)
        page.wait_for_timeout(500)
        thumb = page.locator('[data-testid="gallery-thumbs"] .ws-thumb__pick').last
        if not thumb.is_visible():
            ui.close_menus()
            page.locator('[data-testid="toolgroup-images-toggle"]').click()
            page.wait_for_timeout(300)
        asset = thumb.get_attribute("data-testid")[len("gallery-"):]
        out.fact("gallery_asset", asset)
        thumb.click()
        page.wait_for_timeout(300)
        p = ui.click_pt(*args["image_at"], number=1)
        page.wait_for_selector('[data-testid^="ann-image-"][data-state="ready"]', timeout=20000)
        placed["image_click"] = p
        # remove it from the gallery (the placed copy must stay)
        ui.close_menus()
        page.locator('[data-testid="toolgroup-images-toggle"]').click()
        page.locator(f'[data-testid="gallery-remove-{asset}"]').click()
        page.wait_for_timeout(1200)
        ui.close_menus()
    else:
        # legacy Image tool: a click asks for the file
        if not ui.arm("image", "images"):
            out.pending("Images ▾ Image is not in the live toolbar")
            return
        with page.expect_file_chooser() as fc:
            p = ui.click_pt(*args["image_at"], number=1)
        fc.value.set_files(args["png"])
        page.wait_for_selector('[data-testid^="ann-image-"]', timeout=20000)
        placed["image_click"] = p
    # stamp
    stamp_ok = False
    if page.locator('[data-testid="toolgroup-images-toggle"]').count():
        page.locator('[data-testid="toolgroup-images-toggle"]').click()
        page.wait_for_timeout(200)
        if page.locator('[data-testid="menu-new-stamp"]').count():
            page.locator('[data-testid="menu-new-stamp"]').click()
            page.locator('[data-testid="stamp-dialog"]').wait_for(timeout=10000)
            page.locator('[data-testid="stamp-mode-preset"]').check()
            page.locator('[data-testid="stamp-preset"]').select_option("approved")
            page.locator('[data-testid="stamp-save"]').click()
            page.wait_for_timeout(300)
            placed["stamp_click"] = ui.click_pt(*args["stamp_at"], number=1)
            page.wait_for_timeout(800)
            stamp_ok = True
        else:
            ui.close_menus()
    if not stamp_ok:
        out.pending("Images ▾ New Stamp… is not in the live toolbar")
    page.wait_for_timeout(2500)
    page.wait_for_selector('[data-testid="workspace"][data-markup-sync="synced"]', timeout=30000)
    new = [e for e in api.markup()["elements"] if e["id"] not in before]
    out.fact("placed", placed)
    out.fact("new_elements", new)
    img = next((e for e in new if e["kind"] in ("image", "image2")), None)
    out.check("picture saved as markup at the click (top-left ±0.5 pt)", img is not None
              and abs(img["x_pt"] - placed["image_click"][0]) <= 0.5 and abs(img["y_pt"] - placed["image_click"][1]) <= 0.5,
              img and {"xy": [img["x_pt"], img["y_pt"]], "click": placed["image_click"]})
    if stamp_ok:
        st = next((e for e in new if e["kind"] == "stamp"), None)
        out.check("stamp saved (preset approved, no date filled in)", st is not None and st.get("preset") == "approved"
                  and not st.get("date_text"), st)
    ui.shot("t06_placed")
    out.fact("export_before_remove", True)
    # remove both with Select + Delete (the Objects list is the reliable handle)
    for el in [e for e in new if e["kind"] in ("image", "image2", "stamp")]:
        row = page.locator(f'[data-testid="object-ann-{el["id"]}"]')
        if row.count() == 0:
            page.locator('[data-testid="section-toggle-objects"]').first.click() if ui.exists('[data-testid="section-toggle-objects"]') else None
            page.wait_for_timeout(300)
        for attempt in range(3):
            row = page.locator(f'[data-testid="object-ann-{el["id"]}"]')
            if row.count() == 0:
                break
            ui.arm("select")
            if attempt < 2:
                # select it ON THE PAGE (Select tool, a click in its middle), like a user
                ui.click_pt(el["x_pt"] + el["w_pt"] / 2, el["y_pt"] + el["h_pt"] / 2, number=1)
            else:
                row.first.click()
            page.wait_for_timeout(400)
            btn = page.locator('[data-testid="ann-delete"]')
            if attempt == 0 and btn.count() and btn.first.is_enabled():
                btn.first.click()
            else:
                page.keyboard.press("Delete")
            page.wait_for_timeout(700)
    page.wait_for_timeout(2500)
    page.wait_for_selector('[data-testid="workspace"][data-markup-sync="synced"]', timeout=30000)
    left = [e["id"] for e in api.markup()["elements"] if e["id"] in {x["id"] for x in new}]
    out.check("picture and stamp removed again (server markup)", left == [], left)
    # original image: Images ▾ Delete original image (edit session)
    if ui.arm("image-remove", "images"):
        try:
            ui.page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=5000)
        except Exception:  # noqa: BLE001
            ui.start_session()
            ui.arm("image-remove", "images")
        frame = page.locator('[data-testid^="page-image-1-"]')
        frame.first.wait_for(timeout=20000)
        frame.first.click()
        page.wait_for_timeout(800)
        ui.wait_saved()
        _s, draft = api.get(f"{api.D}/draft")
        kinds = [o["kind"] for o in (draft.get("ops", []) if isinstance(draft, dict) else [])]
        out.check("the original image: an image.remove op", "image.remove" in kinds, kinds)
        ui.shot("t06_image_removed")
        out.fact("banner", ui.save_session())
    else:
        out.pending("Images ▾ Delete original image is not in the live toolbar")
    out.fact("version_no", api.current_no())


def sc_forms(ui, api, args, out):
    """7: Forms ▾ field tools: draw text, multiline, dropdown (+options), radio (+a second
    button of the group), checkbox, signature box -> Save; then Select = fill mode:
    type / choose / tick every value -> Save."""
    page = ui.page
    ui.open_ws(args["cid"])
    n = args.get("page", 3)
    if not ui.arm("field-text", "forms"):
        out.pending("Forms ▾ field tools are not in the live toolbar")
        return
    drawn = []
    for tool, rect in args["draws"]:
        if not ui.arm(tool, "forms"):
            out.check(f"{tool}: tool present", False)
            continue
        p1, p2 = ui.drag_pt(*rect, number=n)
        page.wait_for_timeout(700)
        drawn.append({"tool": tool, "p1": p1, "p2": p2})
        if tool == "field-dropdown":
            for opt in args["options"]:
                page.locator('[data-testid="field-option-new"]').fill(opt)
                page.locator('[data-testid="field-option-add"]').click()
                page.wait_for_timeout(300)
        if tool == "field-radio":
            page.locator('[data-testid="field-duplicate"]').click()
            page.wait_for_timeout(600)
    ui.arm("select")
    page.wait_for_timeout(1500)
    ui.wait_saved()
    _s, draft = api.get(f"{api.D}/draft")
    ops = draft.get("ops", []) if isinstance(draft, dict) else []
    adds = [o for o in ops if o["kind"] == "field.add"]
    out.fact("drawn", drawn)
    out.fact("field_ops", adds)
    types = sorted(o["field"]["type"] for o in adds)
    out.check("7 field.add ops: text, multiline, dropdown, radio ×2, checkbox, signature",
              types == sorted(["text", "multiline", "dropdown", "radio", "radio", "checkbox", "signature"]), types)
    want_type = {"field-text": "text", "field-multiline": "multiline", "field-dropdown": "dropdown", "field-radio": "radio",
                 "field-checkbox": "checkbox", "field-signature": "signature"}
    for d in drawn:
        op = next((o for o in adds if o["field"]["type"] == want_type[d["tool"]]), None)
        if not op:
            continue
        r = op["field"]["rect"]
        want = (min(d["p1"][0], d["p2"][0]), min(d["p1"][1], d["p2"][1]), abs(d["p2"][0] - d["p1"][0]), abs(d["p2"][1] - d["p1"][1]))
        dev = max(abs(a - b) for a, b in zip((r["x_pt"], r["y_pt"], r["w_pt"], r["h_pt"]), want))
        out.check(f"{d['tool']}: drawn where dragged (±0.5 pt)", dev <= 0.5, {"rect": r, "drag": [round(v, 3) for v in want]})
    dd = next((o for o in adds if o["field"]["type"] == "dropdown"), None)
    out.check("dropdown got the typed options", dd is not None and all(x in (dd["field"].get("options") or []) for x in args["options"]),
              dd and dd["field"].get("options"))
    ui.shot("t07_fields_drawn")
    out.fact("banner_structure", ui.save_session())
    # fill mode (Select)
    ui.arm("select")
    page.wait_for_timeout(800)

    def field_input(ftype, nth=0):
        return page.locator(f'[data-testid="form-field"][data-type="{ftype}"] [data-testid="field-input"]').nth(nth)

    ui.reveal(620, n)
    t = field_input("text")
    t.click()
    t.fill(args["values"]["text"])
    t.press("Enter")
    page.wait_for_timeout(500)
    m = field_input("multiline")
    m.click()
    m.fill(args["values"]["multiline"])
    m.press("Tab")
    page.wait_for_timeout(500)
    field_input("dropdown").select_option(args["values"]["dropdown"])
    page.wait_for_timeout(500)
    field_input("radio", 1).check()
    page.wait_for_timeout(500)
    field_input("checkbox").check()
    page.wait_for_timeout(1500)
    ui.wait_saved()
    _s, draft = api.get(f"{api.D}/draft")
    vals = {o["name"]: o["value"] for o in (draft.get("ops", []) if isinstance(draft, dict) else []) if o["kind"] == "field.value"}
    out.fact("value_ops", vals)
    out.check("fill mode: 5 field.value ops", len(vals) == 5, vals)
    ui.shot("t07_fields_filled")
    out.fact("banner_values", ui.save_session())
    out.fact("version_no", api.current_no())


def sc_links(ui, api, args, out):
    """8: Links: a web link and a link to another page (edit session) -> Save."""
    page = ui.page
    ui.open_ws(args["cid"])
    if not ui.arm("links"):
        out.pending("Links tool is not in the live toolbar")
        return
    r1 = ui.drag_pt(*args["uri_rect"], number=1)
    page.locator('[data-testid="link-dialog"]').wait_for(timeout=10000)
    page.locator('[data-testid="link-url"]').fill(args["uri"])
    page.locator('[data-testid="link-save"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=20000)
    ui.arm("links")
    r2 = ui.drag_pt(*args["page_rect"], number=1)
    page.locator('[data-testid="link-dialog"]').wait_for(timeout=10000)
    page.locator('[data-testid="link-kind-page"]').check()
    page.locator('[data-testid="link-page"]').select_option(str(args["target_page"]))
    page.locator('[data-testid="link-save"]').click()
    page.wait_for_timeout(600)
    ui.wait_saved()
    _s, draft = api.get(f"{api.D}/draft")
    ops = [o for o in (draft.get("ops", []) if isinstance(draft, dict) else []) if o["kind"] == "link.add"]
    out.fact("ops", ops)
    out.fact("drag", [r1, r2])
    out.check("two link.add ops", len(ops) == 2, [o["target"] for o in ops])
    if len(ops) == 2:
        uri = next((o for o in ops if o["target"]["type"] == "uri"), None)
        pg = next((o for o in ops if o["target"]["type"] == "page"), None)
        ok = uri and abs(uri["rect"]["x_pt"] - r1[0][0]) <= 0.5 and abs(uri["rect"]["y_pt"] - r1[0][1]) <= 0.5
        out.check("web link rect at the drag (±0.5 pt)", bool(ok), uri and uri["rect"])
        out.check("page link targets a page uid", bool(pg) and pg["target"]["page_uid"].startswith("pg_"), pg and pg["target"])
    ui.shot("t08_links")
    out.fact("banner", ui.save_session())
    out.fact("version_no", api.current_no())


def _norm_box(e):
    if e["kind"] == "shape2" and e.get("shape") in ("rect", "ellipse") or e["kind"] in ("rect", "ellipse", "whiteout", "image", "image2", "stamp", "textbox"):
        return ("box", e["x_pt"], e["y_pt"], e["w_pt"], e["h_pt"])
    if e["kind"] in ("line", "arrow") or (e["kind"] == "shape2" and e.get("shape") in ("line", "arrow")):
        return ("seg", e["x1_pt"], e["y1_pt"], e["x2_pt"], e["y2_pt"])
    if e["kind"] in ("ink", "ink2"):
        pts = [p for path in e["paths"] for p in path]
        xs, ys = [p["x_pt"] for p in pts], [p["y_pt"] for p in pts]
        return ("box", min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
    if e["kind"] in ("check", "cross", "square_mark", "note", "text", "date"):
        return ("pt", e["x_pt"], e["y_pt"])
    return None


def _shape_kind(e):
    if e["kind"] == "ink2":
        return "ink"
    return e.get("shape") if e["kind"] == "shape2" else e["kind"]


def sc_markup(ui, api, args, out):
    """9: annotation + shape tools at 100 % and ~209 %: what the server stores is
    exactly where the pointer was (±0.5 pt)."""
    page = ui.page
    ui.open_ws(args["cid"])
    n = args.get("page", 3)
    results = []
    for zi, target in enumerate(args.get("zooms", [100, 209])):
        z = ui.set_zoom(target)
        dy = zi * 150.0
        before = {e["id"] for e in api.markup()["elements"]}
        expect = []
        for tool, menu, kind, geo in args["shapes"]:
            if not ui.arm(tool, menu):
                out.pending(f"{tool} tool missing")
                continue
            x1, y1, x2, y2 = geo
            p1, p2 = ui.drag_pt(x1, y1 + dy, x2, y2 + dy, number=n)
            expect.append((tool, kind, p1, p2))
        for tool, menu, kind, (x, y) in args["clicks"]:
            if not ui.arm(tool, menu):
                out.pending(f"{tool} tool missing")
                continue
            p = ui.click_pt(x, y + dy, number=n)
            if tool == "note":
                page.locator('[data-testid="note-input"]').wait_for(timeout=5000)
                page.locator('[data-testid="note-input"]').fill("ZZSYNTH note")
                page.locator('[data-testid="note-save"]').click()
            expect.append((tool, kind, p, None))
        ui.arm("select")
        page.wait_for_timeout(2500)
        page.wait_for_selector('[data-testid="workspace"][data-markup-sync="synced"]', timeout=30000)
        new = [e for e in api.markup()["elements"] if e["id"] not in before]
        ui.shot(f"t09_markup_{target}")
        used = set()
        for tool, kind, p1, p2 in expect:
            cand = [e for e in new if e["id"] not in used and _shape_kind(e) == kind]
            if not cand:
                out.check(f"[{z['label']}%] {tool}: saved", False, [(_shape_kind(e)) for e in new])
                continue
            e = cand[0]
            used.add(e["id"])
            g = _norm_box(e)
            if g and g[0] == "box":
                want = (min(p1[0], p2[0]), min(p1[1], p2[1]), abs(p2[0] - p1[0]), abs(p2[1] - p1[1]))
                dev = max(abs(a - b) for a, b in zip(g[1:], want))
            elif g and g[0] == "seg":
                want = (p1[0], p1[1], p2[0], p2[1])
                dev = max(abs(a - b) for a, b in zip(g[1:], want))
            elif g and g[0] == "pt":
                want = p1
                dev = max(abs(g[1] - p1[0]), abs(g[2] - p1[1]))
            else:
                want, dev = None, None
            results.append({"zoom": z, "tool": tool, "stored": g, "pointer": want, "dev_pt": dev, "id": e["id"]})
            out.check(f"[{z['label']}%] {tool}: stored where the pointer was (±0.5 pt)", dev is not None and dev <= 0.5,
                      {"stored": g, "pointer": [round(v, 3) for v in want] if want else None, "dev": round(dev, 3) if dev is not None else None})
    out.fact("placements", results)
    # text markup: highlight a line by selecting its text (100 %)
    ui.set_zoom(100)
    for tool, needle in args.get("text_markup", []):
        if not ui.arm(tool, "annotate"):
            out.pending(f"{tool} tool missing")
            continue
        before = {e["id"] for e in api.markup()["elements"]}
        span = page.locator(f'[data-testid="pdf-text-layer-1"] span:has-text("{needle}")').first
        span.scroll_into_view_if_needed()
        b = span.bounding_box()
        page.mouse.move(b["x"] + 1, b["y"] + b["height"] / 2)
        page.mouse.down()
        page.mouse.move(b["x"] + b["width"] - 1, b["y"] + b["height"] / 2, steps=10)
        page.mouse.up()
        page.wait_for_timeout(2500)
        page.wait_for_selector('[data-testid="workspace"][data-markup-sync="synced"]', timeout=30000)
        new = [e for e in api.markup()["elements"] if e["id"] not in before]
        out.fact(f"text_markup_{tool}", new)
        out.check(f"{tool}: saved over the selected text", any(e["kind"] == tool for e in new), [e["kind"] for e in new])
    ui.arm("select")
    ui.shot("t09_final")


def sc_whiteout(ui, api, args, out):
    """10: Whiteout (markup) over a line; Redact (real, edit session) over another."""
    page = ui.page
    ui.open_ws(args["cid"])
    before = {e["id"] for e in api.markup()["elements"]}
    if ui.arm("whiteout", "whiteout") or ui.arm("whiteout"):
        p1, p2 = ui.drag_pt(*args["white_rect"], number=1)
        page.wait_for_timeout(2500)
        page.wait_for_selector('[data-testid="workspace"][data-markup-sync="synced"]', timeout=30000)
        new = [e for e in api.markup()["elements"] if e["id"] not in before and e["kind"] == "whiteout"]
        out.fact("whiteout", new)
        ok = bool(new) and max(abs(new[0]["x_pt"] - p1[0]), abs(new[0]["y_pt"] - p1[1]),
                               abs(new[0]["w_pt"] - (p2[0] - p1[0])), abs(new[0]["h_pt"] - (p2[1] - p1[1]))) <= 0.5
        out.check("whiteout saved where it was dragged (±0.5 pt)", ok, {"stored": new[0] if new else None, "drag": [p1, p2]})
        ui.shot("t10_whiteout")
    else:
        out.pending("Whiteout tool missing")
    if ui.arm("redact", "whiteout"):
        try:
            page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=3000)
        except Exception:  # noqa: BLE001
            ui.start_session()
            ui.arm("redact", "whiteout")
        ui.drag_pt(*args["redact_rect"], number=1)
        page.wait_for_timeout(800)
        ui.wait_saved()
        _s, draft = api.get(f"{api.D}/draft")
        kinds = [o["kind"] for o in (draft.get("ops", []) if isinstance(draft, dict) else [])]
        out.check("redact op in the draft", "redact" in kinds, kinds)
        ui.shot("t10_redact")
        out.fact("banner", ui.save_session())
    else:
        out.pending("Whiteout ▾ Redact is not in the live toolbar")
    out.fact("version_no", api.current_no())


def sc_pages(ui, api, args, out):
    """12: pages panel: insert after 1, delete the (old) page 2, rotate the inserted page -> Save."""
    page = ui.page
    ui.open_ws(args["cid"])
    if not ui.exists('[data-testid="page-insert-1"]'):
        tog = page.locator('[data-testid="pdf-thumbs-toggle"]')
        if tog.count():
            tog.first.click()
            page.wait_for_timeout(600)
    if not ui.exists('[data-testid="page-insert-1"]'):
        out.pending("pages panel (insert / delete / rotate) is not in the live UI")
        return
    uids_before = [p["page_uid"] for p in api.pages()]
    page.locator('[data-testid="page-insert-1"]').click()
    page.wait_for_function("(n) => document.querySelectorAll('.pdf-page').length === n", arg=len(uids_before) + 1, timeout=30000)
    page.wait_for_timeout(800)
    page.locator('[data-testid="page-delete-3"]').click()
    page.locator('[data-testid="page-delete-dialog"]').wait_for()
    out.fact("delete_dialog", page.locator('[data-testid="page-delete-counts"]').inner_text()
             if ui.exists('[data-testid="page-delete-counts"]') else None)
    page.locator('[data-testid="page-delete-confirm"]').click()
    page.wait_for_function("(n) => document.querySelectorAll('.pdf-page').length === n", arg=len(uids_before), timeout=30000)
    page.wait_for_timeout(800)
    page.locator('[data-testid="page-rotate-right-2"]').click()
    page.wait_for_timeout(1500)
    ui.wait_saved()
    _s, draft = api.get(f"{api.D}/draft")
    kinds = [o["kind"] for o in (draft.get("ops", []) if isinstance(draft, dict) else [])]
    out.fact("ops", kinds)
    out.check("draft: insert, delete, rotate", kinds == ["page.insert", "page.delete", "page.rotate"], kinds)
    ui.shot("t12_pages")
    out.fact("banner", ui.save_session())
    out.fact("uids_before", uids_before)
    out.fact("pages_after", api.pages())
    out.fact("version_no", api.current_no())


def sc_undo_redo(ui, api, args, out):
    """13: edit -> Undo (the op leaves the draft) -> Redo (it is back) -> Cancel editing."""
    page = ui.page
    ui.open_ws(args["cid"])
    v0 = api.current_no()
    ui.start_session()
    ui.arm_edit_text()
    ui.open_block(args["text"])
    page.keyboard.press("End")
    page.keyboard.type(args["suffix"])
    page.keyboard.press("Enter")
    ui.after_text_commit()

    def ops():
        _s, d = api.get(f"{api.D}/draft")
        return [o["kind"] for o in (d.get("ops", []) if isinstance(d, dict) else [])]

    out.check("after the edit: 1 op", ops() == ["text.replace"], ops())
    page.locator('[data-testid="editor-undo"]').click()
    page.wait_for_timeout(2500)
    ui.wait_saved()
    out.check("Undo: the draft holds no op", ops() == [], ops())
    blocks = ui.blocks(1)
    out.check("Undo: the page shows the original line again",
              any(b["text"] == args["text"] for b in blocks) and not any(args["suffix"] in (b["text"] or "") for b in blocks),
              [b["text"] for b in blocks if args["text"][:10] in (b["text"] or "")])
    page.locator('[data-testid="editor-redo"]').click()
    page.wait_for_timeout(2500)
    ui.wait_saved()
    out.check("Redo: the op is back", ops() == ["text.replace"], ops())
    ui.shot("t13_redo")
    page.locator('[data-testid="editor-cancel"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="view"]', timeout=20000)
    page.wait_for_timeout(1000)
    doc = api.document()
    out.check("Cancel editing: no draft, version unchanged", doc["draft"] is None and doc["current_version"]["version_no"] == v0,
              {"draft": doc["draft"], "v": doc["current_version"]["version_no"], "v0": v0})


def sc_save_reopen(ui, api, args, out):
    """14: edit -> Save -> close the tab -> a NEW tab shows the saved version's text."""
    page = ui.page
    ui.open_ws(args["cid"])
    ui.start_session()
    ui.arm_edit_text()
    ui.open_block(args["text"])
    page.keyboard.press("End")
    page.keyboard.type(args["suffix"])
    page.keyboard.press("Enter")
    ui.after_text_commit()
    banner = ui.save_session()
    v = api.current_no()
    out.check("saved as a new version", banner is not None and f"version {v}" in banner, {"banner": banner, "v": v})
    page.close()
    ui.page = ui.new_page()
    ui.open_ws(args["cid"])
    ui.arm_edit_text()
    found = [b["text"] for b in ui.blocks(1) if args["suffix"].strip() in (b["text"] or "")]
    out.check("re-opened: the saved text is the page's text", len(found) == 1, found)
    ui.shot("t14_reopened")
    ui.arm("select")
    out.fact("version_no", v)


def sc_recovery(ui, api, args, out):
    """15: an unsaved draft (tab closed) -> a new tab offers «Unsaved changes …» -> Resume -> Save."""
    page = ui.page
    ui.open_ws(args["cid"])
    ui.start_session()
    ui.arm_edit_text()
    ui.open_block(args["text"])
    page.keyboard.press("End")
    page.keyboard.type(args["suffix"])
    page.keyboard.press("Enter")
    ui.after_text_commit()
    page.close()
    ui.page = ui.new_page()
    page = ui.page
    ui.open_ws(args["cid"], synced=False)
    rec = page.wait_for_selector('[data-testid="session-recovery"]', timeout=20000)
    text = rec.inner_text()
    out.check("recovery banner in a new tab", "Unsaved changes" in text, text)
    ui.shot("t15_recovery")
    page.locator('[data-testid="session-resume"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=20000)
    ui.arm_edit_text()
    page.wait_for_selector('.tb-block[data-edited="true"]', timeout=20000)
    edited = page.locator('.tb-block[data-edited="true"]').first.get_attribute("data-text")
    _s, draft = api.get(f"{api.D}/draft")
    text = "".join(r["text"] for o in (draft.get("ops", []) if isinstance(draft, dict) else []) for r in o.get("runs", []))
    out.check("Resume brings the edit back (the block is marked edited, the draft op holds the new text)",
              edited == args["text"] and args["suffix"].strip() in text, {"edited_block": edited, "draft_text": text})
    out.fact("banner", ui.save_session())
    out.fact("version_no", api.current_no())


def apply_dates(ui, date_iso) -> bool:
    """«Each one separately» -> contract date -> «Apply to all date fields» (dates live in the
    browser until the final is generated -- never filled in automatically)."""
    page = ui.page
    mode = page.get_by_role("radio", name="Each one separately")
    if mode.count():
        mode.first.click()
        page.wait_for_timeout(300)
    done = False
    if ui.exists('[data-testid="contract-date"]'):
        page.locator('[data-testid="contract-date"]').fill(date_iso)
        page.get_by_role("button", name="Apply to all date fields").click()
        done = True
    page.wait_for_timeout(1200)
    return done


def sc_sign(ui, api, args, out):
    """11: Sign ▾ Signature on the slots (drawn in the dialog), dates applied to all."""
    page = ui.page
    ui.open_ws(args["cid"], panel="sign")
    signed = []
    _s, rows0 = api.get(f"/api/contracts/{api.cid}/signatures")
    already = {r["field_id"] for r in (rows0 or []) if r.get("field_id")}
    for field in args["slots"]:
        if field in already:
            continue
        if not ui.arm("signature", "sign"):
            out.pending("Sign ▾ Signature tool missing")
            return
        slot = page.locator(f'.slot[data-field-id="{field}"]')
        slot.scroll_into_view_if_needed()
        try:
            slot.click(timeout=6000)
        except Exception:  # noqa: BLE001 -- something drawn over the slot: press the slot itself
            out.fact(f"slot_click_intercepted_{field}", True)
            slot.dispatch_event("click")
        page.locator(".modal .sign-modal").wait_for(timeout=10000)
        page.locator('[role="tab"][data-tab="draw"]').click()
        canvas = page.locator(".sign-modal .signature-pad__canvas")
        cb = canvas.bounding_box()
        page.mouse.move(cb["x"] + 30, cb["y"] + cb["height"] * 0.6)
        page.mouse.down()
        for i in range(1, 40):
            page.mouse.move(cb["x"] + 30 + i * 8, cb["y"] + cb["height"] * (0.6 - 0.25 * ((i % 8) / 8.0)), steps=2)
        page.mouse.up()
        save_tick = page.locator('.sign-modal .modal__extra input[type="checkbox"]')
        if save_tick.count() and save_tick.is_checked():
            save_tick.uncheck()
        page.locator(".sign-modal .modal__actions .button--primary").click()
        page.wait_for_selector(".modal .sign-modal", state="detached", timeout=30000)
        page.wait_for_timeout(800)
        signed.append(field)
    _s, rows = api.get(f"/api/contracts/{api.cid}/signatures")
    out.fact("signatures", rows)
    out.check("both slots signed", sorted(r["field_id"] for r in rows if r.get("field_id")) == sorted(args["slots"]),
              [(r["field_id"], r["page_number"]) for r in rows])
    out.check("dates applied in the sign panel", apply_dates(ui, args["date_iso"]))
    try:
        page.wait_for_function("() => document.querySelectorAll('.placed-dates text').length >= 2", timeout=10000)
        out.check("date text shown on both date fields", True)
    except Exception:  # noqa: BLE001
        out.check("date text shown on both date fields", False, page.evaluate("() => document.querySelectorAll('.placed-dates text').length"))
    ui.shot("t11_signed")
    # layout (kept for the final): read what the browser will send
    out.fact("layout", page.evaluate("(k) => localStorage.getItem(k)", f"swm.layout.{api.cid}"))


def sc_download(ui, api, args, out):
    """16: «Download PDF»: the export dialog (F2) or the plain download."""
    page = ui.page
    ui.open_ws(args["cid"])
    btn = page.locator('[data-testid="editor-download"]')
    out.fact("download_source", btn.get_attribute("data-source"))
    path = os.path.join(args["dir"], "ui_download.pdf")
    btn.click()
    try:
        page.locator('[data-testid="export-dialog"]').wait_for(timeout=4000)
        out.fact("export_label", page.locator('[data-testid="export-include-label"]').inner_text()
                 if ui.exists('[data-testid="export-include-label"]') else None)
        ui.shot("t16_export_dialog")
        with page.expect_download(timeout=90000) as info:
            page.locator('[data-testid="export-download"]').click()
        out.fact("mode", "export_dialog")
    except Exception:  # noqa: BLE001
        with page.expect_download(timeout=90000) as info:
            btn.click()
        out.fact("mode", "plain_download")
    info.value.save_as(path)
    out.data["files"]["download"] = path
    out.check("a PDF was downloaded", os.path.getsize(path) > 500, os.path.getsize(path))


def sc_finalize(ui, api, args, out):
    """17: «Generate final contract» -> the final page."""
    page = ui.page
    ui.open_ws(args["cid"], panel="sign")
    if args.get("layout"):
        page.evaluate("([k, v]) => localStorage.setItem(k, v)", [f"swm.layout.{api.cid}", args["layout"]])
    if args.get("date_iso"):
        out.check("the date chosen again in this session (dates are not stored before the final)",
                  apply_dates(ui, args["date_iso"]))
    fin = page.locator('[data-testid="editor-finalize"]')
    out.check("Generate final contract is enabled", fin.is_enabled(), fin.get_attribute("title"))
    fin.click()
    page.wait_for_url(f"**/contracts/{api.cid}/final", timeout=180000)
    page.wait_for_timeout(1500)
    ui.shot("t17_final_page")
    out.check("the final page opened", True, page.url.replace(UI_BASE, ""))


def sc_legacy(ui, api, args, out):
    """18: the app's older functions, end to end on a NEW contract uploaded through the UI."""
    page = ui.page
    # dashboard + search
    page.goto(f"{UI_BASE}/")
    page.wait_for_selector('[data-testid="dash-row"], [data-testid="dash-empty-none"]', timeout=60000)
    rows0 = page.locator('[data-testid="dash-row"]').count()
    out.check("dashboard lists contracts", rows0 >= 1, rows0)
    if ui.exists('[data-testid="dash-search"]'):
        page.locator('[data-testid="dash-search"]').fill("ZZSYNTH-ACC")
        page.wait_for_timeout(800)
        rows = page.locator('[data-testid="dash-row"]').all_inner_texts()
        out.check("dashboard search finds the ZZSYNTH contracts only", rows and all("ZZSYNTH-ACC" in r for r in rows), len(rows))
    ui.shot("t18_dashboard")
    # templates, review queue, my signatures
    for path, needle, name in (("/templates", args["template_name"], "templates page lists the template"),
                               ("/review", None, "review queue opens"),
                               ("/signatures", None, "My signatures opens")):
        page.goto(f"{UI_BASE}{path}")
        page.wait_for_timeout(2500)
        body = page.locator("main, body").first.inner_text()
        out.check(name, (needle in body) if needle else len(body) > 20, (needle, len(body)))
        ui.shot("t18" + path.replace("/", "_"))
    # upload through the UI
    page.goto(f"{UI_BASE}/contracts/new")
    page.locator(".upload-template", has_text=args["template_name"]).first.click()
    page.locator('input[type="file"][accept*="pdf"]').first.set_input_files(args["pdf"])
    page.locator('[data-testid="upload-file"]').wait_for(timeout=20000)
    page.locator('[data-testid="upload-contractor-name"]').fill(args["contractor"])
    ui.shot("t18_upload_form")
    page.locator('[data-testid="upload-submit"]').click()
    page.wait_for_url("**/contracts/**", timeout=90000)
    page.wait_for_timeout(1500)
    cid = None
    for part in page.url.split("/"):
        if len(part) == 36 and part.count("-") == 4:
            cid = part
    out.fact("contract_id", cid)
    out.check("upload through the UI created a contract", cid is not None, page.url.replace(UI_BASE, ""))
    if not cid:
        return
    api.cid = cid
    with open(args["ids_file"], "r+", encoding="utf-8") as fh:  # recorded at once, for the cleanup
        ids = json.load(fh)
        ids["contract_legacy_ui"] = cid
        fh.seek(0)
        json.dump(ids, fh, indent=1)
        fh.truncate()
    api.wait_idle()
    out.check("approve (review) -> 200", api.approve() == 200)
    ui.open_ws(cid, panel="sign")
    # thumbnails, search, zoom
    tog = page.locator('[data-testid="pdf-thumbs-toggle"]')
    if tog.count() and not ui.exists('[data-testid="pdf-thumb-3"]'):
        tog.first.click()
    try:
        page.wait_for_selector('[data-testid="pdf-thumb-3"]', timeout=15000)
        out.check("page thumbnails (3)", page.locator('[data-testid^="pdf-thumb-"]').count() >= 3)
    except Exception:  # noqa: BLE001
        out.check("page thumbnails (3)", False, page.locator('[data-testid^="pdf-thumb-"]').count())
    page.locator('[data-testid="pdf-search-input"]').fill("ZZPAGE3")
    page.keyboard.press("Enter")
    page.wait_for_timeout(1500)
    cnt = page.locator('[data-testid="pdf-search-count"]').inner_text() if ui.exists('[data-testid="pdf-search-count"]') else ""
    out.check("search finds the page-3 word", "1" in cnt, cnt)
    page.locator('[data-testid="pdf-search-clear"]').click() if ui.exists('[data-testid="pdf-search-clear"]') else None
    z0 = ui.zoom_label()
    page.get_by_role("button", name="+", exact=True).first.click()
    page.wait_for_timeout(400)
    z1 = ui.zoom_label()
    page.get_by_role("button", name="−", exact=True).first.click()
    page.wait_for_timeout(400)
    out.check("zoom + / − changes the scale", z0 is not None and z1 is not None and z1 > z0, [z0, z1, ui.zoom_label()])
    # check / cross at 100 % and ~209 %
    marks = []
    for target, dy in ((100, 0.0), (209, 40.0)):
        z = ui.set_zoom(target)
        for tool, (x, y) in (("check", args["check_at"]), ("cross", args["cross_at"])):
            if not ui.arm(tool, "forms"):
                out.pending(f"{tool} tool missing")
                continue
            p = ui.click_pt(x, y + dy, number=1)
            marks.append({"tool": tool, "zoom": z, "click": p})
    ui.arm("select")
    page.wait_for_timeout(2500)
    page.wait_for_selector('[data-testid="workspace"][data-markup-sync="synced"]', timeout=30000)
    els = [e for e in api.markup()["elements"] if e["kind"] in ("check", "cross")]
    out.fact("marks", marks)
    out.fact("mark_elements", els)
    for m in marks:
        e = next((e for e in els if e["kind"] == m["tool"] and abs(e["x_pt"] - m["click"][0]) <= 3
                  and abs(e["y_pt"] - m["click"][1]) <= 3), None)
        dev = None if e is None else max(abs(e["x_pt"] - m["click"][0]), abs(e["y_pt"] - m["click"][1]))
        out.check(f"[{m['zoom']['label']}%] {m['tool']} stored at the click (±0.5 pt)", dev is not None and dev <= 0.5,
                  {"dev": dev, "click": m["click"]})
    ui.set_zoom(100)
    ui.shot("t18_marks")
    # sign «One for all» (bulk) with a drawn signature + the date
    sc_sign(ui, api, {"slots": args["slots"], "date_iso": args["date_iso"], "cid": cid}, out)
    # group shift (Signatures together ▲ ×6 = +3 pt) + individual (p3 slot ▲ ×4 = +2 pt)
    lp = page.locator('[data-testid="layout-panel"]')
    if lp.count() == 0 and ui.exists('[data-testid="section-toggle-layout"]'):
        page.locator('[data-testid="section-toggle-layout"]').click()
    if page.locator('[data-testid="shift-signatures"]').count():
        up = page.get_by_role("button", name="Signatures together: move up")
        for _ in range(6):
            up.click()
            page.wait_for_timeout(80)
        slot_up = page.locator(f'[data-testid="offset-slot-{args["slots"][1]}"] button[aria-label$="move up"]')
        for _ in range(4):
            slot_up.first.click()
            page.wait_for_timeout(80)
        layout = page.evaluate("(k) => localStorage.getItem(k)", f"swm.layout.{cid}")
        out.fact("layout", layout)
        out.check("group shift + one slot's own offset saved in the layout", layout is not None and "slot_offsets_pt" in layout, layout)
    else:
        out.check("layout panel (group shift, individual) present", False)
    ui.shot("t18_layout")
    fin = page.locator('[data-testid="editor-finalize"]')
    fin.click()
    page.wait_for_url(f"**/contracts/{cid}/final", timeout=180000)
    page.wait_for_timeout(1500)
    ui.shot("t18_final")
    out.check("final contract generated from the UI", True)


def sc_pdfjs(ui, api, args, out):
    """16: pdf.js (the frontend's own pdfjs-dist 4.8.69) opens the file, renders page 1, reads text."""
    page = ui.page
    page.goto(f"{UI_BASE}/login")
    res = {}
    for label, path in args["files"].items():
        b64 = base64.b64encode(open(path, "rb").read()).decode()
        r = page.evaluate(
            """async (b64) => {
                 const lib = await import('/node_modules/pdfjs-dist/build/pdf.mjs');
                 lib.GlobalWorkerOptions.workerSrc = '/node_modules/pdfjs-dist/build/pdf.worker.mjs';
                 const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
                 const doc = await lib.getDocument({data: bin}).promise;
                 const out = {pages: doc.numPages, text: [], ink: []};
                 for (let i = 1; i <= doc.numPages; i++) {
                   const pg = await doc.getPage(i);
                   const tc = await pg.getTextContent();
                   out.text.push(tc.items.map((it) => it.str).join(' '));
                   const vp = pg.getViewport({scale: 1});
                   const c = document.createElement('canvas'); c.width = Math.ceil(vp.width); c.height = Math.ceil(vp.height);
                   await pg.render({canvasContext: c.getContext('2d'), viewport: vp}).promise;
                   const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                   let dark = 0; for (let k = 0; k < d.length; k += 4) if (d[k+3] > 0 && d[k] < 128 && d[k+1] < 128 && d[k+2] < 128) dark++;
                   out.ink.push(dark);
                 }
                 return out; }""", b64)
        res[label] = r
        out.check(f"pdf.js opens {label} ({r['pages']} pages)", r["pages"] >= 1, {"pages": r["pages"], "ink": r["ink"]})
    out.fact("pdfjs", res)


SCENARIOS = {
    "probe": sc_probe, "edit_text": sc_edit_text, "delete_text": sc_delete_text, "textbox": sc_textbox,
    "style": sc_style, "move_dup": sc_move_dup, "images": sc_images, "forms": sc_forms, "links": sc_links, "markup": sc_markup,
    "whiteout": sc_whiteout, "pages": sc_pages, "undo_redo": sc_undo_redo, "save_reopen": sc_save_reopen,
    "recovery": sc_recovery, "sign": sc_sign, "download": sc_download, "finalize": sc_finalize,
    "legacy": sc_legacy, "pdfjs": sc_pdfjs,
}


def main() -> int:
    name, args_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    args = json.load(open(args_path, encoding="utf-8"))
    out = Out()
    token = login()
    shots = args.get("shots") or os.path.dirname(out_path)
    os.makedirs(shots, exist_ok=True)
    t0 = time.time()
    with sync_playwright() as p:
        ui = UI(p, token, out, shots)
        try:
            SCENARIOS[name](ui, api_for(token, args.get("cid")), args, out)
        except Exception as exc:  # noqa: BLE001
            out.check(f"scenario {name} ran to the end", False, f"{type(exc).__name__}: {str(exc)[:400]}")
            out.data["traceback"] = traceback.format_exc()[-3000:]
            ui.shot(f"ERROR_{name}")
        finally:
            ui.close()
    out.data["seconds"] = round(time.time() - t0, 1)
    json.dump(out.data, open(out_path, "w", encoding="utf-8"), indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
