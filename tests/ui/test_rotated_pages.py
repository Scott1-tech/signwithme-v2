"""
ROTFIX (2026-09-30): pages that are REALLY turned -- the file's own `/Rotate`
(`page.rotate` of the edit session, `document_pages.rotation`), not the
reader's view turn.

Two spaces are measured on such a page, and the checks pin both against the
PDF, with PyMuPDF doing the PDF's side (`Rect * page.rotation_matrix` is where
a point of the UNROTATED page is seen on the page as displayed):

  * what the PDF ENGINE speaks is unrotated (EDITOR-V2-PLAN §0.2): form
    widgets, links, text blocks, redactions, the v7 markup (a text box) --
    on screen each must be where PyMuPDF says the displayed page has it;
  * what the legacy builder draws is the page AS DISPLAYED: slots, slot
    signatures, the v1-v6 markup (a tick) -- on screen exactly at its points.

Then: a click / a drag on a turned page stores the right points (a tick in
displayed points, a text box / a field / a link / a redaction in unrotated
points that PyMuPDF maps back under the pointer); a draft's `page.rotate`
turns the server's marks (inert) and the annotations with the page, Ctrl+Z
turns them back, Save keeps them; a view turn on top of a real one composes;
the search marks a word of a turned page; the "rotated view" notice is about
the VIEW turn only. ±0.5 pt everywhere.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymupdf  # noqa: E402

from _docmock import Line, wait_until  # noqa: E402
from _f2 import clean, last_markup, ops_of, select_tool, shot, wait_markup, wait_ops  # noqa: E402
from _formmock import enable_forms, field_out  # noqa: E402
from _harness import BASE, MockApi, Registry, approx, cli_main, equals, slot, truthy  # noqa: E402

test = Registry("rotated pages (ROTFIX)")

TOL = 0.5
FIELD = (300.0, 140.0, 200.0, 18.0)
LINK = {"link_id": "lk12", "rect": {"x_pt": 72, "y_pt": 300, "w_pt": 100, "h_pt": 20},
        "target": {"type": "uri", "uri": "https://example.com/zz"}}
SLOT = slot("sig_1", "signature", 72, 640, 180, 34, rule_y=679.5)
PLACEMENT_NOTE = "Rotated view — placement disabled; rotate back to place signatures"


# --------------------------------------------------------------------------
# Fixture: a document whose version 1 has pages with their own /Rotate
# --------------------------------------------------------------------------

def install(mock: MockApi, rotation: int, *, pages: int = 1, fields: list | None = None, signed: list | None = None,
            links: bool = True):
    mock.set_slots([SLOT], signed=signed if signed is not None else [])
    doc = enable_forms(
        mock,
        [[Line(f"Page {index + 1} carrier text", 72, 150), Line("Name: ________", 72, 300)] for index in range(pages)],
        fields=fields,
    )
    rotations = [rotation] + [0] * (pages - 1)
    doc._add_version(1, doc.pages, content_changed=False, rotations=rotations)
    mock.set_pdf(doc.pdf_v1())
    if links:
        doc.links[doc.uids[0]] = [dict(LINK)]
    return doc


def open_doc(page, mock: MockApi) -> None:
    page.goto(f"{BASE}/contracts/{mock.contract_id}/edit")
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_selector('[data-testid="workspace"][data-editor-v2="true"]', timeout=15000)
    page.wait_for_timeout(400)


def shown_rotation(page, number: int = 1) -> int:
    """The page's turn as laid out (own /Rotate + the view turn), read off its box."""
    target = page.locator(f'.pdf-page[data-page-number="{number}"][data-rendered]')
    box = target.bounding_box() if target.count() else None
    if not box:
        return -1  # between two documents
    return 90 if box["width"] > box["height"] else 0


def page_box(page, number: int = 1) -> dict:
    target = page.locator(f'.pdf-page[data-page-number="{number}"]')
    target.scroll_into_view_if_needed()
    return target.bounding_box()


