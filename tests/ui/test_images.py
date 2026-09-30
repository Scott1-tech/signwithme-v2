"""
F2: Images ▾ (EDITOR-V2-PLAN §7.4, B2 gallery / `image.remove` / `image2` /
`stamp`).

  * the gallery: «Add to gallery…» uploads to `/api/assets`, thumbnails in
    the menu; a pick + a click on the page COPIES it onto the contract
    (`annotation-assets {from_asset_id}`) and places it; removing it from the
    gallery later leaves the placed picture where it is;
  * «New Stamp…»: a ready word or own text, colour, border, and a date line
    that starts EMPTY (no «now», 2026-09-25) -- the wire is a v7 `stamp`;
  * «Delete original image»: a frame over each picture OF the PDF, a click is
    an `image.remove` op (session), shown as «Removed when you save», ✕ keeps it;
  * a placed picture turns (90°) and fades (opacity) -- `image2` -- and duplicates.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _f2 import (  # noqa: E402
    clean,
    click_pt,
    install,
    last_markup,
    ops_of,
    open_doc,
    png_file,
    select_tool,
    shot,
    wait_markup,
    wait_ops,
    wait_until,
)
from _harness import MockApi, Registry, cli_main, contains, equals, truthy  # noqa: E402

test = Registry("images (F2)")
TMP = os.path.join(tempfile.gettempdir(), "swm_f2_ui")


def open_images(page):
    page.locator("body").click(position={"x": 3, "y": 3})
    page.locator('[data-testid="toolgroup-images-toggle"]').click()
    listing = page.locator('[data-testid="toolgroup-images-menu"]')
    listing.wait_for(state="visible")
    return listing


def kinds(elements, kind):
    return [element for element in elements if element.get("kind") == kind]


@test("the_images_menu_holds_image_stamp_delete_original_and_the_gallery")
def _(page, mock: MockApi) -> None:
    install(mock)
    open_doc(page, mock)
    listing = open_images(page)
    tools = listing.evaluate("(m) => [...m.querySelectorAll('[data-testid^=\"tool-\"]')].map((b) => b.dataset.testid)")
    equals(tools, ["tool-image", "tool-stamp", "tool-image-remove"], "Image, Stamp, Delete original image")
    for testid in ("gallery-thumbs", "menu-gallery-add", "menu-new-stamp"):
        equals(listing.locator(f'[data-testid="{testid}"]').count(), 1, f"{testid} in the list")
    clean(mock)


@test("a_gallery_picture_is_copied_onto_the_contract_and_survives_its_removal_from_the_gallery")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    open_images(page)
    page.locator('[data-testid="gallery-file"]').set_input_files(png_file(TMP, "zz-gallery.png", 80, 40))
    truthy(wait_until(page, lambda: len(mock.gallery) == 1, 5000), "uploaded to the gallery")
    asset_id = next(iter(mock.gallery))
    page.wait_for_selector(f'[data-testid="gallery-{asset_id}"] img', timeout=5000)
    page.locator(f'[data-testid="gallery-{asset_id}"]').click()
    equals(page.locator('[data-testid="tool-image"]').get_attribute("aria-pressed"), "true", "the Image tool is armed")
    click_pt(page, 150, 450)
    elements = wait_markup(page, doc, lambda els: len(kinds(els, "image")) == 1)
    placed = kinds(elements, "image")[0]
    equals(mock.gallery_copies, [asset_id], "COPIED with from_asset_id")
    truthy(placed["asset_id"] != asset_id and placed["asset_id"] in mock.assets, "placed with the contract's copy")
    equals(mock.asset_uploads[-1], {"from_asset_id": asset_id}, "the copy request")
    # Remove from the gallery: the placed one stays.
    open_images(page)
    page.locator(f'[data-testid="gallery-remove-{asset_id}"]').click()
    truthy(wait_until(page, lambda: asset_id in mock.gallery_deleted, 5000), "DELETE /api/assets/{id}")
    page.keyboard.press("Escape")
    page.wait_for_timeout(1000)
    equals(len(kinds(last_markup(doc), "image")), 1, "the placed picture is still there")
    page.wait_for_selector(f'[data-testid="ann-image-{placed["id"]}"][data-state="ready"]', timeout=5000)
    shot(page, "images_gallery")
    clean(mock)


@test("a_new_stamp_has_an_empty_date_line_unless_one_is_typed")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    open_images(page)
    page.locator('[data-testid="menu-new-stamp"]').click()
    dialog = page.locator('[data-testid="stamp-dialog"]')
    dialog.wait_for()
    equals(page.locator('[data-testid="stamp-date"]').input_value(), "", "the date line starts empty (no «now»)")
    page.locator('[data-testid="stamp-preset"]').select_option("received")
    page.locator('[data-testid="stamp-save"]').click()
    equals(page.locator('[data-testid="tool-stamp"]').get_attribute("aria-pressed"), "true", "Stamp armed")
    click_pt(page, 100, 500)
    stamp = kinds(wait_markup(page, doc, lambda els: len(kinds(els, "stamp")) == 1), "stamp")[0]
    equals((stamp.get("preset"), stamp.get("text"), stamp.get("color"), stamp.get("border")),
           ("received", None, "red", True), "RECEIVED, red, bordered")
    truthy("date_text" not in stamp, "no date line")
    # Own text + a typed date + blue.
    open_images(page)
    page.locator('[data-testid="menu-new-stamp"]').click()
    page.locator('[data-testid="stamp-mode-text"]').check()
    page.locator('[data-testid="stamp-text"]').fill("ZZ CHECKED")
    page.locator('[data-testid="stamp-date"]').fill("09/30/2026")
    page.locator('[data-testid="stamp-color-blue"]').click()
    page.locator('[data-testid="stamp-save"]').click()
    click_pt(page, 300, 500)
    stamps = kinds(wait_markup(page, doc, lambda els: len(kinds(els, "stamp")) == 2), "stamp")
    second = stamps[1]
    equals((second.get("text"), second.get("date_text"), second.get("color")), ("ZZ CHECKED", "09/30/2026", "blue"),
           "own text, the typed date, blue")
    truthy("preset" not in second, "a text stamp has no preset")
    page.wait_for_selector(f'[data-testid="ann-stamp-date-{second["id"]}"]', state="attached")
    shot(page, "images_stamps")
    clean(mock)


@test("delete_original_image_is_a_real_image_remove_op")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    doc.images[doc.uids[0]] = [{"xref": 17, "bbox": {"x_pt": 300, "y_pt": 80, "w_pt": 120, "h_pt": 60}}]
    open_doc(page, mock)
    select_tool(page, "image-remove")
    frame = page.locator('[data-testid="page-image-1-17"]')
    frame.wait_for()
    contains(frame.get_attribute("title") or "", "removed from the file", "says what it does")
    frame.click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=8000)
    ops = wait_ops(page, doc, lambda list_: any(op["kind"] == "image.remove" for op in list_))
    op = [op for op in ops if op["kind"] == "image.remove"][0]
    equals((op["page_uid"], op["xref"]), (doc.uids[0], 17), "that picture, by xref")
    equals(op["bbox"], {"x_pt": 300, "y_pt": 80, "w_pt": 120, "h_pt": 60}, "with its bbox")
    marker = page.locator('[data-testid="image-removed-17"]')
    marker.wait_for()
    contains(marker.inner_text(), "Removed when you save", "marked until saved")
    equals(page.locator('[data-testid="page-image-1-17"]').count(), 0, "no frame left to click twice")
    page.locator('[data-testid="image-removed-drop-17"]').click()
    wait_ops(page, doc, lambda list_: not any(op["kind"] == "image.remove" for op in list_))
    equals([op for op in ops_of(doc) if op["kind"] == "image.remove"], [], "✕ keeps the picture")
    clean(mock)


@test("a_placed_picture_turns_fades_and_duplicates")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    open_images(page)
    page.locator('[data-testid="gallery-file"]').set_input_files(png_file(TMP, "zz-turn.png", 60, 30))
    truthy(wait_until(page, lambda: len(mock.gallery) == 1, 5000), "in the gallery")
    asset_id = next(iter(mock.gallery))
    page.locator(f'[data-testid="gallery-{asset_id}"]').click()
    click_pt(page, 150, 450)
    placed = kinds(wait_markup(page, doc, lambda els: len(kinds(els, "image")) == 1), "image")[0]
    equals(placed["kind"], "image", "a plain image first (v5 wire)")
    page.locator('[data-testid="ann-style"]').click()
    page.locator('[data-testid="ann-rotate-90"]').click()
    page.locator('[data-testid="ann-opacity"]').select_option("50")
    elements = wait_markup(page, doc, lambda els: kinds(els, "image2") and kinds(els, "image2")[0].get("opacity") == 0.5)
    turned = kinds(elements, "image2")[0]
    equals((turned["rotation_deg"], turned["opacity"]), (90, 0.5), "image2: 90°, 50 %")
    equals(page.locator(f'[data-testid="ann-image-{placed["id"]}"]').get_attribute("data-rotation"), "90", "drawn turned")
    page.locator('[data-testid="ann-duplicate"]').click()
    elements = wait_markup(page, doc, lambda els: len(kinds(els, "image2")) == 2)
    equals(len(kinds(elements, "image2")), 2, "a copy with the same look")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
