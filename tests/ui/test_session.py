"""
F1b: the edit session -- Edit PDF / Save changes / Cancel editing (EDITOR-V2-PLAN §7 «Sessiya»).

  * the top bar's session group exists only where the server has the
    document editor, and follows `capabilities` (hidden for a user who may
    not edit, off with the reason for a finalised / busy contract);
  * Edit PDF starts ONE draft (`POST …/draft`, this tab's session id);
    signing and «Generate final contract» are off while it is open, and say
    why;
  * autosave: debounced (1.5 s), one PUT in flight, revision in the body;
    offline → retried; 409 → the conflict dialog (Reload / Download my
    changes); every change is also kept in `localStorage["swm.draft.<cid>"]`;
  * Save = one commit with a `commit_request_id`; 409 `signatures_present`
    → the dialog «N signature(s) will be removed and the contract must be
    signed again» (user decision Q3) → the same request with
    `discard_signatures`; after a content commit «Review reopened»; the new
    version is loaded in place;
  * Cancel = `DELETE …/draft?revision=N`, the page is the document again;
  * a draft left behind (this user) or a local copy newer than the server:
    the recovery banner (Resume / Restore from this browser / Discard).

The server is `_docmock.DocMock`; nothing here reaches the live backend.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _docmock import Line, enable_document, seeded_draft, wait_until  # noqa: E402
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

test = Registry("edit session")

CARRIER = "Carrier: ABC Transportation LLC"
REF = "REF-2026-0042"


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
    return enable_document(mock, [[Line(CARRIER, 72, 150), Line(REF, 72, 230, "Courier", 10)]], **options)


def clean(mock: MockApi) -> None:
    equals(mock.page_errors, [], "no uncaught error")
    equals(mock.unmocked, [], "no unmocked API call")


def open_doc(page, mock: MockApi, seed: dict[str, str] | None = None, panel: str = "edit") -> None:
    if seed:
        goto_blank(page, mock)
        for key, value in seed.items():
            page.evaluate("([k, v]) => localStorage.setItem(k, v)", [key, value])
    page.goto(f"{BASE}/contracts/{mock.contract_id}/{panel}")
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-rendered]', timeout=30000)
    page.wait_for_selector('[data-testid="workspace"][data-editor="ready"]', timeout=15000)
    page.wait_for_timeout(250)


def start(page) -> None:
    page.locator('[data-testid="editor-edit-pdf"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=10000)
    status(page, "saved")


def status(page, value: str, timeout: int = 10000) -> None:
    page.wait_for_selector(f'[data-testid="editor-save-status"][data-status="{value}"]', timeout=timeout)


def edit_ref(page, value: str) -> None:
    """Retype the Courier block (one op) and commit it with Enter."""
    arm_tool(page, "edit-text")
    page.wait_for_selector('[data-testid="text-block-layer-1"][data-state="ready"]', timeout=15000)
    box = page.locator(f'.tb-block[data-text="{REF}"]').bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_selector('[data-testid="text-block-editor"]', timeout=10000)
    page.keyboard.press("Control+A")
    page.keyboard.type(value)
    page.keyboard.press("Enter")


def real_puts(doc) -> list:
    return [body for body in doc.draft_puts if "forced" not in body and "aborted" not in body]


# --------------------------------------------------------------------------

@test("the_session_group_follows_the_editor_and_the_capabilities")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    button = page.locator('[data-testid="editor-edit-pdf"]')
    equals(button.count(), 1, "Edit PDF in the top bar")
    truthy(button.is_enabled(), "enabled for an admin")
    equals(page.locator('[data-testid="editor-save"]').count(), 0, "no Save before the session")
    # A finalised contract: off, and why.
    doc.capabilities = {"can_edit": False, "can_edit_markup": False, "reason": "contract_completed"}
    page.reload()
    page.wait_for_selector('[data-testid="workspace"][data-editor="ready"]', timeout=15000)
    truthy(page.locator('[data-testid="editor-edit-pdf"]').is_disabled(), "off on a finalised contract")
    contains(page.locator('[data-testid="editor-edit-pdf-reason"]').inner_text(), "finalised", "with the reason")
    # A contractor who may not edit the content (Q2): no button at all.
    doc.capabilities = {"can_edit": False, "can_edit_markup": True, "reason": "not_permitted"}
    page.reload()
    page.wait_for_selector('[data-testid="workspace"][data-editor="ready"]', timeout=15000)
    page.wait_for_timeout(300)
    equals(page.locator('[data-testid="editor-edit-pdf"]').count(), 0, "not offered to a contractor")
    equals(page.locator('[data-testid="editor-session"]').count(), 0, "no session group at all")
    clean(mock)


@test("edit_pdf_starts_one_draft_and_signing_waits_with_the_reason")
def _(page, mock: MockApi) -> None:
    doc = install(mock, signed=["sig_1"])
    open_doc(page, mock, panel="sign")
    start(page)
    equals(len(doc.draft_posts), 1, "one POST …/draft")
    body = doc.draft_posts[0]
    equals(body["base_version_no"], 1, "on the current version")
    truthy(re.fullmatch(r"[A-Za-z0-9_-]{8,64}", body["client_session_id"] or ""), f"a session id ({body})")
    truthy("take_over" not in body, "no take-over on a fresh start")
    equals(page.locator('[data-testid="editor-save-status"]').inner_text(), "All changes saved", "status")
    equals(page.locator('[data-testid="editor-cancel"]').count(), 1, "Cancel editing")
    finalize = page.locator('[data-testid="editor-finalize"]')
    truthy(finalize.is_disabled(), "Generate final contract is off")
    equals(finalize.get_attribute("title"), "Save or cancel your edits first", "and says why")
    sig = page.locator('[data-testid="tool-signature"]')
    equals(sig.get_attribute("data-disabled-reason"), "Save or cancel your edits first", "Sign ▾ Signature off")
    equals(page.locator('[data-testid="tool-initials"]').get_attribute("data-disabled-reason"),
           "Save or cancel your edits first", "Sign ▾ Initials off")
    equals(page.locator('[data-testid="workspace"]').get_attribute("data-tool"), "select",
           "the armed Signature tool went back to Select")
    # The same tab starting again reuses the same session id.
    session_id = body["client_session_id"]
    equals(page.evaluate(f"() => sessionStorage.getItem('swm.editSession.{mock.contract_id}')"), session_id,
           "kept per tab")
    clean(mock)


@test("autosave_sends_one_put_per_burst_with_the_revision_and_keeps_a_local_copy")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    start(page)
    edit_ref(page, "REF-A")
    truthy(wait_until(page, lambda: len(real_puts(doc)) == 1, 6000), "the block commit is saved at once")
    first = real_puts(doc)[0]
    equals(first["revision"], 0, "revision of the fresh draft")
    equals(len(first["ops"]), 1, "one op")
    equals(first["markup"], None, "no markup in the draft (unchanged)")
    status(page, "saved")
    local = json.loads(page.evaluate(f"() => localStorage.getItem('swm.draft.{mock.contract_id}')"))
    equals((local["acked"], local["revision"], len(local["ops"])), (True, 1, 1), "the local copy, acknowledged")
    # A burst of undo / redo / undo within the debounce: ONE more PUT, with the next revision.
    page.keyboard.press("Control+z")
    page.keyboard.press("Control+y")
    page.keyboard.press("Control+z")
    page.wait_for_timeout(600)
    equals(len(real_puts(doc)), 1, "nothing sent inside the 1.5 s debounce")
    truthy(wait_until(page, lambda: len(real_puts(doc)) == 2, 4000), "then one PUT")
    page.wait_for_timeout(1800)
    equals(len(real_puts(doc)), 2, "only one for the burst")
    second = real_puts(doc)[1]
    equals((second["revision"], second["ops"]), (1, []), "the answered revision, the final list")
    clean(mock)


@test("offline_is_retried_and_a_revision_conflict_opens_the_dialog")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    start(page)
    doc.abort["PUT /draft"] = 1
    edit_ref(page, "REF-B")
    status(page, "offline", timeout=6000)
    truthy(wait_until(page, lambda: len(real_puts(doc)) == 1, 6000), "retried after the back-off")
    status(page, "saved")
    # Someone else moved the draft on.
    doc.force["PUT /draft"] = [(409, {"error": "revision_conflict", "message": "Changed",
                                      "details": {"current_revision": 9}})]
    edit_ref(page, "REF-C")
    dialog = page.wait_for_selector('[data-testid="conflict-dialog"]', timeout=8000)
    contains(dialog.inner_text(), "changed somewhere else", "says what happened")
    equals(page.locator('[data-testid="conflict-download"]').count(), 1, "Download my changes (JSON)")
    with page.expect_download() as info:
        page.locator('[data-testid="conflict-download"]').click()
    data = json.loads(open(info.value.path(), encoding="utf-8").read())
    equals(len(data["ops"]), 1, "the download holds the edits")
    page.locator('[data-testid="conflict-reload"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="view"]', timeout=8000)
    local = page.evaluate(f"() => localStorage.getItem('swm.draft.{mock.contract_id}')")
    truthy(local and "REF-C" in local, "this browser keeps its copy after Reload")
    clean(mock)


@test("save_commits_once_asks_before_removing_signatures_and_reopens_review")
def _(page, mock: MockApi) -> None:
    doc = install(mock, signed=["sig_1"])
    open_doc(page, mock)
    start(page)
    edit_ref(page, "REF-2027-0001")
    truthy(wait_until(page, lambda: len(real_puts(doc)) == 1, 6000), "saved")
    status(page, "saved")
    page.locator('[data-testid="editor-save"]').click()
    dialog = page.wait_for_selector('[data-testid="signatures-dialog"]', timeout=8000)
    equals(page.locator('[data-testid="signatures-dialog-text"]').inner_text(),
           "1 signature will be removed and the contract must be signed again.", "the exact warning (Q3)")
    first = doc.commit_bodies[0]
    truthy(re.fullmatch(r"[A-Za-z0-9_-]{8,64}", first["commit_request_id"]), "a commit request id")
    truthy("discard_signatures" not in first, "not discarded without asking")
    page.locator('[data-testid="signatures-dialog-confirm"]').click()
    page.wait_for_selector('[data-testid="session-committed"]', timeout=10000)
    equals(len(doc.commit_bodies), 2, "one more commit")
    second = doc.commit_bodies[1]
    equals(second["discard_signatures"], True, "with discard_signatures")
    equals(second["commit_request_id"], first["commit_request_id"], "the SAME request id")
    banner = page.locator('[data-testid="session-committed"]').inner_text()
    contains(banner, "Saved as version 2", "the new version")
    contains(banner, "Review reopened", "review reopened (content commit)")
    contains(banner, "1 signature was removed", "and the signatures")
    equals(page.locator('[data-testid="workspace"]').get_attribute("data-session"), "view", "back to view")
    truthy(any(call.endswith("/document/versions/2/file") for call in mock.calls), "version 2 is loaded")
    equals(page.evaluate(f"() => localStorage.getItem('swm.draft.{mock.contract_id}')"), None,
           "the local copy is gone")
    truthy(dialog is not None, "dialog was shown")
    equals(page.locator('[data-testid="editor-finalize"]').get_attribute("title") != "Save or cancel your edits first",
           True, "finalize no longer blocked by the session")
    clean(mock)


@test("cancel_deletes_the_draft_and_the_page_is_the_document_again")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    open_doc(page, mock)
    start(page)
    edit_ref(page, "REF-X")
    truthy(wait_until(page, lambda: len(real_puts(doc)) == 1, 6000), "saved")
    page.wait_for_selector('.pdf-page[data-page-number="1"][data-override]', timeout=8000)
    page.locator('[data-testid="editor-cancel"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="view"]', timeout=8000)
    confirms = page.evaluate("() => window.__swmConfirms")
    contains(confirms[-1], "Discard 1 unsaved edit", "asked first")
    equals(doc.draft_deletes, ["revision=1"], "DELETE …/draft?revision=1")
    equals(doc.draft, None, "the draft is gone")
    page.wait_for_function(
        "() => !document.querySelector('.pdf-page[data-page-number=\"1\"]').hasAttribute('data-override')",
        timeout=8000,
    )
    equals(len(doc.commit_bodies), 0, "nothing committed")
    clean(mock)


@test("a_draft_left_behind_offers_resume_and_discard")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    op = {"id": "op_left", "kind": "text.delete", "page_uid": doc.uids[0],
          "target": {"block_id": "b1", "line_ids": ["b1l0"], "expect_text": REF,
                     "bbox": {"x_pt": 72, "y_pt": 220.95, "w_pt": 78, "h_pt": 11.17}}}
    seeded_draft(doc, mine=True, lease_active=False, ops=[op], revision=4)
    open_doc(page, mock)
    banner = page.wait_for_selector('[data-testid="session-recovery"]', timeout=8000)
    contains(banner.inner_text(), "Unsaved changes from", "the recovery banner")
    page.locator('[data-testid="session-resume"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=8000)
    equals(doc.draft_posts[-1].get("take_over"), True, "resumed in this tab (take over my own draft)")
    arm_tool(page, "edit-text")
    page.wait_for_selector('.tb-block[data-edited="true"]', timeout=8000)
    equals(page.locator('.tb-block[data-edited="true"]').get_attribute("data-text"), REF, "the left edit is back")
    clean(mock)


@test("a_local_copy_without_a_server_draft_can_be_restored")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    op = {"id": "op_local", "kind": "text.delete", "page_uid": doc.uids[0],
          "target": {"block_id": "b1", "line_ids": ["b1l0"], "expect_text": REF,
                     "bbox": {"x_pt": 72, "y_pt": 220.95, "w_pt": 78, "h_pt": 11.17}}}
    local = {"v": 1, "base_version_no": 1, "revision": 2, "ops": [op], "markup": None,
             "changed_at": 1790000000000, "acked": False}
    open_doc(page, mock, seed={f"swm.draft.{mock.contract_id}": json.dumps(local)})
    banner = page.wait_for_selector('[data-testid="session-recovery"][data-source="local"]', timeout=8000)
    contains(banner.inner_text(), "kept in this browser", "says where")
    page.locator('[data-testid="session-restore-local"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=8000)
    truthy(wait_until(page, lambda: doc.draft is not None and len(doc.draft["ops"]) == 1, 6000),
           "the local edits are sent to the new draft")
    equals(doc.draft["ops"][0]["id"], "op_local", "as they were")
    clean(mock)


@test("someone_elses_draft_is_a_lock_with_take_over")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    seeded_draft(doc, mine=False, lease_active=True)
    open_doc(page, mock)
    lock = page.wait_for_selector('[data-testid="session-lock"]', timeout=8000)
    contains(lock.inner_text(), "Other Editor is editing", "who")
    truthy(page.locator('[data-testid="editor-edit-pdf"]').is_disabled(), "Edit PDF is off")
    page.locator('[data-testid="session-take-over"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=8000)
    equals(doc.draft_posts[-1].get("take_over"), True, "take_over: true, after a confirm")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