def scale_of(page, number: int = 1) -> float:
    box = page_box(page, number)
    # A 612 x 792 page: landscape on screen = 792 wide.
    return box["width"] / (792 if box["width"] > box["height"] else 612)


def dom_pt(page, selector: str, number: int = 1) -> tuple[float, float, float, float]:
    """An element's box on screen, in points of the page AS DISPLAYED."""
    box = page_box(page, number)
    scale = scale_of(page, number)
    target = page.locator(f'.pdf-page[data-page-number="{number}"] {selector}').first
    target.wait_for(state="attached", timeout=10000)
    b = target.bounding_box()
    return ((b["x"] - box["x"]) / scale, (b["y"] - box["y"]) / scale, b["width"] / scale, b["height"] / scale)


def at_pt(page, x: float, y: float, number: int = 1) -> tuple[float, float]:
    """A point of the page AS DISPLAYED, in viewport pixels."""
    box = page_box(page, number)
    scale = scale_of(page, number)
    return box["x"] + x * scale, box["y"] + y * scale


def drag(page, a: tuple[float, float], b: tuple[float, float], number: int = 1) -> None:
    ax, ay = at_pt(page, *a, number)
    bx, by = at_pt(page, *b, number)
    page.mouse.move(ax, ay)
    page.mouse.down()
    page.mouse.move(ax + 4, ay + 4)
    page.mouse.move(bx, by, steps=10)
    page.mouse.up()
    page.wait_for_timeout(200)


