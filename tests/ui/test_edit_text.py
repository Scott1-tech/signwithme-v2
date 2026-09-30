"""
WE: editing the text that is ALREADY in the PDF, and choosing its font.

What a user does (Sejda-style): arm «Edit text» (E), hover a line of the
document -- it is framed --, click it: a box opens right on that line with the
line's own text, in the closest printable face (serif → Liberation Serif,
sans → Liberation Sans, mono → Liberation Mono; bold / italic from the font's
name; size from the run). Enter commits ONE undo step: a `whiteout` over the
line's ink box (+0.5 pt a side) and a `text` on its baseline, sharing a
`group_id`.

These checks pin, in PDF points:

  * the pdf.js text layer exists only while the tool is armed, only on the
    pages of the render window (≤ `MAX_RENDERED_PAGES` = 6) and never on a
    turned page;
  * pdf.js' word-by-word items are merged into lines (and two columns on one
    baseline stay two lines);
  * the box opens with the original text and the right face;
  * the finalize body carries the pair with one `group_id`, the whiteout
    covering the line to the point and the text on the line's baseline
    (±0.5 pt); `bold: false` is never sent;
  * one step for undo / redo; an untouched line adds nothing; a second click
    on an edited line re-opens THAT edit (no second whiteout); deleting either
    member deletes the group; the whiteout cannot be dragged, the text can;
  * Cyrillic and Uzbek `oʻ` go through in Liberation, and the browser refuses
    them in Helvetica -- and an emoji anywhere -- before the server would, by
    name, with the fix;
  * the search still works with the layer on.

Nothing here opens a real contract: the documents are generated below, byte by
byte, and every `/api/*` call is answered by `MockApi`.
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
    missing,
    slot,
    truthy,
)

test = Registry("edit text")
#: F1b (2026-09-29): the WE checks below pinned the whiteout + text commit of the
#: old Edit text tool. The user decided «oq quti TAQIQ» (no white box), and Edit
#: text v2 edits the PDF's own text through the server (`test_text_edit_v2.py`).
#: They are kept here, UNREGISTERED, for the record -- see README «Replaced in F1b».
#: The remaining checks run against a server WITHOUT the document editor, where
#: Edit text still lays the pdf.js lines over the page (and a click only says why).
replaced = Registry("edit text (replaced in F1b)")

PAGE_W = 612.0
PAGE_H = 792.0
#: hooks/usePdfViewer.ts
MAX_RENDERED_PAGES = 6
#: workspace/useAnnotationTools.ts
WHITEOUT_MARGIN_PT = 0.5
STORAGE_KEY = "swm.annotations.ZZ-UI-1"

# --------------------------------------------------------------------------
# The synthetic document
# --------------------------------------------------------------------------

#: Advance widths (1/1000 em) of ASCII 32..126 -- the standard AFM metrics
#: (dumped from reportlab, the library the backend prints with).
_W = {
    "Helvetica": [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584],
    "Helvetica-Bold": [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584],
    "Times-Roman": [250, 333, 408, 500, 500, 833, 778, 180, 333, 333, 500, 564, 250, 333, 250, 278, 500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 278, 278, 564, 564, 564, 444, 921, 722, 667, 667, 722, 611, 556, 722, 722, 333, 389, 722, 611, 889, 722, 722, 556, 722, 667, 556, 611, 722, 722, 944, 722, 722, 611, 333, 278, 333, 469, 500, 333, 444, 500, 444, 500, 444, 333, 500, 500, 278, 278, 500, 278, 778, 500, 500, 500, 500, 333, 389, 278, 500, 500, 722, 500, 500, 444, 480, 200, 480, 541],
    "Times-Italic": [250, 333, 420, 500, 500, 833, 778, 214, 333, 333, 500, 675, 250, 333, 250, 278, 500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 333, 333, 675, 675, 675, 500, 920, 611, 611, 667, 722, 611, 611, 722, 722, 333, 444, 667, 556, 833, 667, 722, 611, 722, 611, 500, 556, 722, 611, 833, 611, 556, 556, 389, 278, 389, 422, 500, 333, 500, 500, 444, 500, 444, 278, 500, 500, 278, 278, 444, 278, 722, 500, 500, 500, 500, 389, 389, 278, 500, 444, 667, 444, 444, 389, 400, 275, 400, 541],
    "Courier": [600] * 95,
}
#: F6 / F7 are second resources for the SAME faces as F1 / F3. pdf.js merges
#: neighbouring text of one font resource into one item by itself, so the
#: split lines alternate between the two -- which is exactly what a real
#: contract does (subset fonts, kerning runs) and what makes pdf.js hand out
#: word-by-word items the edit tool has to join into one line.
_FONT_RES = {"F1": "Helvetica", "F2": "Helvetica-Bold", "F3": "Times-Roman", "F4": "Courier",
             "F5": "Times-Italic", "F6": "Helvetica", "F7": "Times-Roman"}
_RES_OF = {"Helvetica": "F1", "Helvetica-Bold": "F2", "Times-Roman": "F3", "Courier": "F4",
           "Times-Italic": "F5"}
_ALT_RES = {"F1": "F6", "F3": "F7"}


def text_width(text: str, font: str, size: float) -> float:
    return sum(_W[font][ord(c) - 32] for c in text) * size / 1000.0


#: The lines of page 1: (key, font, size, x, baseline from the TOP, text, split).
#: `split` = "words": every word is its own BT..ET block (what word-by-word
#: generators do, and what pdf.js hands out as separate items); an int N =
#: the first N words and the rest are two blocks; None = one Tj.
LINES = [
    ("sans", "Helvetica", 14, 72, 100, "Carrier agrees to deliver the load", "words"),
    ("serif", "Times-Roman", 12, 72, 150, "Payment terms: net thirty days", 2),
    ("bold", "Helvetica-Bold", 16, 72, 200, "TOTAL DUE", None),
    ("mono", "Courier", 10, 72, 240, "REF-2026-0042", None),
    ("italic", "Times-Italic", 12, 72, 280, "Signed in good faith", None),
    ("left", "Helvetica", 12, 72, 330, "Left column", None),
    ("right", "Helvetica", 12, 350, 330, "Right column", None),
    ("short", "Helvetica", 12, 72, 380, "Name", None),
]
LINE = {entry[0]: entry for entry in LINES}


def _blocks(font: str, size: float, x: float, top: float, text: str, split) -> list[str]:
    res = _RES_OF[font]
    y = PAGE_H - top
    if split is None:
        chunks = [(x, text)]
    else:
        words = text.split(" ")
        groups = [[w] for w in words] if split == "words" else [words[:split], words[split:]]
        chunks = []
        cursor = x
        for index, group in enumerate(groups):
            chunk = " ".join(group)
            chunks.append((cursor, chunk))
            cursor += text_width(chunk + " ", font, size)
    out = []
    for index, (cx, chunk) in enumerate(chunks):
        safe = chunk.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        key = _ALT_RES.get(res, res) if index % 2 else res
        out.append(f"BT /{key} {size} Tf 1 0 0 1 {cx:.3f} {y:.3f} Tm ({safe}) Tj ET")
    return out


def _pdf(page_contents: list[str]) -> bytes:
    fonts = " ".join(f"/{key} {3 + i} 0 R" for i, key in enumerate(_FONT_RES))
    objs: list[str] = ["", ""]
    for name in _FONT_RES.values():
        objs.append(f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} /Encoding /WinAnsiEncoding >>")
    first = len(objs) + 1
    kids = []
    for index, content in enumerate(page_contents):
        page_obj = first + index * 2
        kids.append(f"{page_obj} 0 R")
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W:.0f} {PAGE_H:.0f}] "
            f"/Contents {page_obj + 1} 0 R /Resources << /Font << {fonts} >> >> >>"
        )
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    objs[0] = "<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(page_contents)} >>"
    pdf = "%PDF-1.4\n"
    offsets = []
    for index, obj in enumerate(objs):
        offsets.append(len(pdf))
        pdf += f"{index + 1} 0 obj\n{obj}\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    for offset in offsets:
        pdf += f"{offset:010d} 00000 n \n"
    pdf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
    return pdf.encode("latin-1")


def edit_text_pdf(extra_pages: int = 0) -> bytes:
    """Page 1: the lines above (serif, sans, bold, mono, italic, two columns).
    Every further page: 40 dense Helvetica lines -- the scroll / memory case."""
    first = []
    for key, font, size, x, top, text, split in LINES:
        first.extend(_blocks(font, size, x, top, text, split))
    pages = [" ".join(first)]
    for number in range(2, extra_pages + 2):
        blocks = []
        for row in range(40):
            words = f"Page {number} line {row + 1} of the dense synthetic filler text block"
            blocks.extend(_blocks("Helvetica", 10, 72, 60 + row * 17, words, "words"))
        pages.append(" ".join(blocks))
    return _pdf(pages)


def install(mock: MockApi, extra_pages: int = 0) -> None:
    """Slots out of the way (bottom of page 1), the text document in."""
    mock.set_slots(
        [
            slot("sig_1", "signature", 72, 640, 180, 34, rule_y=679.5,
                 label="Carrier signature", paired_date="date_1"),
            slot("date_1", "date", 350, 660, 120, 20, rule_y=679.5,
                 label="Carrier date", paired_signature="sig_1"),
        ],
        signed=["sig_1"],
    )
    mock.set_pdf(edit_text_pdf(extra_pages))


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def shot(page, name: str) -> None:
    folder = os.path.join("c:\\", "PTG", ".playwright-mcp", "swm_edittext")
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, f"{name}.png"))


def clean(mock: MockApi) -> None:
    equals(mock.page_errors, [], "no uncaught error")
    equals(mock.unmocked, [], "no unmocked API call")


def open_edit(page, mock: MockApi, extra_pages: int = 0, seed: str | None = None) -> None:
    install(mock, extra_pages)
    if seed is not None:
        goto_blank(page, mock)
        page.evaluate("([k, v]) => localStorage.setItem(k, v)", [STORAGE_KEY, seed])
    page.goto(f"{BASE}/contracts/{mock.contract_id}/edit")
    page.wait_for_selector('[data-testid="editor-toolbar"]', timeout=30000)
    page.wait_for_function(
        "() => { const p = document.querySelector('.pdf-page');"
        " return !!p && p.getBoundingClientRect().width > 300; }",
        timeout=30000,
    )
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_timeout(350)


def arm(page, tool: str) -> None:
    # F1a: Edit text / Add text / Date live in «Text ▾»; `arm_tool` opens it first.
    arm_tool(page, tool)
    page.wait_for_timeout(80)


def wait_layer(page, page_number: int = 1) -> None:
    page.wait_for_selector(
        f'[data-testid="pdf-text-layer-{page_number}"][data-state="ready"]', timeout=20000
    )


LINES_JS = """
(pageNumber) => [...document.querySelectorAll(
  `.pdf-page[data-page-number="${pageNumber}"] .pdf-text-line`)].map((el) => ({
  index: Number(el.dataset.lineIndex),
  text: el.dataset.text,
  x: Number(el.dataset.xPt), y: Number(el.dataset.yPt),
  w: Number(el.dataset.wPt), h: Number(el.dataset.hPt),
  baseline: Number(el.dataset.baselinePt),
  size: Number(el.dataset.fontSize),
  family: el.dataset.fontFamily,
  bold: el.dataset.bold === 'true',
  italic: el.dataset.italic === 'true',
  runs: Number(el.dataset.runs),
}))
"""


def lines(page, page_number: int = 1) -> list[dict]:
    return page.evaluate(LINES_JS, page_number)


def line(page, text: str, page_number: int = 1) -> dict:
    found = [entry for entry in lines(page, page_number) if entry["text"] == text]
    if len(found) != 1:
        raise AssertionError(
            f"expected one line {text!r}, got {len(found)} of {[e['text'] for e in lines(page, page_number)]}"
        )
    return found[0]


def line_locator(page, text: str, page_number: int = 1):
    return page.locator(
        f'.pdf-page[data-page-number="{page_number}"] .pdf-text-line[data-text="{text}"]'
    )


def open_line(page, text: str, page_number: int = 1):
    target = line_locator(page, text, page_number)
    target.scroll_into_view_if_needed()
    target.click()
    return page.wait_for_selector('[data-testid="ann-input"][data-mode="edit-text"]', timeout=10000)


def retype(page, value: str) -> None:
    box = page.locator('[data-testid="ann-input"]')
    box.fill(value)
    page.wait_for_timeout(80)


def stored(page) -> list:
    raw = page.evaluate("(key) => localStorage.getItem(key)", STORAGE_KEY)
    return json.loads(raw) if raw else []


def depth(page, which: str) -> int:
    return int(page.locator(f'[data-testid="{which}-depth"]').inner_text().strip())


def wait_stored(page, count: int) -> None:
    page.wait_for_function(
        "([key, n]) => { const raw = localStorage.getItem(key);"
        " return (raw ? JSON.parse(raw).length : 0) === n; }",
        arg=[STORAGE_KEY, count],
        timeout=10000,
    )


def pair(items: list) -> tuple[dict, dict]:
    whiteouts = [i for i in items if i["kind"] == "whiteout"]
    texts = [i for i in items if i["kind"] == "text"]
    equals(len(whiteouts), 1, f"one whiteout ({items})")
    equals(len(texts), 1, f"one text ({items})")
    return whiteouts[0], texts[0]


def finalize_body(page, mock: MockApi) -> list:
    apply_dates_to_all(page)
    page.locator('[data-testid="editor-finalize"]').click()
    page.wait_for_function("() => window.location.pathname.endsWith('/final')", timeout=20000)
    return (mock.bodies.get("finalize") or {}).get("annotations") or []


def layer_pages(page) -> list[int]:
    return page.evaluate(
        "() => [...document.querySelectorAll('.pdf-text-layer')]"
        ".map((el) => Number(el.dataset.pageNumber)).sort((a, b) => a - b)"
    )


# --------------------------------------------------------------------------
# The text layer
# --------------------------------------------------------------------------

@test("the_text_layer_is_there_only_with_the_tool_and_only_in_the_window")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock, extra_pages=11)  # 12 pages
    equals(page.locator(".pdf-text-layer").count(), 0, "no text layer while Select is armed")
    page.keyboard.press("e")
    page.wait_for_timeout(100)
    equals(
        page.locator('[data-testid="tool-edit-text"]').get_attribute("aria-pressed"), "true",
        "E arms Edit text",
    )
    wait_layer(page, 1)
    window = page.evaluate(
        "() => [...document.querySelectorAll('.pdf-page canvas.pdf-canvas')]"
        ".filter((c) => c.width > 0).length"
    )
    pages_with_layer = layer_pages(page)
    truthy(1 in pages_with_layer, f"page 1 has one ({pages_with_layer})")
    truthy(len(pages_with_layer) <= MAX_RENDERED_PAGES,
           f"at most {MAX_RENDERED_PAGES} pages carry one ({pages_with_layer})")
    truthy(len(pages_with_layer) <= max(window, 1) + 0,
           f"only pages of the render window ({pages_with_layer}, {window} painted)")
    classes = page.locator('[data-testid="workspace"]').get_attribute("class") or ""
    contains(classes, "workspace--text-tools", "the workspace says the text tools are on")

    # Far down the document: page 1 leaves the window, page 10 enters it.
    page.evaluate(
        "() => document.querySelector('.pdf-page[data-page-number=\"10\"]').scrollIntoView()"
    )
    page.wait_for_function(
        "() => !!document.querySelector('[data-testid=\"pdf-text-layer-10\"]')", timeout=15000
    )
    page.wait_for_timeout(400)
    after = layer_pages(page)
    truthy(1 not in after, f"page 1 dropped its layer ({after})")
    truthy(10 in after, f"page 10 has one ({after})")
    truthy(len(after) <= MAX_RENDERED_PAGES, f"still ≤ {MAX_RENDERED_PAGES} ({after})")
    wait_layer(page, 10)
    ten = lines(page, 10)
    truthy(len(ten) == 40, f"page 10's 40 lines ({len(ten)})")

    arm(page, "select")
    page.wait_for_timeout(200)
    equals(page.locator(".pdf-text-layer").count(), 0, "gone again with Select")
    clean(mock)


@test("words_are_merged_into_lines_and_columns_stay_apart")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm(page, "edit-text")
    wait_layer(page)
    sans = line(page, "Carrier agrees to deliver the load")
    truthy(sans["runs"] >= 6, f"six word items became one line ({sans['runs']} runs)")
    approx(sans["x"], 72, 0.5, "starts at the first word (pt)")
    approx(sans["baseline"], 100, 0.5, "on its baseline (pt)")
    approx(sans["w"], text_width("Carrier agrees to deliver the load", "Helvetica", 14), 1.0,
           "as wide as the words (pt)")
    serif = line(page, "Payment terms: net thirty days")
    truthy(serif["runs"] >= 2, f"two chunks, one line ({serif['runs']})")
    line(page, "Left column")
    line(page, "Right column")
    approx(line(page, "Right column")["x"], 350, 0.5, "the second column is its own line")
    equals(line(page, "TOTAL DUE")["bold"], True, "Helvetica-Bold is bold")
    equals(line(page, "Signed in good faith")["italic"], True, "Times-Italic is italic")
    equals(serif["family"], "serif", "Times is serif")
    equals(line(page, "REF-2026-0042")["family"], "monospace", "Courier is monospace")
    equals(sans["family"], "sans-serif", "Helvetica is sans")
    # The ink box: ascent above, descent below the baseline.
    truthy(sans["y"] <= 100 - 0.7 * 14 and sans["y"] + sans["h"] >= 100 + 0.2 * 14,
           f"the box holds caps and descenders ({sans})")
    clean(mock)


@test("hovering_a_line_frames_it")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm(page, "edit-text")
    wait_layer(page)
    target = line_locator(page, "Carrier agrees to deliver the load")
    before = target.evaluate("(el) => getComputedStyle(el).outlineStyle")
    target.hover()
    page.wait_for_timeout(120)
    after = target.evaluate("(el) => getComputedStyle(el).outlineStyle")
    equals(before, "none", "no frame at rest")
    equals(after, "solid", "a frame under the pointer")
    hit = page.evaluate(
        "() => { const el = document.querySelector('.pdf-text-line[data-text=\"TOTAL DUE\"]');"
        " const r = el.getBoundingClientRect();"
        " return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === el; }"
    )
    truthy(hit, "the line itself is what the pointer reaches")
    shot(page, "hover_frame")
    clean(mock)


# --------------------------------------------------------------------------
# The editor on a line
# --------------------------------------------------------------------------

@replaced("a_click_opens_the_box_with_the_original_text_in_the_right_face")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm(page, "edit-text")
    wait_layer(page)
    cases = [
        ("Payment terms: net thirty days", "LiberationSerif", False, False, 12),
        ("Carrier agrees to deliver the load", "LiberationSans", False, False, 14),
        ("TOTAL DUE", "LiberationSans", True, False, 16),
        ("REF-2026-0042", "LiberationMono", False, False, 10),
        ("Signed in good faith", "LiberationSerif", False, True, 12),
    ]
    for text, font, bold, italic, size in cases:
        box = open_line(page, text)
        equals(box.input_value(), text, f"the box holds {text!r}")
        equals(box.get_attribute("data-font"), font, f"{text!r}: family")
        equals(box.get_attribute("data-bold") == "true", bold, f"{text!r}: bold")
        equals(box.get_attribute("data-italic") == "true", italic, f"{text!r}: italic")
        approx(float(box.get_attribute("data-size-pt")), size, 0.01, f"{text!r}: size")
        equals(page.locator('[data-testid="ann-font"]').input_value(), font, "the toolbar says the family")
        equals(page.locator('[data-testid="ann-bold"]').get_attribute("aria-pressed"),
               "true" if bold else "false", "the toolbar says bold")
        equals(page.locator('[data-testid="ann-italic"]').get_attribute("aria-pressed"),
               "true" if italic else "false", "the toolbar says italic")
        drawn = box.evaluate("(el) => getComputedStyle(el).fontFamily")
        contains(drawn, font, "the box is drawn in that face")
        if text == "TOTAL DUE":
            shot(page, "editor_open")
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        equals(page.locator('[data-testid="ann-input"]').count(), 0, "Esc closes it")
    equals(stored(page), [], "and nothing was added")
    equals(depth(page, "undo"), 0, "not a step either")
    clean(mock)


@replaced("a_commit_is_one_group_one_step_whiteout_over_the_line_text_on_its_baseline")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm(page, "edit-text")
    wait_layer(page)
    original = line(page, "Carrier agrees to deliver the load")
    open_line(page, "Carrier agrees to deliver the load")
    retype(page, "Carrier agrees to deliver the cargo")
    page.keyboard.press("Enter")
    wait_stored(page, 2)
    equals(depth(page, "undo"), 1, "ONE step for the pair")
    whiteout, text = pair(stored(page))
    truthy(whiteout.get("group_id") and whiteout["group_id"] == text.get("group_id"),
           f"one group_id ({whiteout.get('group_id')}, {text.get('group_id')})")
    shot(page, "edited_result")

    # Undo / redo: the pair goes and comes back as one.
    page.keyboard.press("Control+z")
    wait_stored(page, 0)
    page.keyboard.press("Control+y")
    wait_stored(page, 2)

    sent = finalize_body(page, mock)
    w_sent = [e for e in sent if e["kind"] == "whiteout"]
    t_sent = [e for e in sent if e["kind"] == "text"]
    equals(len(w_sent), 1, "one whiteout in the finalize body")
    equals(len(t_sent), 1, "one text in the finalize body")
    w, t = w_sent[0], t_sent[0]
    equals(w["group_id"], t["group_id"], "sharing a group_id on the wire")
    equals(sorted(w.keys()), ["group_id", "h_pt", "id", "kind", "page", "w_pt", "x_pt", "y_pt"],
           "the whiteout carries exactly the v5 keys")
    approx(w["x_pt"], original["x"] - WHITEOUT_MARGIN_PT, 0.02, "whiteout left = line left - 0.5")
    approx(w["y_pt"], original["y"] - WHITEOUT_MARGIN_PT, 0.02, "whiteout top = line top - 0.5")
    approx(w["w_pt"], original["w"] + 2 * WHITEOUT_MARGIN_PT, 0.02, "whiteout width = line + 1")
    approx(w["h_pt"], original["h"] + 2 * WHITEOUT_MARGIN_PT, 0.02, "whiteout height = line + 1")
    width = text_width("Carrier agrees to deliver the load", "Helvetica", 14)
    truthy(w["x_pt"] <= 72 and w["x_pt"] + w["w_pt"] >= 72 + width,
           f"covers the words left to right ({w}, {width:.2f})")
    truthy(w["y_pt"] <= 100 - 0.72 * 14 and w["y_pt"] + w["h_pt"] >= 100 + 0.21 * 14,
           f"covers caps and descenders ({w})")
    approx(t["x_pt"], 72, 0.5, "the text starts where the line did")
    approx(t["y_pt"], 100, 0.5, "on the line's baseline")
    equals(t["text"], "Carrier agrees to deliver the cargo", "the new words")
    equals(t["font"], "LiberationSans", "in Liberation Sans")
    approx(t["size_pt"], 14, 0.01, "at the line's size")
    truthy("bold" not in t and "italic" not in t, f"no bold/italic key for a regular face ({t})")
    truthy("color" not in t, "black: no colour key")
    clean(mock)


@replaced("an_untouched_line_adds_nothing")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm(page, "edit-text")
    wait_layer(page)
    open_line(page, "TOTAL DUE")
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    equals(stored(page), [], "Enter on the same text: nothing")
    open_line(page, "TOTAL DUE")
    # A click elsewhere is a commit too -- still nothing to commit.
    page.mouse.click(5, 5)
    page.wait_for_timeout(200)
    equals(stored(page), [], "a click away: nothing")
    equals(depth(page, "undo"), 0, "no step")
    clean(mock)


@replaced("a_second_click_re_opens_the_same_edit_never_a_second_whiteout")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm(page, "edit-text")
    wait_layer(page)
    open_line(page, "TOTAL DUE")
    retype(page, "TOTAL PAID")
    page.keyboard.press("Enter")
    wait_stored(page, 2)
    box = open_line(page, "TOTAL DUE")
    equals(box.input_value(), "TOTAL PAID", "the box holds the edited text")
    retype(page, "TOTAL OWED")
    page.keyboard.press("Enter")
    page.wait_for_timeout(250)
    items = stored(page)
    whiteout, text = pair(items)
    equals(text["text"], "TOTAL OWED", "the group's text changed")
    equals(text.get("bold"), True, "still bold")
    equals(depth(page, "undo"), 2, "two edits, two steps")
    page.keyboard.press("Control+z")
    page.wait_for_timeout(150)
    equals(pair(stored(page))[1]["text"], "TOTAL PAID", "undo takes back the re-edit only")
    clean(mock)


@replaced("the_whiteout_stays_put_the_text_moves_and_delete_takes_the_group")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm(page, "edit-text")
    wait_layer(page)
    open_line(page, "Signed in good faith")
    retype(page, "Signed in bad faith")
    page.keyboard.press("Enter")
    wait_stored(page, 2)
    whiteout, text = pair(stored(page))

    arm(page, "select")
    t_hit = page.locator(f'[data-testid="ann-{text["id"]}"]')
    box = t_hit.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2 + 4, box["y"] + box["height"] / 2 + 4)
    page.mouse.move(box["x"] + box["width"] / 2 + 60, box["y"] + box["height"] / 2 + 80, steps=8)
    page.mouse.up()
    page.wait_for_timeout(200)
    w2, t2 = pair(stored(page))
    truthy(t2["x_pt"] > text["x_pt"] + 20 and t2["y_pt"] > text["y_pt"] + 30,
           f"the text moved ({text['x_pt']},{text['y_pt']} → {t2['x_pt']},{t2['y_pt']})")
    equals((w2["x_pt"], w2["y_pt"]), (whiteout["x_pt"], whiteout["y_pt"]), "the whiteout did not")
    steps = depth(page, "undo")

    # Now the whiteout is on its own: press and drag it.
    w_hit = page.locator(f'[data-testid="ann-{whiteout["id"]}"]')
    equals(w_hit.get_attribute("data-locked"), "true", "it says it is locked")
    wb = w_hit.bounding_box()
    page.mouse.move(wb["x"] + wb["width"] / 2, wb["y"] + wb["height"] / 2)
    page.mouse.down()
    page.mouse.move(wb["x"] + wb["width"] / 2 + 5, wb["y"] + wb["height"] / 2 + 5)
    page.mouse.move(wb["x"] + wb["width"] / 2 + 90, wb["y"] + wb["height"] / 2 + 60, steps=8)
    page.mouse.up()
    page.wait_for_timeout(200)
    w3, _ = pair(stored(page))
    equals((w3["x_pt"], w3["y_pt"]), (whiteout["x_pt"], whiteout["y_pt"]), "a drag does not move it")
    equals(depth(page, "undo"), steps, "and is no step")
    # Pressing it selected the group: both members.
    equals(t_hit.get_attribute("data-selected"), "true", "its text is selected with it")
    equals(w_hit.get_attribute("data-selected"), "true", "and itself")

    page.keyboard.press("Delete")
    wait_stored(page, 0)
    page.keyboard.press("Control+z")
    wait_stored(page, 2)
    # Deleting the TEXT takes the whiteout too.
    page.locator(f'[data-testid="ann-{text["id"]}"]').click()
    page.wait_for_timeout(100)
    page.locator('[data-testid="ann-delete"]').click()
    wait_stored(page, 0)
    clean(mock)


@replaced("the_objects_list_shows_an_edited_line_with_show_and_revert")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm(page, "edit-text")
    wait_layer(page)
    open_line(page, "Left column")
    retype(page, "Left side")
    page.keyboard.press("Enter")
    wait_stored(page, 2)
    whiteout, text = pair(stored(page))
    group = whiteout["group_id"]
    row = page.locator(f'[data-testid="object-group-{group}"]')
    equals(row.count(), 1, "one row for the group")
    contains(row.inner_text(), "Edited text", "named as an edit")
    contains(row.inner_text(), "Left side", "with its text")
    contains(row.inner_text(), "p.1", "and its page")
    equals(page.locator(f'[data-testid="object-ann-{whiteout["id"]}"]').count(), 0,
           "the whiteout is not a row of its own")
    page.locator(f'[data-testid="object-show-{group}"]').click()
    page.wait_for_timeout(150)
    equals(page.locator('[data-testid="tool-select"]').get_attribute("aria-pressed"), "true",
           "Show switches to Select")
    equals(page.locator(f'[data-testid="ann-{text["id"]}"]').get_attribute("data-selected"), "true",
           "and selects the edit")
    page.locator(f'[data-testid="object-revert-{group}"]').click()
    wait_stored(page, 0)
    equals(depth(page, "undo"), 2, "revert is one step")
    clean(mock)


@test("a_turned_page_has_no_text_layer")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm(page, "edit-text")
    wait_layer(page)
    page.locator('[data-testid="pdf-rotate"]').click()
    page.wait_for_timeout(400)
    equals(page.locator('[data-testid="pdf-text-layer-1"]').count(), 0, "no layer on a turned page")
    equals(page.locator(".pdf-page .pdf-text-line").count(), 0, "no line to click")
    for _ in range(3):
        page.locator('[data-testid="pdf-rotate"]').click()
        page.wait_for_timeout(250)
    wait_layer(page)
    truthy(page.locator(".pdf-text-line").count() > 0, "upright again: the lines are back")
    clean(mock)


# --------------------------------------------------------------------------
# Characters and faces
# --------------------------------------------------------------------------

@replaced("cyrillic_and_uzbek_go_through_in_liberation_and_helvetica_refuses_them_up_front")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm(page, "edit-text")
    wait_layer(page)
    open_line(page, "Carrier agrees to deliver the load")
    retype(page, "Перевозчик oʻzbek gʻalla")
    page.keyboard.press("Enter")
    wait_stored(page, 2)
    _, text = pair(stored(page))
    equals(text["text"], "Перевозчик oʻzbek gʻalla", "stored as typed (NFC)")
    equals(text["font"], "LiberationSans", "in an embedded face")

    # The same words in Helvetica are refused BEFORE anything is stored.
    open_line(page, "Left column")
    page.locator('[data-testid="ann-font"]').select_option("Helvetica")
    page.wait_for_timeout(120)
    equals(page.locator('[data-testid="ann-input"]').count(), 1, "the box stayed open over the font pick")
    retype(page, "Левая колонка")
    page.keyboard.press("Enter")
    error = page.wait_for_selector('[data-testid="ann-input-error"]', timeout=5000)
    contains(error.inner_text(), "Helvetica cannot print", "the face is named")
    contains(error.inner_text(), "U+041B", "and the character")
    contains(error.inner_text(), "Liberation Sans", "and the fix")
    contains(page.locator('[data-testid="editor-note"]').inner_text(), "Helvetica cannot print",
             "said on the page too")
    equals(len(stored(page)), 2, "nothing was added")
    # Taking the fix: Liberation Sans prints it.
    page.locator('[data-testid="ann-font"]').select_option("LiberationSans")
    page.wait_for_timeout(120)
    page.locator('[data-testid="ann-input"]').press("Enter")
    wait_stored(page, 4)

    # The plain Text tool is held to the same rule.
    arm(page, "text")
    page.locator('[data-testid="ann-font"]').select_option("Helvetica")
    box = page.locator('.pdf-page[data-page-number="1"]').bounding_box()
    page.mouse.click(box["x"] + 300, box["y"] + 520)
    page.wait_for_selector('[data-testid="ann-input"]', timeout=5000)
    page.keyboard.type("Oʻzbekiston")
    page.keyboard.press("Enter")
    error = page.wait_for_selector('[data-testid="ann-input-error"]', timeout=5000)
    contains(error.inner_text(), "U+02BB", "the Uzbek okina is named")
    equals(len(stored(page)), 4, "the text tool added nothing either")
    page.keyboard.press("Escape")
    clean(mock)


@replaced("an_emoji_is_refused_with_a_clear_message")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm(page, "edit-text")
    wait_layer(page)
    open_line(page, "Name")
    retype(page, "Name 😀")
    page.keyboard.press("Enter")
    error = page.wait_for_selector('[data-testid="ann-input-error"]', timeout=5000)
    contains(error.inner_text(), "Liberation Sans cannot print", "the face is named")
    contains(error.inner_text(), "U+1F600", "the emoji is named")
    contains(error.inner_text(), "no glyph", "and why")
    equals(stored(page), [], "nothing stored")
    page.keyboard.press("Escape")
    page.wait_for_timeout(100)
    equals(page.locator('[data-testid="ann-input"]').count(), 0, "Esc still cancels")
    clean(mock)


@replaced("bold_false_is_never_sent_and_bold_true_is")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    # A plain run with B switched on and off again: no key.
    arm(page, "text")
    page.locator('[data-testid="ann-bold"]').click()
    page.locator('[data-testid="ann-bold"]').click()
    equals(page.locator('[data-testid="ann-bold"]').get_attribute("aria-pressed"), "false", "B is off")
    box = page.locator('.pdf-page[data-page-number="1"]').bounding_box()
    page.mouse.click(box["x"] + 300, box["y"] + 480)
    page.wait_for_selector('[data-testid="ann-input"]', timeout=5000)
    page.keyboard.type("Plain")
    page.keyboard.press("Enter")
    wait_stored(page, 1)
    # An edited bold line: `bold: true`.
    arm(page, "edit-text")
    wait_layer(page)
    open_line(page, "TOTAL DUE")
    retype(page, "TOTAL DUE NOW")
    page.keyboard.press("Enter")
    wait_stored(page, 3)
    sent = finalize_body(page, mock)
    plain = next(e for e in sent if e.get("text") == "Plain")
    bold = next(e for e in sent if e.get("text") == "TOTAL DUE NOW")
    equals(sorted(plain.keys()), ["font", "id", "kind", "page", "size_pt", "text", "x_pt", "y_pt"],
           "a run nobody bolded is the v5 entry byte for byte")
    equals(bold.get("bold"), True, "bold travels as true")
    truthy("italic" not in bold, "italic false is not sent")
    clean(mock)


@test("text_and_date_tools_take_the_six_families_and_bold_italic")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm(page, "text")
    options = page.evaluate(
        "() => [...document.querySelectorAll('[data-testid=\"ann-font\"] option')].map((o) => o.value)"
    )
    equals(options, ["Helvetica", "Times-Roman", "Courier", "LiberationSans", "LiberationSerif",
                     "LiberationMono"], "six families")
    page.locator('[data-testid="ann-font"]').select_option("LiberationSerif")
    page.locator('[data-testid="ann-bold"]').click()
    page.locator('[data-testid="ann-italic"]').click()
    box = page.locator('.pdf-page[data-page-number="1"]').bounding_box()
    page.mouse.click(box["x"] + 300, box["y"] + 470)
    page.wait_for_selector('[data-testid="ann-input"]', timeout=5000)
    page.keyboard.type("Styled run")
    page.keyboard.press("Enter")
    wait_stored(page, 1)
    entry = stored(page)[0]
    equals((entry["font"], entry.get("bold"), entry.get("italic")),
           ("LiberationSerif", True, True), "stored with its face")
    # `ensureFontLoaded` asked for the face; it arrives from /fonts/liberation/.
    page.wait_for_function("() => document.fonts.check('italic 700 16px LiberationSerif')",
                           timeout=10000)
    drawn = page.evaluate(
        "() => { const t = document.querySelector('.swm-ann__text'); const s = getComputedStyle(t);"
        " return { family: s.fontFamily, weight: s.fontWeight, style: s.fontStyle,"
        " loaded: document.fonts.check('italic 700 16px LiberationSerif') }; }"
    )
    contains(drawn["family"], "LiberationSerif", "drawn in it")
    equals(drawn["weight"], "700", "bold")
    equals(drawn["style"], "italic", "italic")
    truthy(drawn["loaded"], "the face really is loaded (document.fonts)")
    # The date tool, too (the date is always chosen by hand).
    page.locator('[data-testid="ann-date"]').fill("2026-09-26")
    arm(page, "date")
    page.locator('[data-testid="ann-font"]').select_option("LiberationMono")
    page.mouse.click(box["x"] + 300, box["y"] + 540)
    wait_stored(page, 2)
    dated = stored(page)[1]
    equals((dated["kind"], dated["font"], dated.get("bold"), dated.get("italic")),
           ("date", "LiberationMono", True, True), "a date takes the family and the face")
    # A stored entry with a font nobody knows falls back; `bold: "yes"` is dropped.
    clean(mock)


@test("a_run_is_measured_in_its_loaded_face")
def _(page, mock: MockApi) -> None:
    """`getBBox` only after `document.fonts` has the face: the frame is the PDF width."""
    open_edit(page, mock)
    arm(page, "text")
    page.locator('[data-testid="ann-font"]').select_option("Times-Roman")
    box = page.locator('.pdf-page[data-page-number="1"]').bounding_box()
    page.mouse.click(box["x"] + 150, box["y"] + 470)
    page.wait_for_selector('[data-testid="ann-input"]', timeout=5000)
    page.keyboard.type("Payment terms")
    page.keyboard.press("Enter")
    wait_stored(page, 1)
    page.wait_for_timeout(300)
    scale = box["width"] / PAGE_W
    width_px = page.evaluate("() => document.querySelector('.swm-ann__text').getBBox().width")
    expected = text_width("Payment terms", "Times-Roman", 11) * scale
    approx(width_px, expected, 0.6, "SVG width = Times metrics (Liberation Serif is metric-compatible)")
    clean(mock)


@test("stored_rubbish_fonts_and_styles_are_dropped")
def _(page, mock: MockApi) -> None:
    seed = json.dumps([
        {"id": "r1", "kind": "text", "page": 1, "x_pt": 100, "y_pt": 500, "text": "Odd font",
         "font": "Comic Sans", "size_pt": 12, "bold": "yes", "italic": 1},
        {"id": "r2", "kind": "whiteout", "page": 1, "x_pt": 10, "y_pt": 10, "w_pt": 0.2, "h_pt": 5},
        {"id": "r3", "kind": "text", "page": 1, "x_pt": 100, "y_pt": 520, "text": "Good",
         "font": "LiberationMono", "size_pt": 12, "italic": True, "group_id": "bad id!"},
    ])
    open_edit(page, mock, seed=seed)
    page.wait_for_timeout(300)
    items = {i["id"]: i for i in stored(page)}
    equals(sorted(items), ["r1", "r3"], "a too-small whiteout is dropped")
    equals(items["r1"]["font"], "Helvetica", "an unknown family falls back")
    truthy("bold" not in items["r1"] and "italic" not in items["r1"], "only true booleans survive")
    equals(items["r3"].get("italic"), True, "a real italic survives")
    truthy("group_id" not in items["r3"], "a malformed group id is dropped")
    clean(mock)


@replaced("a_longer_text_is_flagged_as_wider_but_not_blocked")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm(page, "edit-text")
    wait_layer(page)
    open_line(page, "Name")
    equals(page.locator('[data-testid="ann-input-wider"]').count(), 0, "the same text fits")
    retype(page, "Name and surname")
    note = page.wait_for_selector('[data-testid="ann-input-wider"]', timeout=5000)
    contains(note.inner_text().lower(), "wider than the original line", "said in words")
    equals(page.locator('[data-testid="ann-input"]').get_attribute("data-wider"), "true", "flagged")
    shot(page, "wider_warning")
    page.keyboard.press("Enter")
    wait_stored(page, 2)
    clean(mock)


@replaced("the_font_list_shows_both_groups")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm(page, "edit-text")
    wait_layer(page)
    open_line(page, "Payment terms: net thirty days")
    groups = page.evaluate(
        "() => [...document.querySelectorAll('[data-testid=\"ann-font\"] optgroup')].map((g) => g.label)"
    )
    equals(len(groups), 2, f"two groups ({groups})")
    contains(groups[1], "Cyrillic", "the embedded group says what it adds")
    # A screenshot of the list: the select drawn open (headless has no native popup).
    page.evaluate(
        "() => { const s = document.querySelector('[data-testid=\"ann-font\"]'); s.size = 8;"
        " s.style.position = 'relative'; s.style.zIndex = 50; s.style.height = 'auto'; }"
    )
    page.wait_for_timeout(150)
    shot(page, "font_list")
    page.evaluate("() => { const s = document.querySelector('[data-testid=\"ann-font\"]'); s.size = 0; s.removeAttribute('style'); }")
    page.keyboard.press("Escape")
    clean(mock)


# --------------------------------------------------------------------------
# Nothing else broke
# --------------------------------------------------------------------------

@test("the_search_still_finds_words_with_the_text_layer_on")
def _(page, mock: MockApi) -> None:
    open_edit(page, mock)
    arm(page, "edit-text")
    wait_layer(page)
    search = page.locator('[data-testid="pdf-search-input"]')
    search.fill("deliver")
    search.press("Enter")
    page.wait_for_function(
        "() => (document.querySelector('[data-testid=\"pdf-search-count\"]')?.textContent || '')"
        ".includes('/ 1')",
        timeout=15000,
    )
    page.wait_for_selector(".pdf-page .pdf-search-hit", timeout=10000)
    hit = page.evaluate(
        "() => { const m = document.querySelector('.pdf-page .pdf-search-hit'); const p = m.closest('.pdf-page');"
        " const s = p.getBoundingClientRect().width / 612; const r = m.getBoundingClientRect();"
        " const q = p.getBoundingClientRect(); return { x: (r.left - q.left) / s, y: (r.bottom - q.top) / s }; }"
    )
    expected_x = 72 + text_width("Carrier agrees to ", "Helvetica", 14)
    approx(hit["x"], expected_x, 1.5, "the mark sits on the word (pt)")
    approx(hit["y"], 100, 1.5, "on its line (pt)")
    # The layer is under the overlay and over the marks.
    order = page.evaluate(
        "() => [...document.querySelector('.pdf-page[data-page-number=\"1\"]').children]"
        ".map((el) => el.className.split(' ')[0])"
    )
    truthy(order.index("pdf-search-layer") < order.index("pdf-text-layer") < order.index("pdf-overlay"),
           f"search < text < overlay ({order})")
    clean(mock)


@test("a_dense_49_page_document_scrolls_with_the_layer_bounded")
def _(page, mock: MockApi) -> None:
    """Measured, not assumed: spans and layers stay bounded while scrolling."""
    open_edit(page, mock, extra_pages=48)
    arm(page, "edit-text")
    wait_layer(page, 1)
    peak_layers = 0
    peak_spans = 0
    peak_lines = 0
    for target in range(1, 50, 4):
        page.evaluate(
            "(n) => document.querySelector(`.pdf-page[data-page-number=\"${n}\"]`).scrollIntoView()",
            target,
        )
        page.wait_for_timeout(260)
        counts = page.evaluate(
            "() => ({ layers: document.querySelectorAll('.pdf-text-layer').length,"
            " spans: document.querySelectorAll('.pdf-text-layer .textLayer span').length,"
            " lines: document.querySelectorAll('.pdf-text-line').length })"
        )
        peak_layers = max(peak_layers, counts["layers"])
        peak_spans = max(peak_spans, counts["spans"])
        peak_lines = max(peak_lines, counts["lines"])
    print(f"        measured: ≤{peak_layers} layers, ≤{peak_spans} spans, ≤{peak_lines} lines")
    truthy(peak_layers <= MAX_RENDERED_PAGES, f"layers ≤ {MAX_RENDERED_PAGES} ({peak_layers})")
    # A filler page is 40 lines of 11 words, each word its own pdf.js item and
    # each gap a whitespace item: ≤ 21 spans a line, ≤ 6 pages.
    truthy(peak_spans <= MAX_RENDERED_PAGES * 40 * 21, f"spans bounded ({peak_spans})")
    truthy(peak_lines <= MAX_RENDERED_PAGES * 40 + 10, f"lines bounded ({peak_lines})")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
