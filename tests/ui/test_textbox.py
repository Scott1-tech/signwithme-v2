"""
F2: «Text ▾ → Add text» = the v7 TEXT BOX (EDITOR-V2-PLAN §7.2, B2 `textbox`).

  * with the document editor Add text (T) is the text box; without it, the W2
    run (`tool-text`) exactly as before;
  * several lines (Shift+Enter), Enter saves, Esc cancels, a paste is plain
    text in NFC, the box wraps at its width with the engine's greedy layout;
  * alignment and line spacing, font (any family of `GET /api/fonts`), size,
    colour, B / I; move, resize (8 handles), duplicate, delete -- each ONE undo
    step;
  * the wire is `{kind: 'textbox', runs, align, line_height, x/y/w/h, page_uid}`;
  * «Generate final contract» does NOT send the annotations again when the
    server holds them (B2 `annotations_in_two_places`).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _f2 import (  # noqa: E402
    clean,
    click_pt,
    drag_pt,
    install,
    last_markup,
    open_doc,
    scale_of,
    select_tool,
    shot,
    wait_markup,
    wait_until,
)
from _harness import MockApi, Registry, approx, cli_main, contains, equals, truthy  # noqa: E402

test = Registry("text box (F2)")


def boxes(elements):
    return [element for element in elements if element.get("kind") == "textbox"]


def text_of(element) -> str:
    return "".join(run["text"] for run in element["runs"])


def add_box(page, words: list[str], x: float = 100, y: float = 300) -> None:
    select_tool(page, "textbox")
    click_pt(page, x, y)
    page.wait_for_selector('[data-testid="textbox-editor"]', timeout=5000)
    for index, line in enumerate(words):
        if index:
            page.keyboard.press("Shift+Enter")
        page.keyboard.type(line)
    page.keyboard.press("Enter")
    page.wait_for_selector('[data-testid="textbox-editor"]', state="detached", timeout=5000)


@test("add_text_is_the_text_box_with_the_editor_and_the_run_without_it")
def _(page, mock: MockApi) -> None:
    install(mock)
    open_doc(page, mock)
    equals(page.locator('[data-testid="tool-text"]').count(), 0, "no W2 run tool with the editor")
    button = page.locator('[data-testid="tool-textbox"]')
    equals(button.get_attribute("aria-keyshortcuts"), "T", "T arms it")
    contains(button.get_attribute("aria-label") or "", "Add text", "named Add text")
    page.locator("body").click(position={"x": 3, "y": 3})
    page.keyboard.press("t")
    equals(page.locator('[data-testid="tool-textbox"]').get_attribute("aria-pressed"), "true", "T armed the text box")
    clean(mock)


@test("several_lines_wrap_at_the_width_and_the_wire_is_a_textbox")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    add_box(page, ["ZZ first line that is long enough to wrap inside two hundred points", "second paragraph"])
    elements = wait_markup(page, doc, lambda els: len(boxes(els)) == 1)
    box = boxes(elements)[0]
    equals(text_of(box), "ZZ first line that is long enough to wrap inside two hundred points\nsecond paragraph",
           "the text, a newline between the paragraphs")
    equals(box["page_uid"], doc.uids[0], "page uid, never page")
    truthy("page" not in box, "no page key")
    equals(box["align"], "left", "left")
    equals(box["line_height"], 1.2, "1.2")
    equals(box["runs"][0]["font"], "LiberationSans", "Liberation Sans by default")
    approx(box["x_pt"], 100, 0.6, "x at the click")
    approx(box["w_pt"], 200, 0.01, "200 pt wide")
    drawn = page.locator(f'[data-testid="ann-textbox-{box["id"]}"]')
    lines = int(drawn.get_attribute("data-line-count"))
    equals(lines, 3, "the long paragraph wraps once: 3 lines")
    truthy(box["h_pt"] >= 3 * 11 * 1.2 - 2, f"the box grew to its lines ({box['h_pt']})")
    shot(page, "textbox_wrap")
    clean(mock)


@test("esc_cancels_a_paste_is_plain_nfc_text_and_an_empty_box_is_nothing")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    select_tool(page, "textbox")
    click_pt(page, 100, 300)
    page.wait_for_selector('[data-testid="textbox-editor"]')
    page.keyboard.type("thrown away")
    page.keyboard.press("Escape")
    page.wait_for_timeout(1200)
    equals(boxes(last_markup(doc)), [], "Esc: nothing kept")
    click_pt(page, 100, 300)
    page.wait_for_selector('[data-testid="textbox-editor"]')
    page.keyboard.press("Enter")
    page.wait_for_timeout(1200)
    equals(boxes(last_markup(doc)), [], "an empty box is nothing")
    click_pt(page, 100, 300)
    editor = page.locator('[data-testid="textbox-editor"]')
    editor.wait_for()
    # A paste of formatted text with a decomposed «ö» (o + U+0308): plain, NFC.
    editor.evaluate(
        """(el) => { const data = new DataTransfer(); data.setData('text/plain', 'Zo\\u0308rich');
                     data.setData('text/html', '<b>bold</b>');
                     el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true })); }"""
    )
    page.wait_for_timeout(100)
    equals(editor.input_value(), "Zörich", "the plain text, composed")
    page.keyboard.press("Enter")
    elements = wait_markup(page, doc, lambda els: len(boxes(els)) == 1)
    equals(text_of(boxes(elements)[0]), "Zörich", "sent in NFC")
    clean(mock)


@test("align_line_spacing_font_size_colour_restyle_the_selected_box_one_step_each")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    add_box(page, ["Styled box", "two"])
    wait_markup(page, doc, lambda els: len(boxes(els)) == 1)
    depth = int(page.locator('[data-testid="undo-depth"]').inner_text())
    page.locator('[data-testid="ann-align"]').select_option("center")
    page.locator('[data-testid="ann-line-height"]').select_option("2")
    elements = wait_markup(page, doc, lambda els: boxes(els) and boxes(els)[0]["align"] == "center"
                           and boxes(els)[0]["line_height"] == 2)
    box = boxes(elements)[0]
    equals((box["align"], box["line_height"]), ("center", 2), "centred, double spacing")
    equals(int(page.locator('[data-testid="undo-depth"]').inner_text()), depth + 2, "two steps")
    # Size and colour of every run.
    size = page.locator('[data-testid="ann-size"]')
    size.fill("18")
    size.press("Tab")
    page.locator('[data-testid="ann-color"]').click()
    page.locator('[data-testid="ann-color-blue"]').click()
    page.locator('[data-testid="ann-bold"]').click()
    elements = wait_markup(page, doc, lambda els: boxes(els) and boxes(els)[0]["runs"][0].get("bold") is True)
    run = boxes(elements)[0]["runs"][0]
    equals((run["size_pt"], run["color"], run.get("bold")), (18, "blue", True), "18 pt, blue, bold")
    truthy(boxes(elements)[0]["h_pt"] >= 18 * 2 + 18 * 0.9, "the box grew with the size")
    # Undo takes the steps back one by one.
    page.keyboard.press("Control+z")
    elements = wait_markup(page, doc, lambda els: boxes(els) and not boxes(els)[0]["runs"][0].get("bold"))
    equals(boxes(elements)[0]["runs"][0].get("bold"), None, "Ctrl+Z: bold off again")
    clean(mock)


@test("move_resize_duplicate_edit_and_delete")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    add_box(page, ["Move me"], x=100, y=300)
    box = boxes(wait_markup(page, doc, lambda els: len(boxes(els)) == 1))[0]
    select_tool(page, "select")
    hit = page.locator(f'[data-testid="ann-{box["id"]}"]')
    hit.click()
    # Move: drag the box 50 pt right, 40 pt down.
    drag_pt(page, 110, 305, 160, 345)
    moved = boxes(wait_markup(page, doc, lambda els: boxes(els) and boxes(els)[0]["x_pt"] > 140))[0]
    approx(moved["x_pt"], 150, 1.5, "moved right")
    approx(moved["y_pt"], 340, 1.5, "moved down")
    # Resize by the east handle: narrower.
    handle = page.locator('[data-testid="ann-handle-e"]')
    hb = handle.bounding_box()
    scale = scale_of(page)
    page.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2)
    page.mouse.down()
    page.mouse.move(hb["x"] - 20 * scale, hb["y"] + hb["height"] / 2, steps=8)
    page.mouse.up()
    narrowed = boxes(wait_markup(page, doc, lambda els: boxes(els) and boxes(els)[0]["w_pt"] < 195))[0]
    approx(narrowed["w_pt"], 180, 3.5, "~20 pt narrower (the 3 px drag threshold is not travel)")
    # Duplicate: a copy 12 pt down-right, selected.
    page.locator('[data-testid="ann-duplicate"]').click()
    pair = boxes(wait_markup(page, doc, lambda els: len(boxes(els)) == 2))
    approx(pair[1]["x_pt"] - pair[0]["x_pt"], 12, 0.01, "12 pt right")
    approx(pair[1]["y_pt"] - pair[0]["y_pt"], 12, 0.01, "12 pt down")
    equals(text_of(pair[1]), "Move me", "the same text")
    # Double-click edits the text.
    page.locator(f'[data-testid="ann-{pair[0]["id"]}"]').dblclick()
    editor = page.locator('[data-testid="textbox-editor"]')
    editor.wait_for()
    equals(editor.input_value(), "Move me", "the editor holds the text")
    editor.fill("Edited")
    page.keyboard.press("Enter")
    wait_markup(page, doc, lambda els: any(text_of(b) == "Edited" for b in boxes(els)))
    # Delete the copy.
    page.locator(f'[data-testid="ann-{pair[1]["id"]}"]').click()
    page.keyboard.press("Delete")
    left = boxes(wait_markup(page, doc, lambda els: len(boxes(els)) == 1))
    equals([text_of(b) for b in left], ["Edited"], "the copy went")
    shot(page, "textbox_edited")
    clean(mock)


@test("finalize_does_not_send_the_servers_annotations_again")
def _(page, mock: MockApi) -> None:
    doc = install(mock, signed=["sig_1"])
    open_doc(page, mock)
    add_box(page, ["On the server"])
    wait_markup(page, doc, lambda els: len(boxes(els)) == 1)
    finalize = page.locator('[data-testid="editor-finalize"]')
    truthy(finalize.is_enabled(), "ready to finalize (one signed slot, no date slot)")
    finalize.click()
    truthy(wait_until(page, lambda: mock.bodies.get("finalize") is not None, 10000), "finalize sent")
    body = mock.bodies["finalize"]
    truthy("annotations" not in body, f"no annotations in the body ({sorted(body)})")
    equals(len(boxes(doc.markup)), 1, "they are on the server")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
