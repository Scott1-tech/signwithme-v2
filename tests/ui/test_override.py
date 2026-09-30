"""
Baseline: the reviewer override — when it is offered, what it demands, what it
sends, and how it reads afterwards.

15 checks. Policy under test (unchanged since 2026-09-17): empty required
fields and cross-field inconsistencies can be waived with a written reason of
at least 10 characters; a critical FMCSA violation never can.

Run alone:      python tests/ui/test_override.py
Run one check:  python tests/ui/test_override.py --only confirm
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (  # noqa: E402
    MockApi,
    Registry,
    apply_dates_to_all,
    cli_main,
    confirms,
    contains,
    ensure_target_is_up,
    equals,
    missing,
    open_review,
    open_sign,
    set_confirm_result,
    truthy,
)

test = Registry("override")

NEW_ISSUES_MESSAGE = (
    "New issues appeared after approval — the contract must be reviewed again."
)
OVERRIDE_NOTES_MIN = 10


def inconsistency(kind, issue, pages) -> dict:
    return {
        "type": kind,
        "issue": issue,
        "values": {},
        "pages": list(pages),
        "field_id": None,
        "bbox": None,
        "severity": "critical",
    }


def missing_field(field_id="p3_vehicle_assignment", label="Vehicle Assignment", page=3) -> dict:
    return {
        "field_id": field_id,
        "label": label,
        "page": page,
        "type": "text",
        "bbox": None,
        "severity": "critical",
    }


def fmcsa(rule="cdl_number_required", issue="CDL number is missing for the driver", page=1) -> dict:
    return {
        "rule": rule,
        "issue": issue,
        "page": page,
        "field_ids": ["p1_cdl"],
        "severity": "critical",
    }


CDL = inconsistency("cdl_mismatch", "CDL numbers do not match across pages", (1, 14))
EXPIRY = inconsistency(
    "exp_date_conflict", "CDL expiration dates conflict across the document", (1, 2)
)


def pending_with(mock: MockApi, *, missing_fields=(), inconsistencies=(), violations=()) -> None:
    mock.review_status = "pending"
    mock.status = "analyzed"
    mock.can_sign = False
    mock.missing_required = list(missing_fields)
    mock.inconsistencies = list(inconsistencies)
    mock.fmcsa_violations = list(violations)


def open_decision(page, mock: MockApi):
    open_review(page, mock)
    page.wait_for_selector(".review__decision", timeout=20000)
    return page.locator(".review__decision")


def notes_box(page):
    return page.locator(".review__decision textarea")


def override_button(page):
    return page.get_by_role("button", name="Approve anyway (override)")


# --------------------------------------------------------------------------
# 1-2  When the override box appears and what it lists
# --------------------------------------------------------------------------

@test("override_box_offered_for_inconsistencies_alone")
def _(page, mock: MockApi):
    """No empty fields at all: the box still appears, with only the mismatch list."""
    pending_with(mock, inconsistencies=(CDL, EXPIRY))
    panel = open_decision(page, mock)
    equals(page.locator('[data-testid="override-eligible"]').count(), 1, "override box shown")
    equals(page.locator('[data-testid="override-missing"]').count(), 0, "no empty-field list")
    equals(page.locator('[data-testid="override-inconsistencies"]').count(), 1, "mismatch list")
    contains(panel.inner_text(), "Approval is blocked by 2 inconsistencies.", "headline")
    listed = page.locator('[data-testid="override-inconsistencies"] li')
    equals(listed.count(), 2, "one line per inconsistency")
    truthy(override_button(page).count() == 1, "the override button replaces the plain approve")


@test("override_box_lists_empty_fields_and_inconsistencies_together")
def _(page, mock: MockApi):
    pending_with(mock, missing_fields=(missing_field(),), inconsistencies=(CDL, EXPIRY))
    panel = open_decision(page, mock)
    contains(panel.inner_text(), "Approval is blocked by 1 required field and 2 inconsistencies.",
             "headline counts both buckets")
    empty = page.locator('[data-testid="override-missing"]')
    equals(empty.count(), 1, "empty-field list present")
    contains(empty.inner_text(), "Vehicle Assignment", "field label")
    contains(empty.inner_text(), "Page 3", "field page")
    contains(page.locator('[data-testid="override-inconsistencies"]').inner_text(),
             "CDL numbers do not match across pages (pages 1, 14)", "inconsistency with pages")


# --------------------------------------------------------------------------
# 3-6  The written reason
# --------------------------------------------------------------------------

@test("override_button_stays_disabled_at_nine_characters")
def _(page, mock: MockApi):
    pending_with(mock, inconsistencies=(CDL, EXPIRY))
    open_decision(page, mock)
    notes_box(page).fill("123456789")
    page.wait_for_timeout(150)
    equals(override_button(page).is_disabled(), True, f"9 < {OVERRIDE_NOTES_MIN}")


@test("override_button_enables_at_ten_characters")
def _(page, mock: MockApi):
    pending_with(mock, inconsistencies=(CDL, EXPIRY))
    open_decision(page, mock)
    notes_box(page).fill("1234567890")
    page.wait_for_timeout(150)
    equals(override_button(page).is_disabled(), False, f"{OVERRIDE_NOTES_MIN} is enough")
    # whitespace does not count: the app trims before measuring
    notes_box(page).fill("   123456   ")
    page.wait_for_timeout(150)
    equals(override_button(page).is_disabled(), True, "trimmed length is what counts")


@test("notes_hint_counts_characters_below_the_minimum")
def _(page, mock: MockApi):
    pending_with(mock, inconsistencies=(CDL, EXPIRY))
    open_decision(page, mock)
    notes_box(page).fill("123456789")
    page.wait_for_timeout(150)
    hint = page.locator('[data-testid="notes-hint"]')
    equals(hint.count(), 1, "hint shown while the override is on offer")
    contains(hint.inner_text(), f"9/{OVERRIDE_NOTES_MIN} characters", "counter")
    contains(hint.inner_text(), f"at least {OVERRIDE_NOTES_MIN} required to approve with override",
             "requirement")


@test("notes_hint_confirms_the_reason_is_recorded")
def _(page, mock: MockApi):
    pending_with(mock, inconsistencies=(CDL, EXPIRY))
    open_decision(page, mock)
    notes_box(page).fill("CDL verified against the licence copy")
    page.wait_for_timeout(150)
    hint = page.locator('[data-testid="notes-hint"]').inner_text()
    contains(hint, f"/{OVERRIDE_NOTES_MIN} characters", "counter still shown")
    contains(hint, "reason recorded in the audit trail", "confirmation")
    contains(page.locator('[data-testid="notes-example"]').inner_text(),
             "Say why it is safe", "worked example")


# --------------------------------------------------------------------------
# 7-10  The confirm and the request
# --------------------------------------------------------------------------

@test("confirm_text_names_both_counts")
def _(page, mock: MockApi):
    pending_with(mock, missing_fields=(missing_field(),), inconsistencies=(CDL, EXPIRY))
    open_decision(page, mock)
    notes_box(page).fill("CDL verified against the licence copy")
    set_confirm_result(page, False)
    override_button(page).click()
    page.wait_for_timeout(400)
    said = confirms(page)
    equals(len(said), 1, "exactly one confirm")
    contains(said[0],
             "You are approving a contract with 1 empty required field and "
             "2 cross-field inconsistencies.", "counts")
    contains(said[0], "This is recorded in the audit trail and the final PDF. Continue?",
             "consequence")


@test("confirm_text_leaves_out_the_zero_part")
def _(page, mock: MockApi):
    pending_with(mock, inconsistencies=(CDL, EXPIRY))
    open_decision(page, mock)
    notes_box(page).fill("Typo on page 14, source document checked")
    set_confirm_result(page, False)
    override_button(page).click()
    page.wait_for_timeout(400)
    said = confirms(page)
    equals(len(said), 1, "exactly one confirm")
    contains(said[0], "with 2 cross-field inconsistencies.", "only the non-zero part")
    missing(said[0], "empty required field", "confirm text")


@test("override_request_body_carries_override_missing")
def _(page, mock: MockApi):
    pending_with(mock, inconsistencies=(CDL, EXPIRY))
    open_decision(page, mock)
    notes_box(page).fill("  Typo on page 14, source document checked  ")
    override_button(page).click()
    page.wait_for_url(f"**/contracts/{mock.contract_id}/sign", timeout=20000)
    body = mock.bodies.get("review")
    truthy(body, "POST /api/review/<id> was made")
    equals(body["decision"], "approved", "decision")
    equals(body["override_missing"], True, "override flag")
    equals(body["notes"], "Typo on page 14, source document checked", "notes are trimmed")
    equals(sorted(body.keys()), ["decision", "notes", "override_missing"], "no extra fields")


@test("cancelling_the_confirm_sends_nothing")
def _(page, mock: MockApi):
    pending_with(mock, inconsistencies=(CDL, EXPIRY))
    open_decision(page, mock)
    notes_box(page).fill("Typo on page 14, source document checked")
    set_confirm_result(page, False)
    override_button(page).click()
    page.wait_for_timeout(700)
    equals(mock.bodies.get("review"), None, "no review request left the browser")
    equals(len(confirms(page)), 1, "the user was asked once")
    contains(page.url, "/review", "still on the review page")


# --------------------------------------------------------------------------
# 11-12  FMCSA is beyond an override
# --------------------------------------------------------------------------

@test("an_fmcsa_violation_removes_the_override_button")
def _(page, mock: MockApi):
    pending_with(mock, inconsistencies=(CDL,), violations=(fmcsa(),))
    open_decision(page, mock)
    equals(page.locator('[data-testid="override-eligible"]').count(), 0, "no override box")
    equals(override_button(page).count(), 0, "no override button")
    plain = page.get_by_role("button", name="Approve for signing")
    equals(plain.count(), 1, "the ordinary approve button is shown instead")
    equals(plain.is_disabled(), True, "and it is disabled while can_sign is false")
    equals(page.locator('[data-testid="notes-hint"]').count(), 0,
           "no notes counter when no override is on offer")


@test("the_fmcsa_blocker_line_names_the_violation")
def _(page, mock: MockApi):
    """One line per distinct violation: identical ones are de-duplicated."""
    pending_with(mock, violations=(fmcsa(), fmcsa(), fmcsa(rule="hours_of_service",
                                                          issue="Driver hours exceed the limit",
                                                          page=7)))
    open_decision(page, mock)
    blockers = page.locator('[data-testid="override-blocker"]')
    equals(blockers.count(), 2, "three violations, two distinct lines")
    text = page.locator('[data-testid="blocked-hint"]').inner_text()
    contains(text, "Override is not available: FMCSA violation must be corrected first: "
                   "CDL number is missing for the driver (page 1)", "first blocker")
    contains(text, "Override is not available: FMCSA violation must be corrected first: "
                   "Driver hours exceed the limit (page 7)", "second blocker")


# --------------------------------------------------------------------------
# 13-15  After the override
# --------------------------------------------------------------------------

@test("override_badge_reads_inconsistency_ids_in_words")
def _(page, mock: MockApi):
    """`inconsistency:<type>` is never shown raw: it becomes the comparison's
    own `issue` text, or a humanised type when the result no longer has it."""
    mock.can_sign = False
    mock.inconsistencies = [CDL]
    mock.review_override = True
    mock.review_override_reason = "Typo on page 14, source document checked"
    mock.review_override_fields = ["inconsistency:cdl_mismatch", "inconsistency:exp_date_conflict"]
    open_sign(page, mock, expect_images=2)
    badge = page.locator('[data-testid="override-badge"]')
    equals(badge.count(), 1, "badge shown")
    items = page.locator('[data-testid="override-items"]').inner_text()
    contains(items, "Inconsistency: CDL numbers do not match across pages (pages 1, 14)",
             "resolved from the comparison")
    contains(items, "Inconsistency: Exp date conflict", "humanised fallback when not in the result")
    missing(items, "inconsistency:cdl_mismatch", "the raw key must not be printed")
    equals(page.locator('[data-override-id="inconsistency:cdl_mismatch"]').count(), 1,
           "the raw key stays available as a data attribute")


@test("override_badge_counts_what_was_skipped")
def _(page, mock: MockApi):
    mock.can_sign = False
    mock.missing_required = [missing_field()]
    mock.inconsistencies = [CDL, EXPIRY]
    mock.review_override = True
    mock.review_override_reason = "Checked against the driver's licence copy"
    mock.review_override_fields = [
        "p3_vehicle_assignment",
        "inconsistency:cdl_mismatch",
        "inconsistency:exp_date_conflict",
    ]
    open_sign(page, mock, expect_images=2)
    badge = page.locator('[data-testid="override-badge"]')
    text = badge.inner_text()
    contains(text, "Approved with override", "badge label")
    contains(text, "1 required field and 2 inconsistencies skipped", "counts")
    contains(text, "Checked against the driver's licence copy", "reason")
    contains(text, "Empty: Vehicle Assignment", "the empty field is named")
    contains(badge.get_attribute("title") or "", "Skipped:", "tooltip repeats the list")


@test("sign_page_reports_new_issues_after_approval")
def _(page, mock: MockApi):
    """A 409 from the signing gate becomes one plain sentence, not a raw error."""
    mock.can_sign = False  # waived by the override, so the page is not blocked
    mock.inconsistencies = [CDL]
    mock.review_override = True
    mock.review_override_reason = "Typo on page 14, source document checked"
    mock.review_override_fields = ["inconsistency:cdl_mismatch"]
    mock.finalize_response = {
        "status": 409,
        "body": {
            "error": "conflict",
            "message": "New critical issues appeared after the reviewer override; "
                       "the contract must be reviewed again.",
            "details": {
                "reason": "new_issues_after_approval",
                "new_issues": ["p1_full_name"],
            },
        },
    }
    open_sign(page, mock, expect_images=2)
    equals(page.get_by_text("Signing is blocked while critical issues remain").count(), 0,
           "an active override opens the page despite can_sign=false")
    apply_dates_to_all(page)
    page.get_by_role("button", name="Generate final contract").click()
    alert = page.locator(".review__document .alert--error").first
    alert.wait_for(state="visible", timeout=20000)
    contains(alert.inner_text(), NEW_ISSUES_MESSAGE, "the 409 is translated")
    contains(page.url, "/sign", "the page did not navigate on")


if __name__ == "__main__":
    ensure_target_is_up()
    raise SystemExit(cli_main(test))
