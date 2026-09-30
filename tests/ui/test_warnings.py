"""
Baseline: the wrong-file / wrong-template warnings and the real blockers the
Decision panel prints.

14 checks. All three warning shapes are additive on the server, so each one is
exercised with `true`, `false`, `null` and "key absent" where that applies —
an older server must read as "nothing to say", never as an error.

Run alone:      python tests/ui/test_warnings.py
Run one check:  python tests/ui/test_warnings.py --only prefilled
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (  # noqa: E402
    OMIT,
    MockApi,
    Registry,
    approx,
    cli_main,
    contains,
    ensure_target_is_up,
    equals,
    missing,
    open_review,
    open_sign,
    open_templates,
    truthy,
)

test = Registry("warnings")


def template(name: str, prefilled) -> dict:
    row = {
        "id": "ZZ-UI-T1",
        "name": name,
        "description": None,
        "page_count": 2,
        "status": "ready",
        "error_message": None,
        "created_at": "2026-09-23T08:00:00+00:00",
        "updated_at": "2026-09-23T08:00:00+00:00",
    }
    if prefilled is not OMIT:
        row["prefilled_summary"] = prefilled
    return row


def prefilled(signatures=(42, 42), dates=(50, 51), warning=True) -> dict:
    return {
        "signatures": {"filled": signatures[0], "total": signatures[1]},
        "dates": {"filled": dates[0], "total": dates[1]},
        "warning": warning,
    }


def fmcsa(rule="cdl_number_required", issue="CDL number is missing for the driver", page=1) -> dict:
    return {
        "rule": rule,
        "issue": issue,
        "page": page,
        "field_ids": ["p1_cdl"],
        "severity": "critical",
    }


def inconsistency(kind="cdl_mismatch", issue="CDL numbers do not match across pages",
                  pages=(1, 14)) -> dict:
    return {
        "type": kind,
        "issue": issue,
        "values": {},
        "pages": list(pages),
        "field_id": None,
        "bbox": None,
        "severity": "critical",
    }


def pending_review(mock: MockApi) -> None:
    mock.review_status = "pending"
    mock.status = "analyzed"
    mock.can_sign = False


# --------------------------------------------------------------------------
# 1-4  "Pre-filled template" (GET /api/templates -> prefilled_summary)
# --------------------------------------------------------------------------

@test("prefilled_template_badge_when_warning_is_true")
def _(page, mock: MockApi):
    mock.templates = [template("ZZ Signed-looking template", prefilled(warning=True))]
    open_templates(page, mock)
    marker = page.locator('[data-testid="prefilled-warning"]')
    equals(marker.count(), 1, "one marker")
    text = marker.inner_text()
    contains(text, "Pre-filled template", "badge")
    contains(text, "Already contains 42/42 signatures and 50/51 dates", "counts")
    contains(text, "looks like a signed document, not a blank form", "explanation")


@test("prefilled_template_badge_hidden_when_warning_is_false")
def _(page, mock: MockApi):
    mock.templates = [template("ZZ Clean template", prefilled(signatures=(0, 42),
                                                              dates=(0, 51), warning=False))]
    open_templates(page, mock)
    equals(page.locator('[data-testid="prefilled-warning"]').count(), 0,
           "a summary without warning:true says nothing")


@test("prefilled_template_badge_hidden_when_summary_is_null")
def _(page, mock: MockApi):
    mock.templates = [template("ZZ Null summary", None)]
    open_templates(page, mock)
    equals(page.locator('[data-testid="prefilled-warning"]').count(), 0, "null reads as silence")
    contains(page.locator("table.table").inner_text(), "ZZ Null summary", "the row still renders")


@test("prefilled_template_badge_hidden_when_the_field_is_absent")
def _(page, mock: MockApi):
    """An older server omits `prefilled_summary` entirely."""
    mock.templates = [template("ZZ Old server", OMIT)]
    open_templates(page, mock)
    equals(page.locator('[data-testid="prefilled-warning"]').count(), 0, "absent reads as silence")
    equals(mock.page_errors, [], "no uncaught error")


# --------------------------------------------------------------------------
# 5-9  "contract appears to be the template file itself" (template_match)
# --------------------------------------------------------------------------

@test("template_match_banner_when_identical_is_true")
def _(page, mock: MockApi):
    mock.template_match = {"identical": True, "page_similarity": 1.0, "image_match_ratio": 1.0}
    open_review(page, mock)
    banner = page.locator('[data-testid="template-match-warning"]')
    equals(banner.count(), 1, "one banner")
    text = banner.inner_text()
    contains(text, "This contract appears to be the template file itself", "banner wording")
    contains(text, "page similarity 100%", "similarity")
    contains(text, "Check that the correct file was uploaded", "advice")


@test("template_match_banner_hidden_when_identical_is_false")
def _(page, mock: MockApi):
    mock.template_match = {"identical": False, "page_similarity": 0.99, "image_match_ratio": 0.9}
    open_review(page, mock)
    equals(page.locator('[data-testid="template-match-warning"]').count(), 0,
           "a high similarity alone is not a warning; only identical:true is")


@test("template_match_banner_hidden_when_the_match_is_null")
def _(page, mock: MockApi):
    mock.template_match = None
    open_review(page, mock)
    equals(page.locator('[data-testid="template-match-warning"]').count(), 0, "null reads as silence")
    mock.template_match = OMIT
    open_review(page, mock)
    equals(page.locator('[data-testid="template-match-warning"]').count(), 0,
           "an absent key reads as silence too")
    equals(mock.page_errors, [], "no uncaught error")


@test("template_match_badge_on_the_sign_page")
def _(page, mock: MockApi):
    mock.template_match = {"identical": True, "page_similarity": 1.0, "image_match_ratio": 1.0}
    open_sign(page, mock, expect_images=2)
    badge = page.locator('[data-testid="template-match-badge"]')
    equals(badge.count(), 1, "one badge")
    contains(badge.inner_text(), "Same file as template? (100%)", "badge text")
    contains(badge.get_attribute("title") or "",
             "This contract appears to be the template file itself", "badge tooltip")


@test("template_match_badge_hidden_when_not_identical")
def _(page, mock: MockApi):
    mock.template_match = {"identical": False, "page_similarity": 0.0, "image_match_ratio": 0.57}
    open_sign(page, mock, expect_images=2)
    equals(page.locator('[data-testid="template-match-badge"]').count(), 0, "no badge")


# --------------------------------------------------------------------------
# 10-12  What the review side actually prints as a blocker
# --------------------------------------------------------------------------

@test("fmcsa_row_shows_the_issue_text")
def _(page, mock: MockApi):
    """The sidebar groups it under FMCSA and prints the server's own `issue`."""
    pending_review(mock)
    mock.fmcsa_violations = [fmcsa()]
    open_review(page, mock)
    sidebar = page.locator(".sidebar").inner_text()
    contains(sidebar, "FMCSA", "group title")
    contains(sidebar, "cdl number required", "rule, underscores turned into spaces")
    contains(sidebar, "CDL number is missing for the driver", "the issue text itself")
    contains(sidebar, "Page 1", "page anchor")