def pdf_shown(pdf: bytes, rect: tuple[float, float, float, float], rotation: int | None = None, index: int = 0):
    """PyMuPDF: an UNROTATED rect of page `index` -> where the displayed page has it.
    `rotation` overrides the page's /Rotate (a draft turn / a view turn on top)."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        page = doc[index]
        if rotation is not None:
            page.set_rotation(rotation % 360)
        x, y, w, h = rect
        shown = pymupdf.Rect(x, y, x + w, y + h) * page.rotation_matrix
        shown.normalize()
        return shown.x0, shown.y0, shown.width, shown.height
    finally:
        doc.close()


def pdf_point(pdf: bytes, x: float, y: float, rotation: int | None = None, index: int = 0):
    sx, sy, _, _ = pdf_shown(pdf, (x, y, 0, 0), rotation, index)
    return sx, sy


def near(actual, expected, what: str, tol: float = TOL) -> None:
    for name, a, e in zip("xywh", actual, expected):
        approx(a, e, tol, f"{what} {name}")


def rect_of(value: dict) -> tuple[float, float, float, float]:
    return value["x_pt"], value["y_pt"], value["w_pt"], value["h_pt"]


def centre(rect):
    x, y, w, h = rect
    return x + w / 2, y + h / 2


# --------------------------------------------------------------------------
# The checks
# --------------------------------------------------------------------------

@test("engine_layers_land_where_the_pdf_puts_them_at_90_180_270")
def _(page, mock: MockApi) -> None:
    """Form widget, link, text block: unrotated in the model, displayed where PyMuPDF has them."""
    for rotation in (90, 180, 270):
        doc = install(mock, rotation, fields=[field_out("zzf", "text", "pg_000000000001", FIELD, value="ZZ")])
        pdf = doc.pdf_v1()
        open_doc(page, mock)
        equals(shown_rotation(page), 90 if rotation in (90, 270) else 0, f"/Rotate {rotation}: the box is turned")
        field = dom_pt(page, '[data-testid="form-field"][data-name="zzf"]')
        near(field, pdf_shown(pdf, FIELD), f"/Rotate {rotation}: the form widget")
        select_tool(page, "links")
        link = dom_pt(page, '[data-testid="link-lk12"]')
        near(link, pdf_shown(pdf, rect_of(LINK["rect"])), f"/Rotate {rotation}: the link")
        select_tool(page, "edit-text")
        page.wait_for_selector('.tb-block[data-block-id="b0"]', state="attached", timeout=10000)
        block = page.locator('.tb-block[data-block-id="b0"]')
        model = tuple(float(block.get_attribute(f"data-{k}-pt")) for k in ("x", "y", "w", "h"))
        near(dom_pt(page, '.tb-block[data-block-id="b0"]'), pdf_shown(pdf, model), f"/Rotate {rotation}: the text block")
        equals(page.locator('[data-testid="pdf-rotation-note"]').count(), 0, "a real /Rotate is not a view turn")
        if rotation == 90:
            shot(page, "rotfix_engine_90")
    clean(mock)


@test("slots_signatures_and_markup_keep_their_space_at_90_180_270")
def _(page, mock: MockApi) -> None:
    """Displayed-space marks on their points; a v7 text box where the engine draws it."""
    for rotation in (90, 180, 270):
        doc = install(mock, rotation, signed=["sig_1"], links=False)
        pdf = doc.pdf_v1()
        # The server's markup: a legacy tick (displayed) and a text box (unrotated,
        # upright on screen = turned back by the page's own rotation).
        box_u = (120.0, 400.0, 150.0, 30.0)
        doc.markup = [
            {"id": "m1", "kind": "check", "page_uid": doc.uids[0], "x_pt": 330, "y_pt": 210, "size_pt": 16},
            {"id": "t1", "kind": "textbox", "page_uid": doc.uids[0], "x_pt": box_u[0], "y_pt": box_u[1],
             "w_pt": box_u[2], "h_pt": box_u[3], "align": "left", "line_height": 1.2,
             "rotation_deg": (360 - rotation) % 360,
             "runs": [{"text": "ZZ rotated box", "font": "LiberationSans", "size_pt": 11, "color": "black"}]},
        ]
        doc.markup_revision = 1
        open_doc(page, mock)
        page.wait_for_selector('[data-testid="ann-m1"]', state="attached", timeout=10000)
        # The slot and its signature: displayed points, drawn as they are.
        slot_box = dom_pt(page, '.slot[data-field-id="sig_1"]')
        approx(slot_box[0], SLOT["bbox"]["x"], TOL, f"/Rotate {rotation}: slot left")
        approx(slot_box[2], SLOT["bbox"]["width"], TOL, f"/Rotate {rotation}: slot width")
        sig = page.locator('.placed-signature').first
        sig.wait_for(state="attached", timeout=10000)
        sig_box = dom_pt(page, '.placed-signature')
        approx(sig_box[0], float(sig.get_attribute("data-left-pt")), TOL, f"/Rotate {rotation}: signature left = the PDF's")
        approx(sig_box[1] + sig_box[3], float(sig.get_attribute("data-bottom-pt")), TOL,
               f"/Rotate {rotation}: signature bottom = the PDF's")
        equals(page.locator('[data-testid="server-marks-turned"]').count(), 0, "no turned wrapper for a saved turn")
        # The tick: its centre where the legacy builder prints it.
        tick = dom_pt(page, '[data-testid="ann-m1"]')
        cx, cy = centre(tick)
        approx(cx, 330, TOL, f"/Rotate {rotation}: tick x")
        approx(cy, 210, TOL, f"/Rotate {rotation}: tick y")
        # The text box: where PyMuPDF puts the unrotated box's centre, upright.
        shown = page.locator('[data-testid="ann-t1"]')
        stored = tuple(float(shown.get_attribute(f"data-{k}-pt")) for k in ("x", "y", "w", "h"))
        ex, ey = centre(pdf_shown(pdf, box_u))
        sx, sy = centre(stored)
        approx(sx, ex, TOL, f"/Rotate {rotation}: text box centre x")
        approx(sy, ey, TOL, f"/Rotate {rotation}: text box centre y")
        equals((stored[2], stored[3]), (box_u[2], box_u[3]), f"/Rotate {rotation}: upright, its own size")
        # Nothing was rewritten just by opening it.
        equals(len(doc.markup_puts), 0, f"/Rotate {rotation}: nothing PUT on open")
    clean(mock)


@test("a_click_and_a_drag_on_a_turned_page_store_the_points_under_the_pointer")
def _(page, mock: MockApi) -> None:
    doc = install(mock, 90, links=False)
    pdf = doc.pdf_v1()
    open_doc(page, mock)
    # A tick (legacy, displayed points).
    select_tool(page, "check")
    x, y = at_pt(page, 500, 250)
    page.mouse.click(x, y)
    marks = wait_markup(page, doc, lambda els: any(e["kind"] == "check" for e in els))
    tick = next(e for e in marks if e["kind"] == "check")
    approx(tick["x_pt"], 500, TOL, "tick x = the click")
    approx(tick["y_pt"], 250, TOL, "tick y = the click")
    # A text box (v7, unrotated on the wire): its box, mapped by PyMuPDF, is the one on screen.
    select_tool(page, "textbox")
    x, y = at_pt(page, 150, 420)
    page.mouse.click(x, y)
    page.wait_for_selector('[data-testid="textbox-editor"]', timeout=5000)
    page.keyboard.type("ZZ turned page")
    page.keyboard.press("Enter")
    page.wait_for_selector('[data-testid="textbox-editor"]', state="detached", timeout=5000)
    elements = wait_markup(page, doc, lambda els: any(e["kind"] == "textbox" for e in els))
    box = next(e for e in elements if e["kind"] == "textbox")
    equals(box.get("rotation_deg"), 270, "upright on a /Rotate 90 page = 270 on the unrotated one")
    on_screen = page.locator(f'[data-testid="ann-{box["id"]}"]')
    stored = tuple(float(on_screen.get_attribute(f"data-{k}-pt")) for k in ("x", "y", "w", "h"))
    approx(stored[0], 150, TOL, "the box starts at the click (x)")
    ex, ey = centre(pdf_shown(pdf, rect_of(box)))
    approx(centre(stored)[0], ex, TOL, "wire box centre (PyMuPDF) = screen x")
    approx(centre(stored)[1], ey, TOL, "wire box centre (PyMuPDF) = screen y")
    # A form field drawn by dragging.
    select_tool(page, "field-text")
    drag(page, (400, 100), (560, 124))
    ops = wait_ops(page, doc, lambda list_: any(op["kind"] == "field.add" for op in list_))
    field = next(op for op in ops if op["kind"] == "field.add")["field"]["rect"]
    near(pdf_shown(pdf, rect_of(field)), (400, 100, 160, 24), "the field (PyMuPDF) = the drag")
    # A link drawn by dragging.
    select_tool(page, "links")
    drag(page, (100, 500), (220, 520))
    page.locator('[data-testid="link-url"]').fill("https://example.org/zz-rot")
    page.locator('[data-testid="link-save"]').click()
    ops = wait_ops(page, doc, lambda list_: any(op["kind"] == "link.add" for op in list_))
    link = next(op for op in ops if op["kind"] == "link.add")["rect"]
    near(pdf_shown(pdf, rect_of(link)), (100, 500, 120, 20), "the link (PyMuPDF) = the drag")
    near(dom_pt(page, f'[data-testid="link-{next(op for op in ops if op["kind"] == "link.add")["id"]}"]'),
         (100, 500, 120, 20), "and it is framed there")
    # A redaction.
    select_tool(page, "redact")
    drag(page, (300, 300), (380, 330))
    ops = wait_ops(page, doc, lambda list_: any(op["kind"] == "redact" for op in list_))
    red = next(op for op in ops if op["kind"] == "redact")["rects"][0]
    near(pdf_shown(pdf, rect_of(red)), (300, 300, 80, 30), "the redaction (PyMuPDF) = the drag")
    near(dom_pt(page, '.ws-redact-pending'), (300, 300, 80, 30), "drawn black there until saved")
    shot(page, "rotfix_placed_90")
    clean(mock)


@test("a_draft_rotate_turns_the_server_marks_and_the_annotations_with_the_page")
def _(page, mock: MockApi) -> None:
    doc = install(mock, 0, pages=2, signed=["sig_1"], links=False)
    box_u = (120.0, 400.0, 150.0, 30.0)
    doc.markup = [
        {"id": "m1", "kind": "check", "page_uid": doc.uids[0], "x_pt": 330, "y_pt": 210, "size_pt": 16},
        {"id": "t1", "kind": "textbox", "page_uid": doc.uids[0], "x_pt": box_u[0], "y_pt": box_u[1],
         "w_pt": box_u[2], "h_pt": box_u[3], "align": "left", "line_height": 1.2,
         "runs": [{"text": "ZZ box", "font": "LiberationSans", "size_pt": 11, "color": "black"}]},
    ]
    doc.markup_revision = 1
    pdf = doc.pdf_v1()
    open_doc(page, mock)
    page.wait_for_selector('[data-testid="ann-m1"]', state="attached", timeout=10000)
    upright_slot = dom_pt(page, '.slot[data-field-id="sig_1"]')
    page.locator('[data-testid="page-rotate-right-1"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=8000)
    wait_until(page, lambda: shown_rotation(page) == 90, 10000)
    page.wait_for_selector('.pdf-page[data-page-number="1"] [data-testid="server-marks-turned"]', timeout=8000)
    page.wait_for_timeout(400)
    # The slot turns with the page (inert while the session holds the draft).
    near(dom_pt(page, '.slot[data-field-id="sig_1"]'), pdf_shown(pdf, upright_slot, 90), "the slot, turned with its page")
    truthy(page.evaluate("() => getComputedStyle(document.querySelector('.pdf-overlay__frame .slot')).pointerEvents") == "none",
           "and inert")
    # The tick follows the words it was on; the draft's markup says so.
    tx, ty = pdf_point(pdf, 330, 210, 90)
    cx, cy = centre(dom_pt(page, '[data-testid="ann-m1"]'))
    approx(cx, tx, TOL, "tick x after the turn")
    approx(cy, ty, TOL, "tick y after the turn")
    markup = wait_markup(page, doc, lambda els: bool(doc.draft and doc.draft.get("markup")))
    wire_tick = next(e for e in markup if e["id"] == "m1")
    approx(wire_tick["x_pt"], tx, TOL, "wire tick x (displayed, turned)")
    approx(wire_tick["y_pt"], ty, TOL, "wire tick y (displayed, turned)")
    wire_box = next(e for e in markup if e["id"] == "t1")
    near(rect_of(wire_box), box_u, "the text box is still where it was on the UNROTATED page")
    equals(wire_box.get("rotation_deg", 0), 0, "and not turned there")
    shot(page, "rotfix_draft_90")
    # Ctrl+Z: the page and everything on it come back.
    page.locator("body").click(position={"x": 3, "y": 3})
    page.keyboard.press("Control+z")
    wait_until(page, lambda: shown_rotation(page) == 0, 10000)
    page.wait_for_timeout(500)
    cx, cy = centre(dom_pt(page, '[data-testid="ann-m1"]'))
    approx(cx, 330, TOL, "undo: tick x back")
    approx(cy, 210, TOL, "undo: tick y back")
    equals(page.locator('[data-testid="server-marks-turned"]').count(), 0, "undo: no turned wrapper")
    # Turn again and Save: version 2 is /Rotate 90 and the markup stays turned.
    page.locator('[data-testid="page-rotate-right-1"]').click()
    wait_until(page, lambda: shown_rotation(page) == 90, 10000)
    page.wait_for_timeout(600)
    page.locator('[data-testid="editor-save"]').click()
    # The mock treats every op as content (the real server keeps signatures on a turn).
    confirm = page.locator('[data-testid="signatures-dialog-confirm"]')
    try:
        confirm.wait_for(timeout=3000)
        confirm.click()
    except Exception:  # noqa: BLE001 - no dialog: nothing to confirm
        pass
    page.wait_for_selector('[data-testid="workspace"][data-session="view"]', timeout=10000)
    truthy(wait_until(page, lambda: doc.current == 2, 5000), "version 2")
    equals(doc.versions[2]["rotations"][0], 90, "saved turned")
    page.wait_for_timeout(1500)
    tick = next(e for e in doc.markup if e["id"] == "m1")
    approx(tick["x_pt"], tx, TOL, "committed tick x")
    approx(tick["y_pt"], ty, TOL, "committed tick y")
    for put in doc.markup_puts:
        again = next((e for e in put.get("elements", []) if e["id"] == "m1"), None)
        if again:
            approx(again["x_pt"], tx, TOL, "no save after the commit puts the tick back")
    cx, cy = centre(dom_pt(page, '[data-testid="ann-m1"]'))
    approx(cx, tx, TOL, "after the reload: tick x")
    approx(cy, ty, TOL, "after the reload: tick y")
    clean(mock)


@test("a_view_turn_on_a_turned_page_composes_and_says_view_only")
def _(page, mock: MockApi) -> None:
    doc = install(mock, 90, fields=[field_out("zzf", "text", "pg_000000000001", FIELD, value="ZZ")])
    pdf = doc.pdf_v1()
    open_doc(page, mock)
    select_tool(page, "signature")
    equals(page.locator('[data-testid="pdf-rotation-note"]').count(), 0, "a real /Rotate: no notice")
    truthy("pdf-page--interactive" in (page.locator('.pdf-page[data-page-number="1"]').get_attribute("class") or ""),
           "and the page places")
    page.locator('[data-testid="pdf-rotate"]').click()
    wait_until(page, lambda: shown_rotation(page) == 0, 10000)
    page.wait_for_timeout(600)
    note = page.locator('[data-testid="pdf-rotation-note"]')
    equals(note.count(), 1, "the view turn has its notice")
    equals(" ".join(note.inner_text().split()), PLACEMENT_NOTE, "the placement wording")
    equals(page.locator('[data-testid="pdf-rotation-shield-1"]').count(), 1, "and its shield")
    truthy("pdf-page--interactive" not in (page.locator('.pdf-page[data-page-number="1"]').get_attribute("class") or ""),
           "a view-turned page does not place")
    select_tool(page, "links")
    page.wait_for_selector('[data-testid="link-lk12"]', state="attached", timeout=8000)
    # /Rotate 90 + a view turn of 90 = seen at 180.
    near(dom_pt(page, '[data-testid="link-lk12"]'), pdf_shown(pdf, rect_of(LINK["rect"]), 180), "the link, composed")
    near(dom_pt(page, '[data-testid="form-field"][data-name="zzf"]'), pdf_shown(pdf, FIELD, 180), "the widget, composed")
    clean(mock)


@test("the_search_marks_a_word_of_a_turned_page")
def _(page, mock: MockApi) -> None:
    doc = install(mock, 90, links=False)
    pdf = doc.pdf_v1()
    open_doc(page, mock)
    page.locator('[data-testid="pdf-search-input"]').fill("carrier")
    page.wait_for_selector('.pdf-page[data-page-number="1"] .pdf-search-hit', timeout=8000)
    hit = dom_pt(page, '.pdf-search-hit')
    with pymupdf.open(stream=pdf, filetype="pdf") as found:
        word = found[0].search_for("carrier")[0] * found[0].rotation_matrix
    word.normalize()
    # Along the line (turned: top to bottom), the mark is the word, to the advance widths.
    approx(hit[1], word.y0, TOL, "the word starts where PyMuPDF has it")
    approx(hit[1] + hit[3], word.y1, TOL, "and ends there")
    truthy(hit[0] < word.x1 and hit[0] + hit[2] > word.x0, f"across the line, on the word {hit} {tuple(word)}")
    truthy(hit[3] > hit[2], "a turned word is a tall mark")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
