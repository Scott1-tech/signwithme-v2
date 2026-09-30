"""
F1b: the annotations move to the server (EDITOR-V2-PLAN §3 «Markup», §3.4).

  * wire = the finalize body's entry with `page_uid` instead of `page`
    (`lib/markupWire.ts`): no `page` key ever reaches `PUT …/markup`;
  * first contact, server empty + browser has annotations → one PUT, the
    browser's key renamed to `swm.annotations.<cid>.migrated` (kept);
  * server has markup, browser empty → the server's are drawn;
  * both, different → ONE dialog (Import / Keep the server's), never a
    silent loss; either way the browser's list is kept under `.migrated`;
  * a server without the editor (404) → the old localStorage path, untouched;
  * later changes: PUT (debounced) with the revision; inside an edit session
    the markup travels with the draft (no markup PUT: 409 `draft_active`).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _docmock import Line, enable_document, wait_until  # noqa: E402
from _harness import (  # noqa: E402
    BASE,
    MockApi,
    Registry,
    arm_tool,
    cli_main,
    contains,
    equals,
    goto_blank,
    slot,
    truthy,
)

test = Registry("markup migration")

KEY = "swm.annotations.ZZ-UI-1"
MIGRATED = KEY + ".migrated"


def install(mock: MockApi, document: bool = True):
    mock.set_slots([slot("sig_1", "signature", 72, 640, 180, 34, rule_y=679.5)], signed=[])
    if not document:
        return None
    return enable_document(mock, [[Line("Carrier: ABC Transportation LLC", 72, 150)]])


def seed_items(texts: list[str]) -> str:
    return json.dumps([
        {"id": f"s{i}", "kind": "text", "page": 1, "x_pt": 100, "y_pt": 400 + i * 20, "text": text,
         "font": "Helvetica", "size_pt": 11}
        for i, text in enumerate(texts)
    ])


def open_doc(page, mock: MockApi, local: str | None = None) -> None:
    goto_blank(page, mock)
    if local is not None:
        page.evaluate("([k, v]) => localStorage.setItem(k, v)", [KEY, local])
    page.goto(f"{BASE}/contracts/{mock.contract_id}/edit")
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_timeout(300)


def storage(page, key: str):
    raw = page.evaluate("(k) => localStorage.getItem(k)", key)
    return json.loads(raw) if raw else None


def rows(page) -> list[str]:
    return page.locator('[data-testid^="object-ann-"]').all_inner_texts()


def clean(mock: MockApi) -> None:
    equals(mock.page_errors, [], "no uncaught error")
    equals(mock.unmocked, [], "no unmocked API call")


def place_text(page, words: str, y: float = 480, text_box: bool = True) -> None:
    """Add text. F2: with the document editor «Add text» is the v7 text box
    (`tool-textbox`, Enter saves); without it, the W2 run (`tool-text`)."""
    arm_tool(page, "textbox" if text_box else "text")
    box = page.locator('.pdf-page[data-page-number="1"]').bounding_box()
    scale = box["width"] / 612
    page.mouse.click(box["x"] + 120 * scale, box["y"] + y * scale)
    page.wait_for_selector('[data-testid="textbox-editor"]' if text_box else '[data-testid="ann-input"]', timeout=5000)
    page.keyboard.type(words)
    page.keyboard.press("Enter")


def text_of(element: dict) -> str:
    """The words of a markup element: a run's `text`, a text box's runs (F2)."""
    if "runs" in element:
        return "".join(run["text"] for run in element["runs"])
    return element.get("text", "")


# --------------------------------------------------------------------------

@test("browser_annotations_are_put_once_and_the_key_is_renamed_not_deleted")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    seed = seed_items(["alpha", "beta"])
    open_doc(page, mock, local=seed)
    page.wait_for_selector('[data-testid="workspace"][data-markup-sync="synced"]', timeout=10000)
    equals(len(doc.markup_puts), 1, "one PUT")
    put = doc.markup_puts[0]
    equals(put["revision"], 0, "on revision 0")
    equals(len(put["elements"]), 2, "both annotations")
    for element in put["elements"]:
        truthy("page" not in element, f"no page key ({element})")
        equals(element["page_uid"], doc.uids[0], "the page's uid")
    equals(doc.markup_revision, 1, "the server's revision moved")
    equals(storage(page, KEY), None, "the browser key is gone …")
    equals(storage(page, MIGRATED), json.loads(seed), "… renamed to .migrated, untouched")
    equals(len(rows(page)), 2, "still drawn")
    contains(page.locator('[data-testid="editor-hint"]').inner_text(), "saved on the server", "the hint says so")
    clean(mock)


@test("the_servers_markup_is_drawn_when_the_browser_has_none")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    doc.markup = [{"id": "m1", "kind": "text", "page_uid": doc.uids[0], "x_pt": 100, "y_pt": 420,
                   "text": "from the server", "font": "Helvetica", "size_pt": 11}]
    doc.markup_revision = 5
    open_doc(page, mock)
    page.wait_for_selector('[data-testid="workspace"][data-markup-sync="synced"]', timeout=10000)
    page.wait_for_function("() => document.querySelectorAll('[data-testid^=\"object-ann-\"]').length === 1",
                           timeout=8000)
    contains(rows(page)[0], "from the server", "the server's annotation")
    equals(doc.markup_puts, [], "nothing written back")
    equals(page.locator('[data-testid="markup-migration-dialog"]').count(), 0, "nothing to ask")
    clean(mock)


@test("different_lists_ask_once_and_import_replaces_the_servers")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    doc.markup = [{"id": "m1", "kind": "text", "page_uid": doc.uids[0], "x_pt": 100, "y_pt": 420,
                   "text": "server one", "font": "Helvetica", "size_pt": 11}]
    doc.markup_revision = 2
    seed = seed_items(["local one", "local two"])
    open_doc(page, mock, local=seed)
    dialog = page.wait_for_selector('[data-testid="markup-migration-dialog"]', timeout=10000)
    contains(dialog.inner_text(), "2 annotations", "the browser's count")
    contains(dialog.inner_text(), "the 1 saved on the server", "the server's count")
    equals(doc.markup_puts, [], "nothing written before the answer")
    page.locator('[data-testid="markup-import"]').click()
    truthy(wait_until(page, lambda: len(doc.markup_puts) == 1, 5000), "one PUT")
    equals(doc.markup_puts[0]["revision"], 2, "on the server's revision")
    equals(sorted(e["text"] for e in doc.markup), ["local one", "local two"], "the browser's list won")
    page.wait_for_function("(k) => localStorage.getItem(k) !== null", arg=MIGRATED, timeout=5000)
    equals(storage(page, MIGRATED), json.loads(seed), "kept under .migrated")
    page.wait_for_function("() => document.querySelectorAll('[data-testid^=\"object-ann-\"]').length === 2",
                           timeout=5000)
    clean(mock)


@test("keep_the_servers_sets_the_browsers_list_aside_without_deleting_it")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    doc.markup = [{"id": "m1", "kind": "text", "page_uid": doc.uids[0], "x_pt": 100, "y_pt": 420,
                   "text": "server one", "font": "Helvetica", "size_pt": 11}]
    doc.markup_revision = 2
    seed = seed_items(["local only"])
    open_doc(page, mock, local=seed)
    page.wait_for_selector('[data-testid="markup-migration-dialog"]', timeout=10000)
    page.locator('[data-testid="markup-keep-server"]').click()
    page.wait_for_timeout(1200)
    equals(doc.markup_puts, [], "the server is not written")
    equals([e["text"] for e in doc.markup], ["server one"], "the server's list stays")
    equals(storage(page, MIGRATED), json.loads(seed), "the browser's list is set aside, not lost")
    equals(storage(page, KEY), None, "and no longer the live copy")
    contains(rows(page)[0], "server one", "the server's is drawn")
    clean(mock)


@test("without_the_editor_the_browser_keeps_its_annotations_as_before")
def _(page, mock: MockApi) -> None:
    install(mock, document=False)
    seed = seed_items(["legacy"])
    open_doc(page, mock, local=seed)
    page.wait_for_selector('[data-testid="workspace"][data-markup-sync="local"]', timeout=10000)
    equals(storage(page, KEY), json.loads(seed), "the key stays where it was")
    equals(storage(page, MIGRATED), None, "nothing migrated")
    place_text(page, "another", text_box=False)
    page.wait_for_function(
        "(k) => (JSON.parse(localStorage.getItem(k) || '[]')).length === 2", arg=KEY, timeout=5000)
    contains(page.locator('[data-testid="editor-hint"]').inner_text(), "saved in this browser only", "the old hint")
    clean(mock)


@test("a_change_is_put_with_the_revision_and_in_a_session_it_rides_the_draft")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    page.wait_for_selector('[data-testid="workspace"][data-markup-sync="synced"]', timeout=10000)
    place_text(page, "first note")
    truthy(wait_until(page, lambda: len(doc.markup_puts) == 1, 5000), "PUT after the debounce")
    equals(doc.markup_puts[0]["revision"], 0, "revision 0")
    equals(text_of(doc.markup_puts[0]["elements"][0]), "first note", "the text")
    truthy("page" not in doc.markup_puts[0]["elements"][0], "page_uid only")
    equals(storage(page, KEY), None, "the browser key is not written any more")
    # In a session the server refuses a markup PUT: it goes with the draft.
    page.locator('[data-testid="editor-edit-pdf"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=8000)
    place_text(page, "second note", y=520)
    truthy(wait_until(page, lambda: any(body.get("markup") for body in doc.draft_puts), 6000),
           f"the draft PUT carries the markup ({doc.draft_puts})")
    markup = next(body["markup"] for body in doc.draft_puts if body.get("markup"))
    equals(sorted(text_of(e) for e in markup), ["first note", "second note"], "the whole layer")
    equals(len(doc.markup_puts), 1, "no markup PUT during the session")
    page.locator('[data-testid="editor-save"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="view"]', timeout=8000)
    equals(doc.markup_revision, 2, "the commit wrote it (revision 1 → 2)")
    equals(sorted(text_of(e) for e in doc.markup), ["first note", "second note"], "on the server")
    clean(mock)


@test("cancel_puts_the_servers_markup_back")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    page.wait_for_selector('[data-testid="workspace"][data-markup-sync="synced"]', timeout=10000)
    page.locator('[data-testid="editor-edit-pdf"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=8000)
    place_text(page, "thrown away")
    truthy(wait_until(page, lambda: any(body.get("markup") for body in doc.draft_puts), 6000), "in the draft")
    page.locator('[data-testid="editor-cancel"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="view"]', timeout=8000)
    page.wait_for_function("() => document.querySelectorAll('[data-testid^=\"object-ann-\"]').length === 0",
                           timeout=5000)
    equals(doc.markup, [], "the server never had it")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
