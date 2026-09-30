"""
F2: Sign ▾ (EDITOR-V2-PLAN §7.4, B2 typed signatures).

  * the list holds Signature, Initials, the saved signatures' thumbnails,
    «New Signature…» and «My signatures…»;
  * a saved thumbnail + a click on the page places it (no dialog);
  * the signature dialog has a «Type» tab with the scripts of
    `GET /api/fonts?category=signature`: the server renders the PNG
    (`POST /api/signatures {typed: {text, font}}`), it is saved, then placed;
  * without the editor (an older server) there is no Type tab and the menu
    is F1a's.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _f2 import clean, click_pt, install, open_doc, shot, wait_until  # noqa: E402
from _harness import BASE, MockApi, Registry, arm_tool, cli_main, equals, truthy  # noqa: E402

test = Registry("sign menu (F2)")

SAVED = {"id": "zz-lib-saved", "label": "ZZ saved", "is_default": True, "signature_hash": "zz",
         "created_at": "2026-09-23T08:00:00+00:00"}


def open_sign(page):
    page.locator("body").click(position={"x": 3, "y": 3})
    page.locator('[data-testid="toolgroup-sign-toggle"]').click()
    listing = page.locator('[data-testid="toolgroup-sign-menu"]')
    listing.wait_for(state="visible")
    return listing


@test("the_sign_menu_lists_saved_signatures_new_and_my_signatures")
def _(page, mock: MockApi) -> None:
    mock.user_signatures = [dict(SAVED)]
    install(mock)
    open_doc(page, mock)
    listing = open_sign(page)
    tools = listing.evaluate("(m) => [...m.querySelectorAll('[data-testid^=\"tool-\"]')].map((b) => b.dataset.testid)")
    equals(tools, ["tool-signature", "tool-initials"], "the two tools, as before")
    equals(listing.locator('[data-testid="sign-thumbs"]').get_attribute("data-count"), "1", "one saved signature")
    page.wait_for_selector('[data-testid="sign-saved-zz-lib-saved"] img', timeout=5000)
    for testid in ("menu-new-signature", "menu-my-signatures"):
        equals(listing.locator(f'[data-testid="{testid}"]').count(), 1, testid)
    shot(page, "sign_menu")
    clean(mock)


@test("a_saved_thumbnail_is_placed_by_the_next_click_without_a_dialog")
def _(page, mock: MockApi) -> None:
    mock.user_signatures = [dict(SAVED)]
    mock.place_echo = True
    install(mock)
    open_doc(page, mock)
    open_sign(page)
    page.locator('[data-testid="sign-saved-zz-lib-saved"]').click()
    equals(page.locator('[data-testid="tool-signature"]').get_attribute("aria-pressed"), "true", "Signature armed")
    click_pt(page, 300, 420)
    truthy(wait_until(page, lambda: len(mock.place_bodies) == 1, 8000), "placed")
    body = mock.place_bodies[0]
    equals(body.get("field_id"), None, "a free placement")
    truthy(str(body.get("image_data", "")).startswith("data:image/png;base64,"), "the saved PNG")
    equals(page.locator('.modal').count(), 0, "no dialog")
    clean(mock)


@test("the_type_tab_renders_on_the_server_saves_and_places")
def _(page, mock: MockApi) -> None:
    mock.place_echo = True
    install(mock)
    open_doc(page, mock)
    open_sign(page)
    page.locator('[data-testid="menu-new-signature"]').click()
    click_pt(page, 300, 420)
    tab = page.locator('[role="tab"][data-tab="type"]')
    tab.wait_for()
    tab.click()
    fonts = page.evaluate("() => [...document.querySelectorAll('[data-testid^=\"sign-type-font-\"]')].map((b) => b.dataset.testid.slice(15))")
    equals(fonts, ["GreatVibes", "Allura", "Sacramento"], "the three scripts of /api/fonts?category=signature")
    page.locator('[data-testid="sign-type-text"]').fill("Zed Zulu")
    page.locator('[data-testid="sign-type-font-Allura"]').click()
    preview = page.locator('[data-testid="sign-type-font-Allura"] .sign-type__preview')
    equals(preview.inner_text(), "Zed Zulu", "previewed in the script")
    shot(page, "sign_type")
    page.locator('[data-testid="sign-type-apply"]').click()
    truthy(wait_until(page, lambda: len(mock.typed_saves) == 1, 8000), "POST /api/signatures {typed}")
    equals(mock.typed_saves[0]["typed"], {"text": "Zed Zulu", "font": "Allura"}, "the text and the script")
    truthy(wait_until(page, lambda: len(mock.place_bodies) == 1, 8000), "then placed")
    page.wait_for_selector(".modal", state="detached", timeout=5000)
    clean(mock)


@test("without_the_editor_there_is_no_type_tab_and_no_extras")
def _(page, mock: MockApi) -> None:
    page.goto(f"{BASE}/contracts/{mock.contract_id}/sign")
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.locator("body").click(position={"x": 3, "y": 3})
    page.locator('[data-testid="toolgroup-sign-toggle"]').click()
    equals(page.locator('[data-testid="menu-extras-sign"]').count(), 0, "no extras on an older server")
    page.keyboard.press("Escape")
    arm_tool(page, "signature")
    click_pt(page, 300, 420)
    page.wait_for_selector(".modal", timeout=5000)
    equals(page.locator('[role="tab"][data-tab="type"]').count(), 0, "no Type tab")
    equals(page.locator('[role="tab"]').count(), 3, "Saved, Draw, Upload")
    equals(mock.page_errors, [], "no uncaught error")


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
