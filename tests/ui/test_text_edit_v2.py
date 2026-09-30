"""
F1b: Edit text v2 -- editing the PDF's OWN text through the server (EDITOR-V2-PLAN §7).

What a user does (Sejda-style): «Edit PDF» (or just E and a click on the
text), hover a block of the document -- it is framed --, click it: an editor
opens exactly on it, in the face the SERVER prints new text in, the caret at
the character boundary nearest to the click (2× = the word, 3× = the row).
Enter / a click elsewhere commits ONE `text.replace` op (same id when the
block is edited again), the draft is autosaved, and the page shows the
server's own preview of it. An emptied block is a `text.delete`. There is no
white box anywhere: not on the screen as an annotation, not in any body.

Replaces `test_edit_text.py`'s WE checks (README «Replaced in F1b»). The
server is `_docmock.DocMock` (B1's endpoints, mocked, with a byte-for-byte
synthetic PDF and ITS text model) -- no real contract, no live database.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _docmock import Line, enable_document, text_width, wait_until  # noqa: E402
from _harness import (  # noqa: E402
    BASE,
    MockApi,
    Registry,
    approx,
    arm_tool,
    cli_main,
    contains,
    equals,
    goto_blank,
    missing,
    slot,
    truthy,
)

test = Registry("text edit v2")

CARRIER = "Carrier: ABC Transportation LLC"
TERMS = "Payment terms: net thirty days"
REF = "REF-2026-0042"
LOCKED = "Locked type3 text"
BLUE = (0.85, 0.93, 1.0)


def page_lines() -> list[Line]:
    return [
        Line(CARRIER, 72, 150, "Helvetica", 12, background=BLUE),
        Line(TERMS, 72, 190, "Times-Roman", 12),
        Line(REF, 72, 230, "Courier", 10),
        Line(LOCKED, 72, 270, "Helvetica", 12, editable=False, reason="type3_font"),
    ]


def install(mock: MockApi, signed: list[str] | None = None, **options):
    mock.set_slots(
        [
            slot("sig_1", "signature", 72, 640, 180, 34, rule_y=679.5, label="Carrier signature",
                 paired_date="date_1"),
            slot("date_1", "date", 350, 660, 120, 20, rule_y=679.5, label="Carrier date",
                 paired_signature="sig_1"),
        ],
        signed=signed if signed is not None else [],
    )
    return enable_document(mock, [page_lines()], **options)


def shot(page, name: str) -> None:
    folder = os.path.join("c:\\", "PTG", ".playwright-mcp", "swm_f1b_textedit")
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, f"{name}.png"))


def clean(mock: MockApi) -> None:
    equals(mock.page_errors, [], "no uncaught error")
    equals(mock.unmocked, [], "no unmocked API call")


def open_doc(page, mock: MockApi, seed_annotations: str | None = None) -> None:
    if seed_annotations is not None:
        goto_blank(page, mock)
        page.evaluate("([k, v]) => localStorage.setItem(k, v)",
                      [f"swm.annotations.{mock.contract_id}", seed_annotations])
    page.goto(f"{BASE}/contracts/{mock.contract_id}/edit")
    page.wait_for_selector('[data-testid="editor-toolbar"]', timeout=30000)
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_selector('[data-testid="workspace"][data-editor="ready"]', timeout=15000)
    page.wait_for_timeout(250)


def start_session(page) -> None:
    page.locator('[data-testid="editor-edit-pdf"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=10000)
    page.wait_for_selector('[data-testid="editor-save-status"][data-status="saved"]', timeout=10000)


def arm_edit(page) -> None:
    arm_tool(page, "edit-text")
    page.wait_for_selector('[data-testid="text-block-layer-1"][data-state="ready"]', timeout=15000)


def block(page, text: str):
    return page.locator(f'.tb-block[data-text="{text}"]')


def click_block(page, text: str, fraction: float = 0.5, clicks: int = 1) -> None:
    box = block(page, text).bounding_box()
    truthy(box is not None, f"block {text!r} is on the page")
    x = box["x"] + box["width"] * fraction
    y = box["y"] + box["height"] / 2
    page.mouse.click(x, y, click_count=clicks)
    page.wait_for_selector('[data-testid="text-block-editor"]', timeout=10000)
    page.wait_for_timeout(80)


def editor_text(page) -> str:
    return page.evaluate("() => document.querySelector('[data-testid=\"text-block-editor\"]').textContent")


def selection(page) -> list[int]:
    return page.evaluate(
        "() => { const s = window.getSelection(); const r = s.getRangeAt(0);"
        " return [r.startOffset, r.endOffset]; }"
    )


def replace_all(page, value: str) -> None:
    page.keyboard.press("Control+A")
    page.keyboard.type(value)
    page.wait_for_timeout(60)


def ops_of(doc) -> list:
    return doc.draft["ops"] if doc.draft else []


def last_put(doc) -> dict:
    truthy(doc.draft_puts, "a PUT was sent")
    return doc.draft_puts[-1]


def wait_ops(page, doc, count: int) -> None:
    truthy(wait_until(page, lambda: doc.draft is not None and len(doc.draft["ops"]) == count, 8000),
           f"the draft holds {count} op(s) (has {ops_of(doc)})")


def wait_status(page, status: str) -> None:
    page.wait_for_selector(f'[data-testid="editor-save-status"][data-status="{status}"]', timeout=10000)


# --------------------------------------------------------------------------
# The layer
# --------------------------------------------------------------------------

@test("the_server_blocks_are_framed_and_a_locked_block_says_why")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    equals(page.locator(".tb-layer").count(), 0, "no layer while Select is armed")
    arm_edit(page)
    equals(page.locator(".pdf-text-line").count(), 0, "not the pdf.js lines: the server's blocks")
    equals(block(page, CARRIER).count(), 1, "one frame per block")
    carrier = block(page, CARRIER)
    approx(float(carrier.get_attribute("data-x-pt")), 72, 0.01, "x (pt)")
    approx(float(carrier.get_attribute("data-baseline-pt")), 150, 0.01, "baseline (pt)")
    approx(float(carrier.get_attribute("data-w-pt")), text_width(CARRIER, "Helvetica", 12), 0.01, "width (pt)")
    equals(carrier.get_attribute("data-family"), "LiberationSans", "the family the server sets it in")
    equals(block(page, TERMS).get_attribute("data-family"), "LiberationSerif", "Times → Liberation Serif")
    equals(block(page, REF).get_attribute("data-family"), "LiberationMono", "Courier → Liberation Mono")
    locked = block(page, LOCKED)
    equals(locked.get_attribute("data-editable"), "false", "the server says it cannot be edited")
    equals(locked.evaluate("(el) => getComputedStyle(el).cursor"), "not-allowed", "not-allowed cursor")
    contains(locked.get_attribute("title") or "", "Type 3", "and why, in words")
    carrier.hover()
    page.wait_for_timeout(120)
    equals(carrier.evaluate("(el) => getComputedStyle(el).borderTopColor"), "rgb(0, 153, 255)", "hover frames it")
    equals(doc.text_gets, [f"1/{doc.uids[0]}"], "the page's text model read once, for version 1")
    locked.click(force=True)
    page.wait_for_timeout(300)
    equals(page.locator('[data-testid="text-block-editor"]').count(), 0, "a locked block opens nothing")
    shot(page, "layer")
    clean(mock)


@test("a_click_starts_the_session_and_opens_the_editor_at_the_nearest_character")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    arm_edit(page)
    equals(page.locator('[data-testid="workspace"]').get_attribute("data-session"), "view", "not editing yet")
    # Just past "Carrier: " (9 chars): the click lands on the boundary nearest to it.
    prefix = text_width("Carrier: ", "Helvetica", 12)
    width = text_width(CARRIER, "Helvetica", 12)
    click_block(page, CARRIER, fraction=(prefix + 0.8) / width)
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=10000)
    equals(len(doc.draft_posts), 1, "the click started the draft (= Edit PDF)")
    equals(editor_text(page), CARRIER, "the editor holds the block's own text")
    equals(selection(page), [9, 9], "caret at the boundary nearest the click")
    editor = page.locator('[data-testid="text-block-editor"]')
    equals(editor.get_attribute("data-family"), "LiberationSans", "set in the server's face")
    equals(editor.get_attribute("data-size"), "12", "at the block's size")
    approx(float(editor.get_attribute("data-baseline-pt")), 150, 0.01, "on the PDF's baseline")
    family = editor.evaluate("(el) => getComputedStyle(el).fontFamily")
    contains(family, "LiberationSans", "the @font-face the server prints with")
    # The page under it: the server's preview WITHOUT this block.
    truthy(wait_until(page, lambda: any("hide=b0" in call for call in doc.previews), 5000),
           f"preview?hide=b0 asked ({doc.previews})")
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-override]', timeout=10000)
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    equals(page.locator('[data-testid="text-block-editor"]').count(), 0, "Esc closes it")
    equals(ops_of(doc), [], "and changes nothing")
    clean(mock)


@test("double_click_selects_the_word_and_triple_click_the_row")
def _(page, mock: MockApi) -> None:
    install(mock)
    open_doc(page, mock)
    start_session(page)
    arm_edit(page)
    width = text_width(TERMS, "Times-Roman", 12)
    at = text_width("Payment te", "Times-Roman", 12) / width
    click_block(page, TERMS, fraction=at, clicks=2)
    equals(selection(page), [8, 13], "2×: the word «terms» (the colon is not part of it)")
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    click_block(page, TERMS, fraction=at, clicks=3)
    equals(selection(page), [0, len(TERMS)], "3×: the whole row")
    clean(mock)


@test("enter_commits_one_text_replace_and_a_re_edit_keeps_its_id")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    start_session(page)
    arm_edit(page)
    click_block(page, CARRIER, fraction=0.97)
    page.keyboard.press("End")
    for _ in range(len("ABC Transportation LLC")):
        page.keyboard.press("Backspace")
    page.keyboard.type("Premier Trucking Group Inc.")
    page.keyboard.press("Enter")
    wait_ops(page, doc, 1)
    op = ops_of(doc)[0]
    equals(op["kind"], "text.replace", "a real edit of the document's text")
    equals(op["page_uid"], doc.uids[0], "on its page (uid)")
    equals(op["target"]["block_id"], "b0", "of its block")
    equals(op["target"]["line_ids"], ["b0l0"], "all its lines")
    equals(op["target"]["expect_text"], CARRIER, "with the text the server must still find")
    equals("".join(r["text"] for r in op["runs"]), "Carrier: Premier Trucking Group Inc.", "the new text")
    run = op["runs"][0]
    equals((run["font"], run["size_pt"], run["color"]), ("LiberationSans", 12, "#000000"), "the block's style")
    truthy("bold" not in run and "italic" not in run, "no false booleans")
    new_width = text_width("Carrier: Premier Trucking Group Inc.", "Helvetica", 12)
    truthy("box" in op and op["box"]["w_pt"] >= new_width - 0.5,
           f"one row grows to fit its text ({op.get('box')}, needs {new_width:.1f})")
    approx(op["box"]["x_pt"], 72, 0.01, "same left edge")
    wait_status(page, "saved")
    # The page is the server's preview of it (no hide), and the frame says «edited».
    truthy(wait_until(page, lambda: any("hide" not in call for call in doc.previews), 5000), "page preview asked")
    equals(block(page, CARRIER).get_attribute("data-edited"), "true", "the block is marked edited")
    first_id = op["id"]
    # Again: the same op is replaced, never a second one.
    click_block(page, CARRIER)
    equals(editor_text(page), "Carrier: Premier Trucking Group Inc.", "re-opened with the edit")
    page.keyboard.press("End")
    page.keyboard.type("!")
    page.keyboard.press("Enter")
    truthy(wait_until(page, lambda: "".join(r["text"] for r in ops_of(doc)[0]["runs"]).endswith("!"), 8000),
           "the edit landed")
    equals(len(ops_of(doc)), 1, "still one op")
    equals(ops_of(doc)[0]["id"], first_id, "with the same id")
    body = json.dumps(doc.draft_puts)
    missing(body, "whiteout", "no whiteout in any draft body")
    shot(page, "edited")
    clean(mock)


@test("an_emptied_block_is_a_text_delete_and_undo_takes_it_back")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    start_session(page)
    arm_edit(page)
    click_block(page, REF)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.keyboard.press("Enter")
    wait_ops(page, doc, 1)
    op = ops_of(doc)[0]
    equals(op["kind"], "text.delete", "an emptied block is deleted")
    truthy("runs" not in op, "a delete carries no runs")
    equals(op["target"]["expect_text"], REF, "of what it held")
    wait_status(page, "saved")
    page.keyboard.press("Control+z")
    truthy(wait_until(page, lambda: doc.draft is not None and doc.draft["ops"] == [], 8000),
           "Ctrl+Z (the 'ops' step) sends the list without it")
    page.keyboard.press("Control+y")
    wait_ops(page, doc, 1)
    clean(mock)


@test("the_mask_is_the_papers_colour_never_white_and_goes_when_the_preview_is_painted")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    # Slow previews: the mask must stand in until the page is painted.
    real = doc.handle

    def slow(api, route, method, sub, query, body_json):
        if "/preview" in sub:
            page.wait_for_timeout(700)
        return real(api, route, method, sub, query, body_json)

    doc.handle = slow
    open_doc(page, mock)
    start_session(page)
    arm_edit(page)
    click_block(page, CARRIER)
    mask = page.wait_for_selector('[data-testid="text-block-mask"]', timeout=3000)
    colour = mask.evaluate("(el) => getComputedStyle(el).backgroundColor")
    rgb = [int(v) for v in colour[colour.index("(") + 1:colour.index(")")].split(",")[:3]]
    truthy(rgb != [255, 255, 255], f"not white ({colour})")
    truthy(abs(rgb[0] - 217) <= 6 and abs(rgb[1] - 237) <= 6 and abs(rgb[2] - 255) <= 6,
           f"the cell's own light blue ({colour})")
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-override]', timeout=10000)
    page.wait_for_function("() => !document.querySelector('[data-testid=\"text-block-mask\"]')", timeout=5000)
    shot(page, "mask_gone")
    clean(mock)


@test("overflow_shows_the_zone_and_shrink_sets_the_fit")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    doc.warnings_for_put = lambda ops: [
        {"op_id": op["id"], "code": "text_overflow", "lines_over": 2, "needed_h_pt": 40.0}
        for op in ops if op["kind"] == "text.replace" and op.get("fit", "wrap") == "wrap"
        and "\n" in "".join(r["text"] for r in op["runs"])
    ]
    open_doc(page, mock)
    start_session(page)
    arm_edit(page)
    click_block(page, TERMS)
    page.keyboard.press("End")
    page.keyboard.press("Shift+Enter")
    page.keyboard.type("second row")
    page.keyboard.press("Shift+Enter")
    page.keyboard.type("third row")
    page.keyboard.press("Enter")
    wait_ops(page, doc, 1)
    notice = page.wait_for_selector('[data-testid="text-overflow"]', timeout=8000)
    contains(page.locator('[data-testid="text-overflow-message"]').inner_text(), "overflows by 2 lines", "said")
    zone = page.locator(".tb-overflow__zone").bounding_box()
    scale = page.locator('.pdf-page[data-page-number="1"]').bounding_box()["width"] / 612
    approx(zone["height"] / scale, 40.0, 1.0, "the zone is as tall as the text needs (pt)")
    shot(page, "overflow")
    page.locator('[data-testid="text-overflow-shrink"]').click()
    truthy(wait_until(page, lambda: ops_of(doc) and ops_of(doc)[0].get("fit") == "shrink", 8000),
           f"fit: shrink sent ({ops_of(doc)})")
    page.wait_for_function("() => !document.querySelector('[data-testid=\"text-overflow\"]')", timeout=5000)
    equals(bool(notice), True, "notice was shown")
    clean(mock)


@test("the_toolbar_restyles_the_block_the_font_list_names_the_substitute_and_offers_a_link")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    start_session(page)
    arm_edit(page)
    click_block(page, TERMS)
    toolbar = page.locator('[data-testid="text-toolbar"]')
    labels = toolbar.inner_text()
    for word in ("B", "I", "Move", "Duplicate", "Delete"):
        contains(labels, word, "on the toolbar")
    # F2: the link tool exists -- «Link» is on the toolbar and opens the link dialog
    # (its op is pinned by test_links.py); before F2 this line asserted its absence.
    contains(labels, "Link", "F2: the Link button is there")
    equals(page.locator('[data-testid="text-link"]').count(), 1, "one Link button")
    page.locator('[data-testid="text-bold"]').click()
    equals(page.locator('[data-testid="text-block-editor"]').get_attribute("data-bold"), "true", "B")
    page.keyboard.press("Control+i")
    equals(page.locator('[data-testid="text-block-editor"]').get_attribute("data-italic"), "true", "Ctrl+I")
    page.locator('[data-testid="text-size-toggle"]').click()
    page.locator('[data-testid="text-size-14"]').click()
    equals(page.locator('[data-testid="text-block-editor"]').get_attribute("data-size"), "14", "size 14")
    page.locator('[data-testid="text-size"]').focus()
    page.keyboard.press("ArrowUp")
    equals(page.locator('[data-testid="text-block-editor"]').get_attribute("data-size"), "15", "↑ = +1")
    page.locator('[data-testid="text-font"]').click()
    chip = page.locator('[data-testid="text-font-substitute"]').inner_text()
    equals(chip, "Times-Roman → Liberation Serif (substitute)", "the substitute is named")
    options = page.locator('.tb-font__option').all_inner_texts()
    equals([o.replace("✓", "").strip() for o in options], ["Liberation Sans", "Liberation Serif", "Liberation Mono"],
           "B1's three families only")
    page.locator('[data-testid="text-font-search"]').fill("mono")
    equals(page.locator('.tb-font__option').count(), 1, "the search filters")
    page.locator('[data-testid="text-font-LiberationMono"]').click()
    equals(page.locator('[data-testid="text-block-editor"]').get_attribute("data-family"), "LiberationMono", "font")
    page.locator('[data-testid="text-color"]').click()
    page.locator('[data-testid="text-color-blue"]').click()
    equals(page.locator('[data-testid="text-block-editor"]').get_attribute("data-color"), "#1D4ED8", "colour")
    shot(page, "toolbar")
    page.locator('[data-testid="text-block-editor"]').press("Enter")
    wait_ops(page, doc, 1)
    run = ops_of(doc)[0]["runs"][0]
    equals((run["font"], run["size_pt"], run["color"], run.get("bold"), run.get("italic")),
           ("LiberationMono", 15, "#1D4ED8", True, True), "the op carries the whole style")
    equals("".join(r["text"] for r in ops_of(doc)[0]["runs"]), TERMS, "text unchanged")
    clean(mock)


@test("duplicate_is_a_new_selected_annotation_in_one_undo_step")
def _(page, mock: MockApi) -> None:
    install(mock)
    open_doc(page, mock)
    start_session(page)
    arm_edit(page)
    click_block(page, REF)
    page.locator('[data-testid="text-duplicate"]').click()
    page.wait_for_timeout(300)
    undo_before = int(page.locator('[data-testid="undo-depth"]').inner_text().strip())
    rows = page.locator('[data-testid^="object-ann-"]')
    equals(rows.count(), 1, "one new annotation")
    contains(rows.first.inner_text(), REF, "with the block's text")
    truthy("is-selected" in (rows.first.get_attribute("class") or ""), "selected at once")
    page.keyboard.press("Escape")
    page.locator('[data-testid="editor-undo"]').click() if page.locator('[data-testid="editor-undo"]').count() else page.keyboard.press("Control+z")
    page.wait_for_timeout(300)
    equals(page.locator('[data-testid^="object-ann-"]').count(), 0, "one undo takes it back")
    truthy(undo_before >= 1, "it was one step")
    clean(mock)


@test("paste_is_plain_text_in_nfc")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    start_session(page)
    arm_edit(page)
    click_block(page, REF)
    page.keyboard.press("Control+A")
    page.evaluate(
        "() => { const dt = new DataTransfer(); dt.setData('text/html', '<b>bold</b>');"
        " dt.setData('text/plain', 'Cafe\\u0301 O\\u02bbzbek');"
        " document.querySelector('[data-testid=\"text-block-editor\"]').dispatchEvent("
        " new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); }"
    )
    page.wait_for_timeout(100)
    equals(page.locator('[data-testid="text-block-editor"] b').count(), 0, "no markup pasted")
    page.keyboard.press("Enter")
    wait_ops(page, doc, 1)
    text = "".join(r["text"] for r in ops_of(doc)[0]["runs"])
    equals(text, "Caf\u00e9 O\u02bbzbek", "plain text, composed (NFC)")
    clean(mock)


@test("an_emoji_is_refused_by_name_before_anything_is_sent")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    start_session(page)
    arm_edit(page)
    click_block(page, REF)
    page.keyboard.press("End")
    page.keyboard.insert_text(" \U0001F600")
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    note = page.locator('[data-testid="editor-note"]').inner_text()
    contains(note, "U+1F600", "the character is named")
    equals(page.locator('[data-testid="text-block-editor"]').count(), 1, "the editor stays open")
    equals(ops_of(doc), [], "nothing sent")
    clean(mock)


@test("without_the_editor_server_edit_text_says_why_and_never_paints_a_white_box")
def _(page, mock: MockApi) -> None:
    mock.set_slots([slot("sig_1", "signature", 72, 640, 180, 34, rule_y=679.5)], signed=[])
    from _docmock import build_pdf
    mock.set_pdf(build_pdf([page_lines()]))
    page.goto(f"{BASE}/contracts/{mock.contract_id}/edit")
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_selector('[data-testid="workspace"][data-editor="unavailable"]', timeout=10000)
    equals(page.locator('[data-testid="editor-session"]').count(), 0, "no Edit PDF without the editor")
    arm_tool(page, "edit-text")
    page.wait_for_selector('[data-testid="pdf-text-layer-1"][data-state="ready"]', timeout=15000)
    page.locator(f'.pdf-text-line[data-text="{CARRIER}"]').click()
    page.wait_for_timeout(200)
    contains(page.locator('[data-testid="editor-note"]').inner_text(), "document editor", "says why")
    equals(page.locator('[data-testid="ann-input"]').count(), 0, "no WE box")
    raw = page.evaluate("(k) => localStorage.getItem(k)", f"swm.annotations.{mock.contract_id}")
    truthy(not raw or "whiteout" not in raw, "no whiteout stored")
    clean(mock)


@test("a_legacy_edited_line_converts_to_a_real_edit")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    legacy = json.dumps([
        {"id": "w1", "kind": "whiteout", "page": 1, "x_pt": 71.5, "y_pt": 138.6, "w_pt": 175, "h_pt": 14.4,
         "group_id": "g1"},
        {"id": "t1", "kind": "text", "page": 1, "x_pt": 72, "y_pt": 150, "text": "Carrier: New Name LLC",
         "font": "LiberationSans", "size_pt": 12, "group_id": "g1"},
    ])
    doc.markup_revision = 0
    open_doc(page, mock, seed_annotations=legacy)
    page.wait_for_selector('[data-testid="object-convert-g1"]', timeout=10000)
    truthy(page.locator('[data-testid="object-revert-g1"]').count() == 1, "Revert is still there")
    page.locator('[data-testid="object-convert-g1"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=10000)
    wait_ops(page, doc, 1)
    op = ops_of(doc)[0]
    equals((op["kind"], op["target"]["block_id"]), ("text.replace", "b0"), "the line under the white box")
    equals("".join(r["text"] for r in op["runs"]), "Carrier: New Name LLC", "with the edited text")
    page.wait_for_function("() => !document.querySelector('[data-testid=\"object-group-g1\"]')", timeout=5000)
    truthy(wait_until(page, lambda: doc.draft and doc.draft["markup"] is not None
                      and all(e["kind"] != "whiteout" for e in doc.draft["markup"]), 8000),
           f"the white box left the markup ({doc.draft and doc.draft['markup']})")
    clean(mock)


def b1_1_carrier_line(model: dict, version: int) -> dict:
    """B1.1: the carrier line as ONE logical line of three spans -- «Carrier:» in
    Helvetica, a glyph-less `synthetic` space (deliberately odd: 30 pt red, so a
    style taken from it shows), «ABC Transportation LLC» in Liberation Sans."""
    if version != 1:
        return model
    block = model["blocks"][0]
    line = block["lines"][0]
    span = line["spans"][0]
    chars = span["chars"]
    head, tail = chars[:8], chars[9:]  # "Carrier:" | " " | "ABC…"
    line["spans"] = [
        {**span, "chars": head},
        {**span, "font": "", "size_pt": 30, "color": "#FF0000", "chars": [], "synthetic": True},
        {**span, "font": "LiberationSans", "chars": tail},
    ]
    return model


@test("a_b1_1_mixed_font_line_with_a_synthetic_space_is_one_block_and_one_op")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    doc.model_hook = b1_1_carrier_line
    open_doc(page, mock)
    start_session(page)
    arm_edit(page)
    equals(block(page, CARRIER).count(), 1, "one frame for the whole logical line (space rebuilt)")
    carrier = block(page, CARRIER)
    equals((carrier.get_attribute("data-size"), carrier.get_attribute("data-family")), ("12", "LiberationSans"),
           "the style sample is a real span, never the synthetic one")
    click_block(page, CARRIER, fraction=0.97)
    editor = page.locator('[data-testid="text-block-editor"]')
    equals(editor.get_attribute("data-size"), "12", "the editor is not in the synthetic span's 30 pt")
    equals(editor_text(page), CARRIER, "the whole line, the synthetic space included")
    # The change starts AT the synthetic space: its style must be a real neighbour's.
    page.keyboard.press("End")
    for _ in range(len(" ABC Transportation LLC")):
        page.keyboard.press("Shift+ArrowLeft")
    page.keyboard.type("-Premier Trucking Group Inc.")
    page.keyboard.press("Enter")
    wait_ops(page, doc, 1)
    op = ops_of(doc)[0]
    equals(op["target"]["line_ids"], ["b0l0"], "one op for the one logical line (no duplicate_target)")
    equals(op["target"]["expect_text"], CARRIER, "expect_text = line.text")
    equals(len(op["runs"]), 1, "the whole line as one run")
    run = op["runs"][0]
    equals((run["text"], run["size_pt"], run["color"]), ("Carrier:-Premier Trucking Group Inc.", 12, "#000000"),
           "no 30 pt / red from the synthetic span")
    wait_status(page, "saved")
    equals(block(page, CARRIER).count(), 1, "still one frame after the edit")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
