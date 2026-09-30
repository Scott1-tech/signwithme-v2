"""
F3-UI: «Share for others to fill» (EDITOR-V2-PLAN §8, §11 Q4 -- LAN only, fill only).

  * the top bar's «Share for others to fill» is there only for a user who may
    edit the document and only when the version has a field to share;
  * the dialog says plainly who can use the link and creates NOTHING until
    the user ticks «I understand» and presses «Create link»; the URL is shown
    once (Copy link) and never again; the list shows state and Revoke;
  * the public page `/s#t=…` takes the token out of the address bar before
    any request and sends it only as `X-Share-Token` (no Authorization, not in
    a URL); inputs sit on the fields' rects; Submit → Thank you; the server's
    per-field errors are shown by field; Cyrillic is refused before sending;
    every refusal of the link is the same neutral message;
  * Submissions (right panel): masked values marked, Accept → the values in a
    draft of THIS tab's session → Save (the signatures dialog) → a new version;
    another draft → `draft_locked` said in words; Reject needs a reason.

Server: `_sharemock.ShareMock` over `_docmock.DocMock`.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _docmock import seeded_draft, wait_until  # noqa: E402
from _f2 import clean, install, open_doc, shot  # noqa: E402
from _harness import BASE, MockApi, Registry, cli_main, contains, equals, missing, truthy  # noqa: E402
from _sharemock import enable_share, field  # noqa: E402

test = Registry("share (F3)")

WARNING = "Anyone on the office network with this link can fill these fields. The link works only on the LAN."


def open_dialog(page) -> None:
    page.locator('[data-testid="share-open"]').click()
    page.locator('[data-testid="share-dialog"]').wait_for()


def open_shared(page, token: str) -> None:
    page.goto(f"{BASE}/s#t={token}")
    page.wait_for_selector('[data-testid="shared-form"]:not([data-state="loading"])', timeout=20000)


@test("the_share_button_is_there_only_with_a_field_to_share_and_the_right_to_edit")
def _(page, mock: MockApi) -> None:
    # No form fields (the default server): no button, and no share call is "unmocked".
    install(mock)
    open_doc(page, mock)
    equals(page.locator('[data-testid="share-open"]').count(), 0, "no fields -> no button")
    equals(page.locator('[data-testid="section-submissions"]').count(), 0, "nothing shared -> no section")
    truthy(any("/document/versions/1/fields" in call for call in mock.calls), "the fields were asked for")
    clean(mock)

    # Fields but the user may not edit the document: nothing drawn, nothing asked.
    mock2 = MockApi()
    context = page.context
    other = context.new_page()
    mock2.install(other)
    install(mock2, can_edit=False, reason="not_permitted")
    enable_share(mock2)
    open_doc(other, mock2)
    equals(other.locator('[data-testid="share-open"]').count(), 0, "can_edit false -> no button")
    # `/fields` may still be read: F2b's form layer loads fields for filling
    # (`can_fill`) even when the user may not edit. The share feature itself
    # must stay silent: no share-link or submissions call.
    truthy(not any("share-links" in call or "/submissions" in call for call in mock2.calls), "no share request")
    other.close()

    # Fields and the right: the button, with a name a screen reader says.
    mock3 = MockApi()
    third = context.new_page()
    mock3.install(third)
    install(mock3)
    enable_share(mock3)
    open_doc(third, mock3)
    button = third.locator('[data-testid="share-open"]')
    button.wait_for()
    equals(button.get_attribute("aria-label"), "Share for others to fill", "aria-label")
    contains(button.get_attribute("title") or "", "LAN only", "tooltip")
    truthy(button.is_enabled(), "a real, enabled button")
    third.close()


@test("the_dialog_warns_and_creates_nothing_until_the_user_confirms")
def _(page, mock: MockApi) -> None:
    install(mock)
    share = enable_share(mock)
    open_doc(page, mock)
    open_dialog(page)
    contains(page.locator('[data-testid="share-warning"]').inner_text(), WARNING, "the plain warning")
    # Only the shareable names are offered; the others are named with the reason.
    offered = page.locator('[data-testid^="share-field-"]')
    equals(offered.count(), 5, "carrier_name, agree, state, pay (one group), driver_ssn")
    blocked = page.locator('[data-testid="share-blocked-fields"]').inner_text()
    contains(blocked, "sig_box", "a signature box is not shareable")
    contains(blocked, "office_code (read-only)", "a read-only field says why")
    create = page.locator('[data-testid="share-create"]')
    truthy(create.is_disabled(), "Create is off before «I understand»")
    contains(page.locator('[data-testid="share-create-reason"]').inner_text(), "Confirm", "and says why")
    equals(page.locator('[data-testid="share-expiry"]').input_value(), "72", "default 72 hours")
    equals(page.locator('[data-testid="share-max"]').input_value(), "1", "default one submission")
    page.locator('[data-testid="share-field-driver_ssn"]').uncheck()
    page.locator('[data-testid="share-label"]').fill("Driver - ZZ Smith")
    page.wait_for_timeout(300)
    equals(share.create_bodies, [], "nothing sent before the explicit confirm")
    page.locator('[data-testid="share-ack"]').check()
    truthy(create.is_enabled(), "Create is on after «I understand»")
    shot(page, "share_dialog")
    create.click()
    page.locator('[data-testid="share-created"]').wait_for()
    equals(share.create_bodies, [{
        "allowed_fields": ["carrier_name", "agree", "state", "pay"],
        "recipient_label": "Driver - ZZ Smith",
        "expires_in_hours": 72,
        "max_submissions": 1,
    }], "one create, the chosen fields")
    clean(mock)


@test("the_url_is_shown_once_with_copy_and_never_again")
def _(page, mock: MockApi) -> None:
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
    install(mock)
    share = enable_share(mock)
    open_doc(page, mock)
    open_dialog(page)
    page.locator('[data-testid="share-ack"]').check()
    page.locator('[data-testid="share-create"]').click()
    url_box = page.locator('[data-testid="share-url"]')
    url_box.wait_for()
    url = url_box.input_value()
    truthy(url.startswith("http://10.31.0.58:5173/s#t="), f"the server's LAN URL ({url})")
    token = url.split("#t=", 1)[1]
    contains(page.locator('[data-testid="share-once-note"]').inner_text(), "cannot be shown again", "said once")
    page.locator('[data-testid="share-copy"]').click()
    page.wait_for_selector('[data-testid="share-copy-status"][data-state="copied"]', timeout=5000)
    equals(page.evaluate("() => navigator.clipboard.readText()"), url, "copied to the clipboard")
    # The new link is in the list at once.
    page.wait_for_selector(f'[data-testid="share-link-{share.links[0]["id"]}"][data-status="active"]', timeout=5000)
    page.locator('[data-testid="share-done"]').click()
    missing(page.content(), token, "gone from the page after Done")
    page.locator('[data-testid="share-close"]').click()
    open_dialog(page)
    missing(page.content(), token, "never shown again")
    equals(page.locator('[data-testid="share-url"]').count(), 0, "no URL box")
    stored = page.evaluate(
        "() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])")
    missing(stored, token, "never stored in the browser")
    clean(mock)


@test("an_expiry_over_14_days_is_refused_up_front_and_server_refusals_name_the_field")
def _(page, mock: MockApi) -> None:
    install(mock)
    share = enable_share(mock)
    open_doc(page, mock)
    open_dialog(page)
    page.locator('[data-testid="share-ack"]').check()
    page.locator('[data-testid="share-expiry"]').fill("15")
    page.locator('[data-testid="share-expiry-unit"]').select_option("days")
    create = page.locator('[data-testid="share-create"]')
    truthy(create.is_disabled(), "15 days: off")
    contains(page.locator('[data-testid="share-create-reason"]').inner_text(), "at most 14 days", "why")
    page.locator('[data-testid="share-expiry"]').fill("14")
    truthy(create.is_enabled(), "14 days: on")
    # The version changed under the dialog: the server refuses one name by position.
    share.force["POST /share-links"] = [(422, {"error": "fields_not_shareable", "message": "x",
                                               "details": {"fields": [{"index": 1, "reason": "field_readonly"}]}})]
    create.click()
    error = page.locator('[data-testid="share-error"]')
    error.wait_for()
    contains(error.inner_text(), "“agree” — read-only", "the refused field by name")
    equals(share.create_bodies[-1]["expires_in_hours"], 336, "14 days = 336 hours")
    equals(page.locator('[data-testid="share-url"]').count(), 0, "no link shown")
    clean(mock)


@test("revoke_asks_first_then_the_link_shows_revoked")
def _(page, mock: MockApi) -> None:
    install(mock)
    share = enable_share(mock)
    link, _token = share.add_link(label="ZZ Revoke me")
    open_doc(page, mock)
    equals(page.locator('[data-testid="share-active-count"]').inner_text(), "1", "one active link on the button")
    open_dialog(page)
    row = page.locator(f'[data-testid="share-link-{link["id"]}"]')
    equals(row.get_attribute("data-status"), "active", "active")
    contains(row.inner_text(), "0/1", "submissions 0 of 1")
    revoke = page.locator(f'[data-testid="share-revoke-{link["id"]}"]')
    equals(revoke.get_attribute("aria-label"), "Revoke the link for ZZ Revoke me", "aria-label")
    page.evaluate("() => { window.__swmConfirmResult = false; }")
    revoke.click()
    page.wait_for_timeout(300)
    equals(share.revokes, [], "a cancelled confirm revokes nothing")
    page.evaluate("() => { window.__swmConfirmResult = true; }")
    revoke.click()
    page.wait_for_selector(f'[data-testid="share-link-{link["id"]}"][data-status="revoked"]', timeout=5000)
    equals(share.revokes, [link["id"]], "one DELETE")
    contains(page.evaluate("() => window.__swmConfirms.join('|')"), "stops working at once", "the confirm text")
    equals(page.locator(f'[data-testid="share-revoke-{link["id"]}"]').count(), 0, "no Revoke on a revoked link")
    clean(mock)


@test("the_shared_page_clears_the_token_from_the_address_bar_and_sends_it_only_as_a_header")
def _(page, mock: MockApi) -> None:
    install(mock)
    share = enable_share(mock)
    _link, token = share.add_link()
    open_shared(page, token)
    equals(page.locator('[data-testid="shared-form"]').get_attribute("data-state"), "ready", "ready")
    href = page.evaluate("() => location.href")
    equals(href, f"{BASE}/s", "no fragment left in the address bar")
    missing(page.evaluate("() => JSON.stringify(history.state)"), token, "not in the history state")
    truthy(len(share.public) >= 2, "session + file asked")
    for call in share.public:
        equals(call["token"], token, f"{call['path']}: X-Share-Token")
        equals(call["auth"], None, f"{call['path']}: no Authorization header")
        missing(call["url"], token, f"{call['path']}: not in the URL")
    stored = page.evaluate("() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])")
    missing(stored, token, "never stored")
    # No app navigation on this page, and the inputs sit on the fields' rects.
    equals(page.locator("nav, .app-header, [data-testid='workspace']").count(), 0, "no app chrome")
    page_box = page.locator('[data-testid="shared-page-1"]').bounding_box()
    scale = page_box["width"] / 612
    box = page.locator('[data-testid="shared-field-carrier_name"]').bounding_box()
    truthy(abs(box["x"] - page_box["x"] - 72 * scale) < 1.5, f"x on the rect ({box['x'] - page_box['x']:.1f})")
    truthy(abs(box["y"] - page_box["y"] - 300 * scale) < 1.5, f"y on the rect ({box['y'] - page_box['y']:.1f})")
    truthy(abs(box["width"] - 200 * scale) < 1.5, "width on the rect")
    equals(page.locator('[data-testid="shared-field-sig_box"]').count(), 0, "a non-shared field has no input")
    equals(page.locator('[data-testid="shared-field-driver_ssn"]').count(), 1, "an allowed field has one")
    equals(page.locator('[data-testid^="shared-field-pay-"]').count(), 2, "one radio per button")
    equals(page.locator(".sf-cover").count(), 6, "an opaque cover under each of the 6 widgets")
    equals(page.locator('[data-testid="shared-field-carrier_name"]').get_attribute("aria-label"),
           "Carrier name (required)", "the tooltip as the label")
    shot(page, "shared_form")
    equals(mock.page_errors, [], "no uncaught error")


@test("the_shared_form_submits_its_values_and_says_thank_you")
def _(page, mock: MockApi) -> None:
    install(mock)
    share = enable_share(mock)
    _link, token = share.add_link(max_submissions=2)
    open_shared(page, token)
    page.locator('[data-testid="shared-field-carrier_name"]').fill("ZZ Freight LLC")
    page.locator('[data-testid="shared-field-agree"]').check()
    page.locator('[data-testid="shared-field-state"]').select_option("CA")
    page.locator('[data-testid="shared-field-pay-check"]').check()
    page.locator('[data-testid="shared-field-driver_ssn"]').fill("123-45-6789")
    page.locator('[data-testid="shared-name"]').fill("ZZ Filler")
    page.locator('[data-testid="shared-email"]').fill("zz@example.test")
    page.locator('[data-testid="shared-submit"]').click()
    page.wait_for_selector('[data-testid="shared-form"][data-state="thanks"]', timeout=10000)
    equals(share.submit_bodies, [{
        "values": {"carrier_name": "ZZ Freight LLC", "agree": True, "state": "CA", "pay": "check",
                   "driver_ssn": "123-45-6789"},
        "submitter_name": "ZZ Filler",
        "submitter_email": "zz@example.test",
    }], "one POST, every allowed name once")
    contains(page.locator('[data-testid="shared-thanks"]').inner_text(), "Thank you", "thanks")
    contains(page.locator('[data-testid="shared-left"]').inner_text(), "1 more time", "one use left")
    equals(len(share.submissions), 1, "stored")
    equals(mock.page_errors, [], "no uncaught error")


@test("field_errors_are_shown_by_field_and_cyrillic_is_refused_before_sending")
def _(page, mock: MockApi) -> None:
    install(mock)
    share = enable_share(mock)
    _link, token = share.add_link()
    open_shared(page, token)
    # Required, empty: said before anything is sent.
    page.locator('[data-testid="shared-submit"]').click()
    page.locator('[data-testid="shared-field-error-carrier_name"]').wait_for()
    contains(page.locator('[data-testid="shared-field-error-carrier_name"]').inner_text(), "required", "required")
    equals(page.locator('[data-testid="shared-field-carrier_name"]').get_attribute("aria-invalid"), "true",
           "the input is marked")
    # Cyrillic: named, nothing sent.
    page.locator('[data-testid="shared-field-carrier_name"]').fill("Жук Freight")
    page.locator('[data-testid="shared-submit"]').click()
    page.wait_for_timeout(200)
    contains(page.locator('[data-testid="shared-field-error-carrier_name"]').inner_text(), "“Ж” cannot be printed",
             "the character by name")
    equals(share.submit_bodies, [], "nothing sent so far")
    # The server's own per-field answer.
    page.locator('[data-testid="shared-field-carrier_name"]').fill("ZZ Freight")
    share.force["POST /api/share/submissions"] = [(422, {
        "error": "invalid_submission", "message": "x",
        "details": {"fields": [{"name": "state", "reason": "invalid_field_value"},
                               {"name": "driver_ssn", "reason": "undrawable_character"}], "not_allowed": 0}})]
    page.locator('[data-testid="shared-submit"]').click()
    page.locator('[data-testid="shared-field-error-state"]').wait_for()
    contains(page.locator('[data-testid="shared-field-error-state"]').inner_text(), "not accepted", "state")
    contains(page.locator('[data-testid="shared-field-error-driver_ssn"]').inner_text(), "Latin letters", "ssn")
    equals(page.locator('[data-testid="shared-form"]').get_attribute("data-state"), "ready", "still the form")
    # empty_submission
    share.force["POST /api/share/submissions"] = [(422, {"error": "empty_submission", "message": "x", "details": {}})]
    page.locator('[data-testid="shared-submit"]').click()
    page.wait_for_function(
        "() => (document.querySelector('[data-testid=\"shared-errors\"]')?.innerText || '').includes('Nothing was filled')",
        timeout=5000)
    equals(len(share.submit_bodies), 2, "two POSTs, both refused")
    equals(mock.page_errors, [], "no uncaught error")


@test("an_invalid_expired_or_revoked_link_shows_one_neutral_message")
def _(page, mock: MockApi) -> None:
    install(mock)
    share = enable_share(mock)
    _revoked, revoked_token = share.add_link(status="revoked")
    _expired, expired_token = share.add_link(status="expired")
    texts = []
    for token in ("A" * 43, revoked_token, expired_token):
        page.goto("about:blank")
        open_shared(page, token)
        equals(page.locator('[data-testid="shared-form"]').get_attribute("data-state"), "invalid", "invalid")
        texts.append(page.locator('[data-testid="shared-invalid"]').inner_text())
        equals(page.evaluate("() => location.hash"), "", "the fragment is cleared even then")
    equals(len(set(texts)), 1, f"the same words for every refusal ({texts})")
    equals(texts[0], "This link is invalid or has expired.", "neutral")
    # A second link pasted into the same tab (only the fragment changes) is read too.
    _good, good_token = share.add_link()
    page.goto("about:blank")
    open_shared(page, good_token)
    equals(page.locator('[data-testid="shared-form"]').get_attribute("data-state"), "ready", "a good link")
    page.evaluate("(t) => { location.hash = '#t=' + t; }", revoked_token)
    page.wait_for_selector('[data-testid="shared-form"][data-state="invalid"]', timeout=10000)
    equals(page.evaluate("() => location.hash"), "", "cleared after a same-tab change too")
    equals(share.public[-1]["token"], revoked_token, "the new token was the one asked about")
    # No token / a malformed one: the same message, and no request at all.
    before = len(share.public)
    for tail in ("", "#t=short", "#x=1"):
        page.goto(f"{BASE}/s{tail}")
        page.wait_for_selector('[data-testid="shared-form"][data-state="invalid"]', timeout=10000)
    equals(len(share.public), before, "no request without a well-formed token")
    # Too many failures from this address: a different, still neutral message.
    share.locked = True
    page.goto("about:blank")
    open_shared(page, "B" * 43)
    equals(page.locator('[data-testid="shared-form"]').get_attribute("data-state"), "throttled", "429")
    contains(page.locator('[data-testid="shared-throttled"]').inner_text(), "Too many attempts", "said")
    shot(page, "shared_invalid")
    equals(mock.page_errors, [], "no uncaught error")


@test("submissions_show_masked_values_and_accept_opens_a_draft_that_save_turns_into_a_version")
def _(page, mock: MockApi) -> None:
    doc = install(mock, signed=["sig_1"])
    share = enable_share(mock)
    link, _token = share.add_link()
    row = share.add_submission(link, {"carrier_name": "ZZ Freight LLC", "agree": True, "state": "TX",
                                      "pay": "ach", "driver_ssn": "XXX-XX-6789"})
    open_doc(page, mock)
    section = page.locator('[data-testid="section-submissions"]')
    section.wait_for()
    equals(page.locator('[data-testid="section-submissions-badge"]').inner_text(), "1 to review", "badge")
    item = page.locator(f'[data-testid="submission-{row["id"]}"]')
    contains(item.inner_text(), "ZZ Freight LLC", "a value")
    contains(item.inner_text(), "Yes (ticked)", "a checkbox in words")
    ssn = item.locator('[data-field="driver_ssn"]')
    contains(ssn.inner_text(), "XXX-XX-6789", "the server's mask")
    equals(ssn.locator('[data-testid="submission-masked"]').count(), 1, "marked as masked")
    equals(item.locator('[data-field="carrier_name"] [data-testid="submission-masked"]').count(), 0,
           "a plain field is not marked")
    accept = page.locator(f'[data-testid="submission-accept-{row["id"]}"]')
    truthy("Accept the submission" in (accept.get_attribute("aria-label") or ""), "aria-label")
    shot(page, "submissions")
    accept.click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=10000)
    session_id = page.evaluate(f"() => sessionStorage.getItem('swm.editSession.{mock.contract_id}')")
    equals(share.accept_bodies, [{"client_session_id": session_id}], "accept with THIS tab's session id")
    truthy(doc.draft_posts and doc.draft_posts[-1].get("client_session_id") == session_id, "the draft opened")
    equals([op["kind"] for op in doc.draft["ops"]], ["field.value"] * 5, "five field.value ops in the draft")
    page.locator('[data-testid="submissions-accepted"]').wait_for()
    page.wait_for_selector(f'[data-testid="submission-{row["id"]}"][data-status="accepted"]', timeout=5000)
    # Save: the contract is signed, so the usual dialog first.
    page.locator('[data-testid="editor-save"]').click()
    page.locator('[data-testid="signatures-dialog"]').wait_for()
    page.locator('[data-testid="signatures-dialog-confirm"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="view"]', timeout=10000)
    equals(doc.current, 2, "a new version")
    truthy(doc.commit_bodies[-1].get("discard_signatures") is True, "after the confirm")
    page.locator('[data-testid="session-committed"]').wait_for()
    clean(mock)


@test("accept_with_another_draft_open_says_draft_locked_in_words")
def _(page, mock: MockApi) -> None:
    doc = install(mock)
    share = enable_share(mock)
    link, _token = share.add_link()
    row = share.add_submission(link, {"carrier_name": "ZZ"})
    seeded_draft(doc, mine=False)
    open_doc(page, mock)
    page.locator(f'[data-testid="submission-accept-{row["id"]}"]').click()
    error = page.locator('[data-testid="submission-error"]')
    error.wait_for()
    equals(error.get_attribute("data-code"), "draft_locked", "the code")
    contains(error.inner_text(), "Other Editor is editing this document", "who")
    contains(error.inner_text(), "save or cancel", "what to do")
    equals(page.locator('[data-testid="workspace"]').get_attribute("data-session"), "view", "no session opened")
    equals(row["status"], "received", "still to review")
    equals(doc.draft["owner_name"], "Other Editor", "their draft untouched")
    # My own open edits: a different sentence.
    doc.draft = None
    open_doc(page, mock)
    page.locator('[data-testid="editor-edit-pdf"]').click()
    page.wait_for_selector('[data-testid="workspace"][data-session="edit"]', timeout=10000)
    page.locator(f'[data-testid="submission-accept-{row["id"]}"]').click()
    page.wait_for_function(
        "() => (document.querySelector('[data-testid=\"submission-error\"]')?.innerText || '').includes('You have edits open')",
        timeout=5000)
    clean(mock)


@test("reject_needs_a_reason_and_marks_the_submission_rejected")
def _(page, mock: MockApi) -> None:
    install(mock)
    share = enable_share(mock)
    link, _token = share.add_link()
    row = share.add_submission(link, {"carrier_name": "ZZ"}, name=None, email=None)
    open_doc(page, mock)
    item = page.locator(f'[data-testid="submission-{row["id"]}"]')
    contains(item.inner_text(), "No name given", "anonymous")
    page.locator(f'[data-testid="submission-reject-{row["id"]}"]').click()
    confirm = page.locator('[data-testid="submission-reject-confirm"]')
    truthy(confirm.is_disabled(), "no reason -> off")
    equals(confirm.get_attribute("title"), "Write a reason first", "and says why")
    page.locator('[data-testid="submission-reject-reason-input"]').fill("  Wrong carrier  ")
    confirm.click()
    page.wait_for_selector(f'[data-testid="submission-{row["id"]}"][data-status="rejected"]', timeout=5000)
    equals(share.reject_bodies, [{"reason": "Wrong carrier"}], "the trimmed reason")
    contains(page.locator('[data-testid="submission-reject-reason"]').inner_text(), "Wrong carrier", "shown")
    equals(page.locator(f'[data-testid="submission-accept-{row["id"]}"]').count(), 0, "no more Accept")
    equals(len(share.accept_bodies), 0, "nothing accepted")
    clean(mock)


if __name__ == "__main__":
    raise SystemExit(cli_main(test))
