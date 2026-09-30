"""
F2: the page panel (EDITOR-V2-PLAN §7.4, B2 `page.insert / delete / move /
rotate`) on the thumbnail strip.

  * «Insert page here» between the thumbnails, ✕ delete, drag to move, ◀ ▶
    rotate -- REAL changes of the document (ops of the edit session, which
    starts by itself), set apart from the viewer's ⟳ (view only);
  * after a page op the viewer shows the WHOLE draft (`draft/preview.pdf`,
    order from `X-Page-Uids`), the reader's page is found again by uid, the
    search runs again on the new pages;
  * the delete dialog says what is on the page (signatures, slots, links,
    annotations); a page with slots is refused to the uploader (reviewer /
    admin only) BEFORE anything is sent;
  * annotations and slots follow their page; Ctrl+Z undoes a page op.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _f2 import (  # noqa: E402
    clean,
    click_pt,
    install,
    last_markup,
    ops_of,
    open_doc,
    select_tool,
    shot,
    wait_markup,
    wait_ops,
    wait_until,
    workspace_attr,
)
from _harness import MockApi, Registry, cli_main, contains, equals, slot, truthy  # noqa: E402

test = Registry("pages (F2)")


def shown(page) -> list[str]:
    return (workspace_attr(page, "data-pages") or "").split(",")


def page_ops(doc) -> list[dict]:
    return [op for op in ops_of(doc) if op["kind"].startswith("page.")]


def wait_shown(page, count: int, timeout: int = 10000) -> list[str]:
    page.wait_for_function(
        "(n) => (document.querySelector('[data-testid=\"workspace\"]').dataset.pages || '').split(',').length === n"
        " && document.querySelectorAll('.pdf-page').length === n",
        arg=count,
        timeout=timeout,
    )
    page.wait_for_timeout(300)
    return shown(page)


@test("insert_starts_the_session_and_the_viewer_shows_the_draft")
def _(page, mock: MockApi) -> None:
    doc = install(mock, pages=3)
    open_doc(page, mock)
    equals(shown(page), doc.uids, "the current version's pages")
    page.locator('[data-testid="page-insert-1"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=8000)
    order = wait_shown(page, 4)
    ops = page_ops(doc)
    equals(len(ops), 1, "one op")
    equals((ops[0]["kind"], ops[0]["after_page_uid"], ops[0]["size"]), ("page.insert", doc.uids[0], "same_as_previous"),
           "after page 1, the size of the one before")
    truthy(ops[0]["new_page_uid"].startswith("pg_") and len(ops[0]["new_page_uid"]) == 15, "a fresh pg_ uid")
    equals(order, [doc.uids[0], ops[0]["new_page_uid"], doc.uids[1], doc.uids[2]], "the draft's order, from X-Page-Uids")
    truthy(len(doc.draft_pdf_gets) >= 1 and doc.draft_pdf_gets[-1].startswith("revision="),
           f"the WHOLE draft was fetched ({doc.draft_pdf_gets})")
    equals(page.locator('.pdf-page').count(), 4, "four pages on screen")
    contains(page.locator('[data-testid="page-insert-0"]').get_attribute("title") or "", "Insert page here", "a tooltip")
    shot(page, "pages_insert")
    clean(mock)


@test("delete_asks_with_what_is_on_the_page_and_the_uploader_cannot_delete_a_slot_page")
def _(page, mock: MockApi) -> None:
    mock.role = "user"
    doc = install(mock, pages=2, slots=[slot("sig_1", "signature", 72, 640, 180, 34, rule_y=679.5, page=2)])
    doc.links[doc.uids[1]] = [{"link_id": "lk7", "rect": {"x_pt": 72, "y_pt": 200, "w_pt": 50, "h_pt": 12},
                               "target": {"type": "uri", "uri": "https://example.com"}}]
    doc.markup = [{"id": "m1", "kind": "check", "page_uid": doc.uids[1], "x_pt": 300, "y_pt": 300, "size_pt": 16}]
    doc.markup_revision = 1
    open_doc(page, mock)
    page.locator('[data-testid="page-delete-2"]').click()
    dialog = page.locator('[data-testid="page-delete-dialog"]')
    dialog.wait_for()
    counts = page.locator('[data-testid="page-delete-counts"]')
    page.wait_for_function(
        "() => document.querySelector('[data-testid=\"page-delete-counts\"]').dataset.links !== ''", timeout=5000)
    equals(counts.get_attribute("data-slots"), "1", "the slot is counted")
    equals(counts.get_attribute("data-links"), "1", "the link is counted")
    equals(counts.get_attribute("data-annotations"), "1", "the annotation is counted")
    contains(counts.inner_text(), "1 signature / date slot", "said in words")
    contains(page.locator('[data-testid="page-delete-reason"]').inner_text(), "only a reviewer or an admin",
             "the uploader is told why")
    truthy(page.locator('[data-testid="page-delete-confirm"]').is_disabled(), "and cannot confirm")
    page.locator('[data-testid="page-delete-cancel"]').click()
    equals(page_ops(doc), [], "nothing sent")
    # Page 1 (no slot) is fine for them.
    page.locator('[data-testid="page-delete-1"]').click()
    equals(page.locator('[data-testid="page-delete-reason"]').count(), 0, "no refusal for a page without slots")
    page.locator('[data-testid="page-delete-confirm"]').click()
    wait_shown(page, 1)
    equals([op["kind"] for op in page_ops(doc)], ["page.delete"], "page.delete")
    equals(page_ops(doc)[0]["page_uid"], doc.uids[0], "of page 1")
    clean(mock)


@test("an_admin_deletes_a_slot_page_and_undo_brings_it_back_with_its_annotations")
def _(page, mock: MockApi) -> None:
    doc = install(mock, pages=3)
    doc.markup = [{"id": "m1", "kind": "check", "page_uid": doc.uids[1], "x_pt": 300, "y_pt": 300, "size_pt": 16},
                  {"id": "m3", "kind": "check", "page_uid": doc.uids[2], "x_pt": 200, "y_pt": 200, "size_pt": 16}]
    doc.markup_revision = 1
    open_doc(page, mock)
    page.wait_for_selector('[data-testid="ann-m3"]', state="attached")
    page.locator('[data-testid="page-delete-2"]').click()
    equals(page.locator('[data-testid="page-delete-counts"]').get_attribute("data-annotations"), "1", "one annotation")
    page.locator('[data-testid="page-delete-confirm"]').click()
    wait_shown(page, 2)
    equals(shown(page), [doc.uids[0], doc.uids[2]], "page 2 is gone")
    # The page-3 check is now on page 2 of the screen; the page-2 one is set aside.
    equals(page.locator('[data-testid="ann-m3"]').get_attribute("data-page"), "2", "m3 follows its page")
    equals(page.locator('[data-testid="ann-m1"]').count(), 0, "m1 went with its page")
    markup = wait_markup(page, doc, lambda els: els is not None and len(els) == 1)
    equals([(e["id"], e["page_uid"]) for e in markup], [("m3", doc.uids[2])], "the wire keeps m3's uid, m1 is not sent")
    page.locator("body").click(position={"x": 3, "y": 3})
    page.keyboard.press("Control+z")
    wait_shown(page, 3)
    equals(shown(page), doc.uids, "Ctrl+Z: back")
    page.wait_for_selector('[data-testid="ann-m1"]', state="attached")
    equals(page.locator('[data-testid="ann-m1"]').get_attribute("data-page"), "2", "m1 back on page 2")
    equals(page.locator('[data-testid="ann-m3"]').get_attribute("data-page"), "3", "m3 back on page 3")
    clean(mock)


@test("drag_moves_a_page_and_the_slot_and_the_search_follow_it")
def _(page, mock: MockApi) -> None:
    doc = install(mock, pages=3, slots=[slot("sig_1", "signature", 72, 640, 180, 34, rule_y=679.5, page=3)])
    open_doc(page, mock)
    page.wait_for_selector('.pdf-page[data-page-number="3"] .slot[data-field-id="sig_1"]', state="attached")
    source = page.locator('[data-testid="page-item-3"]')
    target = page.locator('[data-testid="page-item-1"]')
    source.drag_to(target)
    order = wait_shown(page, 3)
    ops = page_ops(doc)
    equals([op["kind"] for op in ops], ["page.move"], "page.move")
    equals((ops[0]["page_uid"], ops[0]["after_page_uid"]), (doc.uids[2], None), "page 3 to the front")
    equals(order, [doc.uids[2], doc.uids[0], doc.uids[1]], "the new order")
    page.wait_for_selector('.pdf-page[data-page-number="1"] .slot[data-field-id="sig_1"]', state="attached",
                           timeout=8000)
    equals(page.locator('.pdf-page[data-page-number="3"] .slot[data-field-id="sig_1"]').count(), 0,
           "the slot is on the moved page's new position")
    # The search runs again on the draft: «Page 3» text is now on page 1.
    search = page.locator('[data-testid="pdf-search-input"]')
    search.fill("Page 3 carrier")
    page.wait_for_function(
        "() => document.querySelector('.pdf-page[data-page-number=\"1\"] .pdf-search-hit') !== null", timeout=8000)
    equals(page.locator('.pdf-page[data-page-number="3"] .pdf-search-hit').count(), 0, "not on the old page")
    clean(mock)


@test("rotate_is_a_real_absolute_page_rotate_apart_from_the_view_turn")
def _(page, mock: MockApi) -> None:
    doc = install(mock, pages=2)
    open_doc(page, mock)
    contains(page.locator('[data-testid="pdf-rotate"]').get_attribute("title") or "", "view only",
             "the viewer's ⟳ says view only")
    right = page.locator('[data-testid="page-rotate-right-2"]')
    contains(right.get_attribute("aria-label") or "", "saved with the document", "the strip's says it is real")
    right.click()
    wait_ops(page, doc, lambda ops: any(op["kind"] == "page.rotate" for op in ops))
    right.click()
    ops = wait_ops(page, doc, lambda ops: any(op["kind"] == "page.rotate" and op["rotation"] == 180 for op in ops))
    rotates = [op for op in ops if op["kind"] == "page.rotate"]
    equals([(op["page_uid"], op["rotation"]) for op in rotates], [(doc.uids[1], 180)],
           "ONE absolute op per page (90 then 180)")
    page.wait_for_function(
        "() => document.querySelector('[data-testid=\"page-rotation-2\"]').textContent.includes('180')", timeout=8000)
    page.locator('[data-testid="page-rotate-left-2"]').click()
    ops = wait_ops(page, doc, lambda ops: any(op["kind"] == "page.rotate" and op["rotation"] == 90 for op in ops))
    equals([op["rotation"] for op in ops if op["kind"] == "page.rotate"], [90], "◀ turns it back to 90")
    equals(page.locator('.pdf-page[data-rotation]').count(), 0, "no view-only turn was made")
    clean(mock)


@test("save_commits_the_new_order_and_the_annotations_stay_on_their_page")
def _(page, mock: MockApi) -> None:
    doc = install(mock, pages=2)
    open_doc(page, mock)
    select_tool(page, "square")
    click_pt(page, 200, 200, number=2)
    wait_markup(page, doc, lambda els: len(els) == 1)
    uid_before = last_markup(doc)[0]["page_uid"]
    equals(uid_before, doc.uids[1], "the square is on page 2")
    page.locator('[data-testid="page-insert-0"]').click()
    page.wait_for_timeout(300)
    wait_shown(page, 3)
    equals(page.locator('.pdf-page[data-page-number="3"] [data-kind="square_mark"]').count() >= 1, True,
           "the square moved to page 3 of the screen")
    page.locator('[data-testid="editor-save"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="view"]', timeout=10000)
    truthy(wait_until(page, lambda: doc.current == 2, 5000), "version 2")
    equals(len(doc.versions[2]["uids"]), 3, "three pages in v2")
    wait_shown(page, 3)
    equals([e["page_uid"] for e in doc.markup], [doc.uids[1]], "the square keeps its page's uid on the server")
    page.wait_for_selector('.pdf-page[data-page-number="3"] [data-kind="square_mark"]', state="attached")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
