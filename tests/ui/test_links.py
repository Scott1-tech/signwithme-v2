"""
F2: Links (EDITOR-V2-PLAN §7.2, B2 `link.add` / `link.update` / `link.remove`).

  * the Links tool exists only with the document editor (next to Text ▾);
  * drag a box → the dialog: a web / mail address (javascript:, file:, data:
    and host-less addresses are refused IN the dialog) or a page of this
    document → a `link.add` op in the draft (the session starts if needed);
  * the page's existing links are framed while the tool is armed: click =
    edit (never followed), drag = move, corner = resize, Delete → ops
    `link.update` / `link.remove`; each change ONE undo step;
  * «Link» on the text toolbar makes the edited block a link.
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
    ops_of,
    open_doc,
    select_tool,
    shot,
    wait_ops,
    workspace_attr,
)
from _harness import MockApi, Registry, approx, cli_main, contains, equals, truthy  # noqa: E402

test = Registry("links (F2)")

SERVER_LINK = {"link_id": "lk12", "rect": {"x_pt": 72, "y_pt": 300, "w_pt": 100, "h_pt": 20},
               "target": {"type": "uri", "uri": "https://example.com/zz"}}


def link_ops(doc, kind: str | None = None):
    return [op for op in ops_of(doc) if op["kind"].startswith("link.") and (kind is None or op["kind"] == kind)]


@test("links_is_on_the_row_only_with_the_editor")
def _(page, mock: MockApi) -> None:
    install(mock)
    open_doc(page, mock)
    row = page.evaluate("() => [...document.querySelectorAll('.ws-toolbar__tools .editor__tool')].map((b) => b.dataset.testid)")
    equals(row[:3], ["tool-select", "toolgroup-text", "tool-links"], "Select | Text ▼ | Links | …")
    link = page.locator('[data-testid="tool-links"]')
    truthy(len(link.get_attribute("aria-label") or "") > 5 and len(link.get_attribute("title") or "") > 5,
           "named, with a tooltip")
    clean(mock)


@test("a_dragged_box_becomes_a_web_link_and_bad_addresses_are_refused")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    select_tool(page, "links")
    drag_pt(page, 100, 400, 220, 420)
    dialog = page.locator('[data-testid="link-dialog"]')
    dialog.wait_for()
    for bad, why in (("javascript:alert(1)", "Only web"), ("ftp://x.test", "Start the address"),
                     ("https://", "Start the address")):
        page.locator('[data-testid="link-url"]').fill(bad)
        page.locator('[data-testid="link-save"]').click()
        contains(page.locator('[data-testid="link-error"]').inner_text(), why, f"{bad} refused")
    equals(ops_of(doc), [], "nothing sent while refused")
    page.locator('[data-testid="link-url"]').fill("https://example.org/zz-terms")
    page.locator('[data-testid="link-save"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=8000)
    ops = wait_ops(page, doc, lambda list_: len(link_ops_of(list_)) == 1)
    op = link_ops_of(ops)[0]
    equals(op["kind"], "link.add", "link.add")
    equals(op["target"], {"type": "uri", "uri": "https://example.org/zz-terms"}, "the address")
    equals(op["page_uid"], doc.uids[0], "on page 1")
    approx(op["rect"]["x_pt"], 100, 0.6, "x")
    approx(op["rect"]["w_pt"], 120, 0.6, "w")
    approx(op["rect"]["h_pt"], 20, 0.6, "h")
    # The new link is framed; Ctrl+Z takes it back (one step).
    page.wait_for_selector(f'[data-testid="link-{op["id"]}"]')
    page.locator("body").click(position={"x": 3, "y": 3})
    page.keyboard.press("Control+z")
    wait_ops(page, doc, lambda list_: len(link_ops_of(list_)) == 0)
    equals(link_ops(doc), [], "undone")
    clean(mock)


def link_ops_of(ops):
    return [op for op in ops if op["kind"].startswith("link.")]


@test("a_link_to_a_page_of_this_document")
def _(page, mock: MockApi) -> None:
    doc = install(mock, pages=3)
    open_doc(page, mock)
    select_tool(page, "links")
    drag_pt(page, 100, 400, 200, 415)
    page.locator('[data-testid="link-dialog"]').wait_for()
    page.locator('[data-testid="link-kind-page"]').check()
    page.locator('[data-testid="link-page"]').select_option("3")
    page.locator('[data-testid="link-save"]').click()
    op = link_ops_of(wait_ops(page, doc, lambda list_: len(link_ops_of(list_)) == 1))[0]
    equals(op["target"], {"type": "page", "page_uid": doc.uids[2]}, "to page 3, by uid")
    clean(mock)


@test("an_existing_link_is_edited_moved_resized_and_deleted_never_followed")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    doc.links[doc.uids[0]] = [SERVER_LINK]
    open_doc(page, mock)
    url_before = page.url
    select_tool(page, "links")
    frame = page.locator('[data-testid="link-lk12"]')
    frame.wait_for()
    equals(frame.get_attribute("data-target"), "https://example.com/zz", "the server's link, framed")
    frame.click()
    page.locator('[data-testid="link-dialog"]').wait_for()
    equals(page.url, url_before, "a click edits, it never navigates")
    equals(page.locator('[data-testid="link-url"]').input_value(), "https://example.com/zz", "its address")
    page.locator('[data-testid="link-url"]').fill("mailto:zz@example.com")
    page.locator('[data-testid="link-save"]').click()
    op = link_ops_of(wait_ops(page, doc, lambda list_: len(link_ops_of(list_)) == 1))[0]
    equals((op["kind"], op["link_id"]), ("link.update", "lk12"), "link.update of lk12")
    equals(op["target"], {"type": "uri", "uri": "mailto:zz@example.com"}, "mail address")
    # Drag it 30 pt down: the SAME update op, new rect.
    drag_pt(page, 120, 310, 120, 340)
    ops = wait_ops(page, doc, lambda list_: link_ops_of(list_) and link_ops_of(list_)[0]["rect"]["y_pt"] > 320)
    moved = link_ops_of(ops)
    equals(len(moved), 1, "still one op for lk12")
    approx(moved[0]["rect"]["y_pt"], 330, 1.5, "moved down")
    # Resize by its corner.
    grip = page.locator('[data-testid="link-grip-lk12"]').bounding_box()
    page.mouse.move(grip["x"] + 4, grip["y"] + 4)
    page.mouse.down()
    page.mouse.move(grip["x"] + 40, grip["y"] + 20, steps=6)
    page.mouse.up()
    ops = wait_ops(page, doc, lambda list_: link_ops_of(list_) and link_ops_of(list_)[0]["rect"]["w_pt"] > 110)
    truthy(link_ops_of(ops)[0]["rect"]["w_pt"] > 110, "wider")
    shot(page, "links_edit")
    # Delete: link.remove, the update dropped.
    page.locator('[data-testid="link-lk12"]').click()
    page.locator('[data-testid="link-delete"]').click()
    ops = wait_ops(page, doc, lambda list_: any(op["kind"] == "link.remove" for op in list_))
    equals([op["kind"] for op in link_ops_of(ops)], ["link.remove"], "only the removal is left")
    page.wait_for_function("() => !document.querySelector('[data-testid=\"link-lk12\"]')", timeout=5000)
    equals(workspace_attr(page, "data-session"), "edit", "in the edit session")
    clean(mock)


@test("link_on_the_text_toolbar_links_the_edited_block")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    page.locator('[data-testid="editor-edit-pdf"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]')
    select_tool(page, "edit-text")
    block = page.locator('[data-testid="text-block"]').first
    block.wait_for()
    block.click()
    page.locator('[data-testid="text-toolbar"]').wait_for()
    page.locator('[data-testid="text-link"]').click()
    page.locator('[data-testid="link-dialog"]').wait_for()
    page.locator('[data-testid="link-url"]').fill("https://example.net/zz")
    page.locator('[data-testid="link-save"]').click()
    op = link_ops_of(wait_ops(page, doc, lambda list_: len(link_ops_of(list_)) == 1))[0]
    equals(op["kind"], "link.add", "a new link")
    approx(op["rect"]["x_pt"], 72, 1, "over the block (x)")
    truthy(op["rect"]["w_pt"] > 50, f"over the block (w {op['rect']['w_pt']})")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