@test("decision_panel_lists_the_real_blockers")
def _(page, mock: MockApi):
    """A critical FMCSA violation: no override, and the blocker is named."""
    pending_review(mock)
    mock.fmcsa_violations = [fmcsa()]
    open_review(page, mock)
    equals(page.locator('[data-testid="override-eligible"]').count(), 0,
           "FMCSA is never overridable, so the override box is not offered")
    hint = page.locator('[data-testid="blocked-hint"]')
    equals(hint.count(), 1, "the blocked hint is shown")
    contains(hint.inner_text(), "Approval is blocked while critical issues remain", "headline")
    blockers = page.locator('[data-testid="override-blocker"]')
    equals(blockers.count(), 1, "one blocker line")
    contains(
        blockers.first.inner_text(),
        "Override is not available: FMCSA violation must be corrected first: "
        "CDL number is missing for the driver (page 1)",
        "blocker line",
    )


@test("cross_field_inconsistency_names_its_pages")
def _(page, mock: MockApi):
    """`(pages 1, 14)` style: unique, sorted, "page" singular when there is one."""
    pending_review(mock)
    mock.inconsistencies = [
        inconsistency(pages=(1, 14)),
        inconsistency("exp_date_conflict", "CDL expiration dates conflict across the document",
                      pages=(2,)),
    ]
    open_review(page, mock)
    listed = page.locator('[data-testid="override-inconsistencies"]').inner_text()
    contains(listed, "CDL numbers do not match across pages (pages 1, 14)", "plural suffix")
    contains(listed, "CDL expiration dates conflict across the document (page 2)", "singular suffix")


# --------------------------------------------------------------------------
# 13-14  Upload pad wording and the similarity maths
# --------------------------------------------------------------------------

@test("background_removal_is_a_static_note_not_a_checkbox")
def _(page, mock: MockApi):
    """Today the pad states it; the old "Remove background" checkbox is gone
    (`SignatureUploadPad.tsx`, the `modal__extra` hint)."""
    open_sign(page, mock, expect_images=2)
    page.get_by_role("button", name="Choose signature").click()
    page.get_by_role("button", name="Upload an image instead").click()
    # The preview canvas stays 0 x 0 (hidden) until a file is chosen, so the
    # modal is recognised by its file input instead.
    page.wait_for_selector('.modal input[type="file"]', timeout=15000)
    extra = page.locator(".modal__extra").inner_text()
    contains(extra, "Background is removed automatically.", "static note")
    equals(page.get_by_role("checkbox", name="Remove background").count(), 0,
           "no Remove background checkbox any more")
    modal = page.locator(".modal__panel").inner_text()
    contains(modal, "PNG or JPEG, up to 2 MB", "format hint")
    missing(modal, "Remove background", "modal text")


@test("similarity_percent_is_rounded_and_clamped")
def _(page, mock: MockApi):
    """`similarityPercent` rounds to a whole percent and clamps into 0..100."""
    mock.template_match = {"identical": True, "page_similarity": 0.876, "image_match_ratio": 0.9}
    open_review(page, mock)
    contains(page.locator('[data-testid="template-match-warning"]').inner_text(),
             "page similarity 88%", "0.876 rounds to 88")

    mock.template_match = {"identical": True, "page_similarity": 1.4, "image_match_ratio": 1.0}
    open_review(page, mock)
    contains(page.locator('[data-testid="template-match-warning"]').inner_text(),
             "page similarity 100%", "anything above 1 clamps to 100")

    mock.template_match = {"identical": True, "page_similarity": -0.2, "image_match_ratio": 0.0}
    open_review(page, mock)
    contains(page.locator('[data-testid="template-match-warning"]').inner_text(),
             "page similarity 0%", "anything below 0 clamps to 0")


if __name__ == "__main__":
    ensure_target_is_up()
    raise SystemExit(cli_main(test))
