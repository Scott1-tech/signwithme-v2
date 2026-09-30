"""
F2: «More fonts…» (EDITOR-V2-PLAN §7.3 FontPicker, B2 `GET /api/fonts`).

  * the font list of an edited block (and of a text box) ends with «More
    fonts…» once `/api/fonts` answered; the dialog lists ONLY its text
    families, each with its licence, what it stands in for («Calibri →
    Carlito (substitute)») and its name set in itself; search by name or by
    the font it replaces;
  * a pick sets the block's family -- the `text.replace` runs carry it.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _f2 import clean, click_pt, install, ops_of, open_doc, select_tool, start_session, wait_markup, wait_ops  # noqa: E402
from _harness import MockApi, Registry, cli_main, contains, equals, truthy  # noqa: E402

test = Registry("more fonts (F2)")


def open_block(page) -> None:
    start_session(page)
    select_tool(page, "edit-text")
    block = page.locator('[data-testid="text-block"]').first
    block.wait_for()
    block.click()
    page.locator('[data-testid="text-toolbar"]').wait_for()


@test("more_fonts_lists_the_servers_families_with_licence_and_substitute")
def _(page, mock: MockApi) -> None:
    install(mock)
    open_doc(page, mock)
    open_block(page)
    page.locator('[data-testid="text-font"]').click()
    page.locator('[data-testid="text-font-more"]').click()
    dialog = page.locator('[data-testid="fonts-dialog"]')
    dialog.wait_for()
    families = page.evaluate(
        "() => [...document.querySelectorAll('[data-testid^=\"fonts-option-\"]')].map((b) => b.dataset.testid.slice(13))")
    equals(families, ["LiberationSans", "Carlito", "Caladea", "DejaVuSans"], "the text families only (no scripts)")
    equals(page.locator('[data-testid="fonts-substitute-Carlito"]').inner_text(), "Calibri → Carlito (substitute)",
           "the substitute chip")
    equals(page.locator('[data-testid="fonts-license-Carlito"]').inner_text(), "SIL Open Font License 1.1", "licence")
    equals(page.locator('[data-testid="fonts-license-DejaVuSans"]').inner_text(), "Bitstream Vera licence", "licence")
    family = page.locator('[data-testid="fonts-preview-Caladea"]').evaluate("(el) => getComputedStyle(el).fontFamily")
    contains(family, "Caladea", "the name is set in itself")
    page.locator('[data-testid="fonts-search"]').fill("calibri")
    families = page.evaluate(
        "() => [...document.querySelectorAll('[data-testid^=\"fonts-option-\"]')].map((b) => b.dataset.testid.slice(13))")
    equals(families, ["Carlito"], "found by the font it replaces")
    clean(mock)


@test("a_font_from_more_fonts_sets_the_blocks_family_in_the_op")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    open_block(page)
    page.locator('[data-testid="text-font"]').click()
    page.locator('[data-testid="text-font-more"]').click()
    page.locator('[data-testid="fonts-option-Carlito"]').click()
    equals(page.locator('[data-testid="text-font"]').get_attribute("data-value"), "Carlito", "the picker shows it")
    page.keyboard.press("End")
    page.keyboard.type(" X")
    page.keyboard.press("Enter")
    ops = wait_ops(page, doc, lambda list_: any(op["kind"] == "text.replace" for op in list_))
    run = [op for op in ops if op["kind"] == "text.replace"][0]["runs"][0]
    equals(run["font"], "Carlito", "the run is set in Carlito")
    truthy(len(ops_of(doc)) == 1, "one op")
    clean(mock)


@test("a_text_box_takes_a_family_from_more_fonts_too")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    select_tool(page, "textbox")
    page.locator('[data-testid="textbox-font"]').click()
    page.locator('[data-testid="textbox-font-more"]').click()
    page.locator('[data-testid="fonts-option-DejaVuSans"]').click()
    click_pt(page, 100, 300)
    page.locator('[data-testid="textbox-editor"]').wait_for()
    equals(page.locator('[data-testid="textbox-editor"]').get_attribute("data-font"), "DejaVuSans", "the editor's face")
    page.keyboard.type("DejaVu box")
    page.keyboard.press("Enter")
    elements = wait_markup(page, doc, lambda els: any(e.get("kind") == "textbox" for e in els))
    box = [e for e in elements if e.get("kind") == "textbox"][0]
    equals(box["runs"][0]["font"], "DejaVuSans", "set in DejaVu Sans")
    clean(mock)


@test("the_text_toolbar_sets_alignment_and_line_spacing_of_the_edited_block")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    open_block(page)
    page.locator('[data-testid="text-align"]').select_option("center")
    page.locator('[data-testid="text-line-height"]').select_option("1.5")
    editor = page.locator('[data-testid="text-block-editor"]')
    editor.focus()
    page.keyboard.press("End")
    page.keyboard.type(" Y")
    page.keyboard.press("Enter")
    ops = wait_ops(page, doc, lambda list_: any(op["kind"] == "text.replace" for op in list_))
    op = [op for op in ops if op["kind"] == "text.replace"][0]
    equals((op.get("align"), op.get("line_height")), ("center", 1.5), "sent with the text.replace")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
