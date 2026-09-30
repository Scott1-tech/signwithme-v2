"""Reviewer override of critical missing-required fields and critical
inconsistencies, over HTTP.

The engine's `can_sign` stays authoritative for FMCSA violations. A reviewer
may approve past empty required fields and (since 2026-09-17) critical
cross-field inconsistencies, with a reason on record; signing then only goes
through while the current critical issues are a subset of the accepted ones.
These tests cover the review gate, the
signing gate that re-checks the override, the audit trail, the final PDF's
record of the override and a re-parse keeping it.
"""
from __future__ import annotations

import io
from uuid import UUID

import pytest
from pypdf import PdfReader

from tests.conftest import _promote, _register, auth
from tests.test_api_signing import _corrupt_data, _place, signature_data_url  # noqa: F401

REASON = "Driver will present the CDL card in person at orientation"


def _block_on_missing(api, contractor_token, contract_id: str, field: str = "p1_cdl") -> dict:
    """Leave the contract with exactly one critical issue: a missing required field."""
    _corrupt_data(api, contract_id, field)
    rerun = api.post(f"/api/contracts/{contract_id}/compare", headers=auth(contractor_token))
    assert rerun.status_code == 200, rerun.text
    comparison = rerun.json()
    assert comparison["can_sign"] is False
    assert [m["field_id"] for m in comparison["missing_required"]] == [field]
    assert comparison["fmcsa_violations"] == [] and comparison["inconsistencies"] == []
    return comparison


def _patch_comparison(api, contract_id: str, **changes) -> None:
    """Write straight into the stored comparison, as a template with the
    FMCSA/cross-field rule fields would make the engine do."""
    from app.models.contract import Contract

    db = api.db_factory()
    try:
        contract = db.get(Contract, UUID(contract_id))
        result = dict(contract.comparison_result)
        result.update(changes)
        contract.comparison_result = result
        db.commit()
    finally:
        db.close()


def _set_status(api, contract_id: str, status: str) -> None:
    from app.models.contract import Contract

    db = api.db_factory()
    try:
        db.get(Contract, UUID(contract_id)).status = status
        db.commit()
    finally:
        db.close()


CRITICAL_FMCSA = [
    {
        "rule": "fmcsa_dot_eligible",
        "issue": "Contractor is not confirmed DOT eligible.",
        "page": 2,
        "field_ids": ["p2_dot_eligible"],
        "severity": "critical",
    }
]
CRITICAL_INCONSISTENCY = [
    {
        "type": "cdl_mismatch",
        "issue": "CDL numbers do not match across pages",
        "values": {"p1_cdl": "A", "p2_cdl": "B"},
        "pages": [1, 2],
        "severity": "critical",
    }
]
CRITICAL_EXP_CONFLICT = [
    {
        "type": "exp_date_conflict",
        "issue": "CDL expiration dates do not match",
        "values": {"p1_cdl_exp": "01/01/2027", "p2_cdl_exp": "01/01/2028"},
        "pages": [1, 2],
        "severity": "critical",
    }
]


def _block_on_inconsistency(api, contractor_token, contract_id: str) -> None:
    """Leave the contract with exactly one critical issue: a cdl_mismatch."""
    _block_on_missing(api, contractor_token, contract_id)
    _patch_comparison(
        api, contract_id, missing_required=[], inconsistencies=list(CRITICAL_INCONSISTENCY)
    )


def _finalize(api, token, contract_id: str):
    return api.post(
        f"/api/contracts/{contract_id}/finalize",
        headers=auth(token),
        json={"contract_date": "2026-09-10", "apply_paired_dates": True},
    )


def _override(api, token, contract_id: str, notes: str = REASON, **extra):
    return api.post(
        f"/api/review/{contract_id}",
        headers=auth(token),
        json={"decision": "approved", "notes": notes, "override_missing": True, **extra},
    )


def _detail(api, token, contract_id: str) -> dict:
    response = api.get(f"/api/contracts/{contract_id}", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


def _trail(api, token, contract_id: str) -> list[dict]:
    return api.get(f"/api/review/{contract_id}/audit", headers=auth(token)).json()


@pytest.fixture
def reviewer_token(api) -> str:
    _register(api, "reviewer@example.com")
    _promote(api, "reviewer@example.com", "reviewer")
    response = api.post(
        "/api/auth/login", json={"email": "reviewer@example.com", "password": "password123"}
    )
    return response.json()["access_token"]


# -- the review gate ---------------------------------------------------------


def test_a_reviewer_can_override_missing_required_fields_with_a_reason(
    api, uploaded_contract, reviewer_token, contractor_token
):
    _block_on_missing(api, contractor_token, uploaded_contract)

    response = _override(api, reviewer_token, uploaded_contract)
    assert response.status_code == 200, response.text
    assert response.json()["review_status"] == "approved"
    assert response.json()["status"] == "signing"

    detail = _detail(api, reviewer_token, uploaded_contract)
    assert detail["review_override"] is True
    assert detail["review_override_reason"] == REASON
    assert detail["review_override_fields"] == ["p1_cdl"]
    assert detail["review_notes"] == REASON
    # The comparison itself is untouched: the engine still says no.
    assert detail["comparison_result"]["can_sign"] is False

    trail = _trail(api, reviewer_token, uploaded_contract)
    override = [e for e in trail if e["action"] == "review_override"]
    assert len(override) == 1
    assert override[0]["actor_email"] == "reviewer@example.com"
    assert override[0]["details"] == {"field_ids": ["p1_cdl"], "inconsistencies": [], "reason": REASON}
    submitted = [e for e in trail if e["action"] == "review_submitted"]
    assert len(submitted) == 1
    assert submitted[0]["details"]["decision"] == "approved"
    assert submitted[0]["details"]["override"] is True


@pytest.mark.parametrize("notes", [None, "", "   ", "too short", "  nine ch  "])
def test_an_override_without_a_real_reason_is_refused(
    api, uploaded_contract, admin_token, contractor_token, notes
):
    _block_on_missing(api, contractor_token, uploaded_contract)
    response = _override(api, admin_token, uploaded_contract, notes=notes)
    assert response.status_code == 422, response.text
    assert response.json()["message"] == "Override requires a reason (at least 10 characters)"

    detail = _detail(api, admin_token, uploaded_contract)
    assert detail["review_status"] == "pending"
    assert detail["review_override"] is False
    assert "review_override" not in {e["action"] for e in _trail(api, admin_token, uploaded_contract)}


def test_without_the_override_flag_a_blocked_contract_is_still_refused(
    api, uploaded_contract, admin_token, contractor_token
):
    _block_on_missing(api, contractor_token, uploaded_contract)
    response = api.post(
        f"/api/review/{uploaded_contract}",
        headers=auth(admin_token),
        json={"decision": "approved", "notes": REASON},
    )
    assert response.status_code == 409, response.text
    assert "cannot be approved" in response.json()["message"]
    assert response.json()["details"] == {}
    assert _detail(api, admin_token, uploaded_contract)["review_override"] is False


def test_an_override_cannot_cover_an_fmcsa_violation(
    api, uploaded_contract, admin_token, contractor_token
):
    _block_on_missing(api, contractor_token, uploaded_contract)
    _patch_comparison(api, uploaded_contract, fmcsa_violations=CRITICAL_FMCSA)

    response = _override(api, admin_token, uploaded_contract)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"] == "conflict"
    assert body["details"]["reason"] == "override_not_allowed"
    assert body["details"]["blocking_categories"] == ["fmcsa_violations"]

    detail = _detail(api, admin_token, uploaded_contract)
    assert detail["review_status"] == "pending"
    assert detail["review_override"] is False
    assert "review_override" not in {e["action"] for e in _trail(api, admin_token, uploaded_contract)}


def test_a_reviewer_can_override_a_critical_inconsistency_with_a_reason(
    api, uploaded_contract, reviewer_token, contractor_token
):
    """2026-09-17 policy: a CDL printed differently on two pages can be accepted
    by a reviewer who checked the licence itself."""
    _block_on_inconsistency(api, contractor_token, uploaded_contract)

    response = _override(api, reviewer_token, uploaded_contract)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "signing"

    detail = _detail(api, reviewer_token, uploaded_contract)
    assert detail["review_override"] is True
    assert detail["review_override_fields"] == ["inconsistency:cdl_mismatch"]
    assert detail["comparison_result"]["can_sign"] is False

    override = [e for e in _trail(api, reviewer_token, uploaded_contract) if e["action"] == "review_override"]
    assert len(override) == 1
    assert override[0]["details"] == {
        "field_ids": [],
        "inconsistencies": [
            {"type": "cdl_mismatch", "issue": "CDL numbers do not match across pages", "pages": [1, 2]}
        ],
        "reason": REASON,
    }
    # The mismatched values themselves (PII) never reach the audit trail.
    assert "values" not in override[0]["details"]["inconsistencies"][0]


def test_missing_fields_and_inconsistencies_are_overridden_together(
    api, uploaded_contract, admin_token, contractor_token
):
    _block_on_missing(api, contractor_token, uploaded_contract)
    _patch_comparison(
        api, uploaded_contract,
        # A repeated type is recorded once.
        inconsistencies=CRITICAL_INCONSISTENCY + CRITICAL_INCONSISTENCY + CRITICAL_EXP_CONFLICT,
    )
    response = _override(api, admin_token, uploaded_contract)
    assert response.status_code == 200, response.text
    assert _detail(api, admin_token, uploaded_contract)["review_override_fields"] == [
        "p1_cdl", "inconsistency:cdl_mismatch", "inconsistency:exp_date_conflict",
    ]
    override = [e for e in _trail(api, admin_token, uploaded_contract) if e["action"] == "review_override"]
    assert override[0]["details"]["field_ids"] == ["p1_cdl"]
    assert [i["type"] for i in override[0]["details"]["inconsistencies"]] == [
        "cdl_mismatch", "cdl_mismatch", "exp_date_conflict",
    ]


def test_an_fmcsa_violation_still_blocks_an_inconsistency_override(
    api, uploaded_contract, admin_token, contractor_token
):
    """Was `test_both_blocking_categories_are_reported_together`: inconsistencies
    are no longer a blocking category, so only FMCSA is reported."""
    _block_on_inconsistency(api, contractor_token, uploaded_contract)
    _patch_comparison(api, uploaded_contract, fmcsa_violations=CRITICAL_FMCSA)
    response = _override(api, admin_token, uploaded_contract)
    assert response.status_code == 409
    assert response.json()["details"] == {
        "reason": "override_not_allowed",
        "blocking_categories": ["fmcsa_violations"],
    }
    detail = _detail(api, admin_token, uploaded_contract)
    assert detail["review_status"] == "pending"
    assert detail["review_override"] is False


def test_an_inconsistency_override_without_a_real_reason_is_refused(
    api, uploaded_contract, admin_token, contractor_token
):
    _block_on_inconsistency(api, contractor_token, uploaded_contract)
    response = _override(api, admin_token, uploaded_contract, notes="too short")
    assert response.status_code == 422, response.text
    assert _detail(api, admin_token, uploaded_contract)["review_override"] is False


def test_a_contractor_cannot_override_an_inconsistency(api, uploaded_contract, contractor_token):
    _block_on_inconsistency(api, contractor_token, uploaded_contract)
    response = _override(api, contractor_token, uploaded_contract)
    assert response.status_code == 403, response.text
    assert _detail(api, contractor_token, uploaded_contract)["review_override"] is False


def test_an_override_with_nothing_overridable_is_refused(
    api, uploaded_contract, admin_token, contractor_token
):
    """A stored comparison that says can_sign=false with no critical entry at
    all must not yield an empty override that opens the signing gate."""
    _block_on_missing(api, contractor_token, uploaded_contract)
    _patch_comparison(api, uploaded_contract, missing_required=[])
    response = _override(api, admin_token, uploaded_contract)
    assert response.status_code == 409, response.text
    assert response.json()["details"] == {"reason": "override_not_allowed", "blocking_categories": []}
    assert _detail(api, admin_token, uploaded_contract)["review_override"] is False


def test_a_warning_level_inconsistency_does_not_block_the_override(
    api, uploaded_contract, admin_token, contractor_token
):
    """Only *critical* entries block; an email-mismatch warning is not a reason
    to refuse what the engine itself would not have counted."""
    _block_on_missing(api, contractor_token, uploaded_contract)
    _patch_comparison(
        api, uploaded_contract,
        inconsistencies=[{**CRITICAL_INCONSISTENCY[0], "type": "email_mismatch", "severity": "warning"}],
    )
    response = _override(api, admin_token, uploaded_contract)
    assert response.status_code == 200, response.text
    assert _detail(api, admin_token, uploaded_contract)["review_override"] is True


def test_a_contractor_cannot_override(api, uploaded_contract, contractor_token):
    _block_on_missing(api, contractor_token, uploaded_contract)
    response = _override(api, contractor_token, uploaded_contract)
    assert response.status_code == 403, response.text
    assert _detail(api, contractor_token, uploaded_contract)["review_override"] is False


def test_the_flag_is_ignored_when_the_contract_can_already_be_signed(
    api, uploaded_contract, admin_token, contractor_token
):
    comparison = api.get(
        f"/api/contracts/{uploaded_contract}/compare", headers=auth(contractor_token)
    ).json()
    assert comparison["can_sign"] is True

    response = _override(api, admin_token, uploaded_contract)
    assert response.status_code == 200, response.text
    assert response.json()["review_status"] == "approved"

    detail = _detail(api, admin_token, uploaded_contract)
    assert detail["review_override"] is False
    assert detail["review_override_reason"] is None
    assert detail["review_override_fields"] == []
    actions = [e["action"] for e in _trail(api, admin_token, uploaded_contract)]
    assert "review_override" not in actions
    assert actions.count("review_submitted") == 1


def test_a_later_plain_decision_clears_an_earlier_override(
    api, uploaded_contract, admin_token, contractor_token
):
    """A reject after an override must not leave the override flag behind for
    a later approval to lean on."""
    _block_on_missing(api, contractor_token, uploaded_contract)
    assert _override(api, admin_token, uploaded_contract).status_code == 200
    response = api.post(
        f"/api/review/{uploaded_contract}",
        headers=auth(admin_token),
        json={"decision": "rejected", "notes": "changed my mind"},
    )
    assert response.status_code == 200, response.text
    detail = _detail(api, admin_token, uploaded_contract)
    assert detail["review_status"] == "rejected"
    assert detail["review_override"] is False
    assert detail["review_override_fields"] == []


# -- the signing gate --------------------------------------------------------


def test_an_overridden_contract_can_be_signed_and_the_pdf_records_it(
    api, uploaded_contract, admin_token, contractor_token, signature_data_url
):
    _block_on_missing(api, contractor_token, uploaded_contract)
    assert _override(api, admin_token, uploaded_contract).status_code == 200

    placed = _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        field_id="p1_contractor_signature",
    )
    assert placed.status_code == 201, placed.text

    finalized = api.post(
        f"/api/contracts/{uploaded_contract}/finalize",
        headers=auth(contractor_token),
        json={"contract_date": "2026-09-10", "apply_paired_dates": True},
    )
    assert finalized.status_code == 200, finalized.text
    assert len(finalized.json()["file_hash"]) == 64

    download = api.get(f"/api/download/{uploaded_contract}/final", headers=auth(contractor_token))
    assert download.status_code == 200
    metadata = PdfReader(io.BytesIO(download.content)).metadata
    assert metadata.subject.startswith("Reviewer override: " + REASON)
    assert "p1_cdl" in metadata.subject
    assert metadata.get("/Keywords") == "reviewer-override"
    # The note is part of the hashed bytes, so the integrity check covers it.
    verified = api.get(f"/api/download/{uploaded_contract}/verify", headers=auth(contractor_token))
    assert verified.json()["intact"] is True

    actions = {e["action"] for e in _trail(api, admin_token, uploaded_contract)}
    assert {"review_override", "review_submitted", "signature_added", "contract_finalized"} <= actions


def test_a_pdf_signed_without_an_override_carries_no_override_note(
    api, uploaded_contract, admin_token, contractor_token, signature_data_url
):
    response = api.post(
        f"/api/review/{uploaded_contract}",
        headers=auth(admin_token),
        json={"decision": "approved", "notes": "data checks out"},
    )
    assert response.status_code == 200, response.text
    assert _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        field_id="p1_contractor_signature",
    ).status_code == 201
    finalized = api.post(
        f"/api/contracts/{uploaded_contract}/finalize",
        headers=auth(contractor_token),
        json={"contract_date": "2026-09-10"},
    )
    assert finalized.status_code == 200, finalized.text
    download = api.get(f"/api/download/{uploaded_contract}/final", headers=auth(contractor_token))
    # The source PDF's own Subject (reportlab writes 'unspecified') is left
    # alone; only an override replaces it.
    metadata = PdfReader(io.BytesIO(download.content)).metadata
    assert "Reviewer override" not in (metadata.subject or "")
    assert metadata.get("/Keywords") != "reviewer-override"


def test_an_fmcsa_violation_found_after_the_override_blocks_signing_again(
    api, uploaded_contract, admin_token, contractor_token, signature_data_url
):
    """The override is re-checked against the current comparison at every
    signing step; it never covers a violation that surfaced later."""
    _block_on_missing(api, contractor_token, uploaded_contract)
    assert _override(api, admin_token, uploaded_contract).status_code == 200
    _patch_comparison(api, uploaded_contract, fmcsa_violations=CRITICAL_FMCSA)
    # The accepted missing field is still there; only the FMCSA entry is new.
    _assert_signing_blocked(
        api, contractor_token, uploaded_contract, signature_data_url, ["fmcsa:fmcsa_dot_eligible"]
    )


def test_a_critical_inconsistency_found_after_the_override_blocks_signing_again(
    api, uploaded_contract, admin_token, contractor_token, signature_data_url
):
    """A missing-field override does not stretch to an inconsistency that
    appeared later: it was not among the accepted issues."""
    _block_on_missing(api, contractor_token, uploaded_contract)
    assert _override(api, admin_token, uploaded_contract).status_code == 200
    _patch_comparison(api, uploaded_contract, inconsistencies=CRITICAL_INCONSISTENCY)
    _assert_signing_blocked(
        api, contractor_token, uploaded_contract, signature_data_url, ["inconsistency:cdl_mismatch"]
    )


def test_an_inconsistency_override_can_be_signed_and_the_pdf_records_it(
    api, uploaded_contract, admin_token, contractor_token, signature_data_url
):
    _block_on_inconsistency(api, contractor_token, uploaded_contract)
    assert _override(api, admin_token, uploaded_contract).status_code == 200

    placed = _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        field_id="p1_contractor_signature",
    )
    assert placed.status_code == 201, placed.text
    finalized = _finalize(api, contractor_token, uploaded_contract)
    assert finalized.status_code == 200, finalized.text

    download = api.get(f"/api/download/{uploaded_contract}/final", headers=auth(contractor_token))
    metadata = PdfReader(io.BytesIO(download.content)).metadata
    assert metadata.subject == (
        f"Reviewer override: {REASON} - inconsistencies: "
        "CDL numbers do not match across pages (pages 1, 2)"
    )
    assert metadata.get("/Keywords") == "reviewer-override"


def test_both_override_sections_appear_in_the_pdf_subject(
    api, uploaded_contract, admin_token, contractor_token, signature_data_url
):
    _block_on_missing(api, contractor_token, uploaded_contract)
    _patch_comparison(api, uploaded_contract, inconsistencies=CRITICAL_INCONSISTENCY)
    assert _override(api, admin_token, uploaded_contract).status_code == 200
    assert _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        field_id="p1_contractor_signature",
    ).status_code == 201
    assert _finalize(api, contractor_token, uploaded_contract).status_code == 200
    download = api.get(f"/api/download/{uploaded_contract}/final", headers=auth(contractor_token))
    subject = PdfReader(io.BytesIO(download.content)).metadata.subject
    assert subject.startswith(f"Reviewer override: {REASON} - fields: ")
    assert "(p1_cdl); inconsistencies: CDL numbers do not match across pages (pages 1, 2)" in subject


NEW_ISSUES_MESSAGE = (
    "New critical issues appeared after the reviewer override; the contract must be reviewed again."
)


def _assert_signing_blocked(api, token, contract_id: str, image: str, new_issues: list[str]) -> None:
    """place, bulk and finalize all answer with the same new-issues 409."""
    expected = {
        "error": "conflict",
        "message": NEW_ISSUES_MESSAGE,
        "details": {"reason": "new_issues_after_approval", "new_issues": new_issues},
    }
    placed = _place(api, token, contract_id, image, field_id="p1_contractor_signature")
    bulk = api.post(
        f"/api/contracts/{contract_id}/signatures/bulk",
        headers=auth(token),
        json={"image_data": image, "field_ids": ["p1_contractor_signature"]},
    )
    finalized = _finalize(api, token, contract_id)
    for response in (placed, bulk, finalized):
        assert response.status_code == 409, response.text
        body = response.json()
        assert {k: body.get(k) for k in expected} == expected


def test_a_new_inconsistency_type_after_the_override_blocks_signing(
    api, uploaded_contract, admin_token, contractor_token, signature_data_url
):
    _block_on_inconsistency(api, contractor_token, uploaded_contract)
    assert _override(api, admin_token, uploaded_contract).status_code == 200
    _patch_comparison(
        api, uploaded_contract, inconsistencies=CRITICAL_INCONSISTENCY + CRITICAL_EXP_CONFLICT
    )
    _assert_signing_blocked(
        api, contractor_token, uploaded_contract, signature_data_url,
        ["inconsistency:exp_date_conflict"],
    )


def test_a_new_missing_field_after_an_inconsistency_override_blocks_signing(
    api, uploaded_contract, admin_token, contractor_token, signature_data_url
):
    _block_on_inconsistency(api, contractor_token, uploaded_contract)
    assert _override(api, admin_token, uploaded_contract).status_code == 200
    _patch_comparison(
        api, uploaded_contract,
        missing_required=[{"field_id": "p1_cdl", "label": "CDL", "page": 1, "severity": "critical"}],
    )
    _assert_signing_blocked(api, contractor_token, uploaded_contract, signature_data_url, ["p1_cdl"])


def test_an_fmcsa_violation_after_an_inconsistency_override_blocks_signing(
    api, uploaded_contract, admin_token, contractor_token, signature_data_url
):
    _block_on_inconsistency(api, contractor_token, uploaded_contract)
    assert _override(api, admin_token, uploaded_contract).status_code == 200
    _patch_comparison(api, uploaded_contract, fmcsa_violations=CRITICAL_FMCSA)
    _assert_signing_blocked(
        api, contractor_token, uploaded_contract, signature_data_url, ["fmcsa:fmcsa_dot_eligible"]
    )


def test_fewer_issues_than_were_overridden_still_allow_signing(
    api, uploaded_contract, admin_token, contractor_token, signature_data_url
):
    """One accepted issue got corrected: the rest is still covered."""
    _block_on_missing(api, contractor_token, uploaded_contract)
    _patch_comparison(
        api, uploaded_contract, inconsistencies=CRITICAL_INCONSISTENCY + CRITICAL_EXP_CONFLICT
    )
    assert _override(api, admin_token, uploaded_contract).status_code == 200
    _patch_comparison(api, uploaded_contract, missing_required=[], inconsistencies=CRITICAL_EXP_CONFLICT)

    placed = _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        field_id="p1_contractor_signature",
    )
    assert placed.status_code == 201, placed.text
    assert _finalize(api, contractor_token, uploaded_contract).status_code == 200


def test_a_reject_clears_an_inconsistency_override(
    api, uploaded_contract, admin_token, contractor_token, signature_data_url
):
    _block_on_inconsistency(api, contractor_token, uploaded_contract)
    assert _override(api, admin_token, uploaded_contract).status_code == 200
    response = api.post(
        f"/api/review/{uploaded_contract}",
        headers=auth(admin_token),
        json={"decision": "rejected", "notes": "licence copy does not match"},
    )
    assert response.status_code == 200, response.text
    detail = _detail(api, admin_token, uploaded_contract)
    assert detail["review_override"] is False
    assert detail["review_override_fields"] == []


def test_the_override_flag_alone_does_not_open_the_signing_gate(
    api, uploaded_contract, admin_token, contractor_token, signature_data_url
):
    """Security: a stored override without an approved review is worthless."""
    from app.models.contract import Contract

    _block_on_missing(api, contractor_token, uploaded_contract)
    db = api.db_factory()
    try:
        contract = db.get(Contract, UUID(uploaded_contract))
        contract.review_override = True
        contract.review_override_reason = REASON
        contract.review_override_fields = ["p1_cdl"]
        contract.status = "signing"
        db.commit()
    finally:
        db.close()
    response = _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        field_id="p1_contractor_signature",
    )
    assert response.status_code == 409, response.text
    assert "approved by a reviewer" in response.json()["message"]


# -- re-parse ----------------------------------------------------------------


def test_a_reparse_keeps_the_override_on_record(
    api, uploaded_contract, admin_token, contractor_token
):
    _block_on_missing(api, contractor_token, uploaded_contract)
    assert _override(api, admin_token, uploaded_contract).status_code == 200
    # Re-parsing is refused during signing; step the workflow back first.
    _set_status(api, uploaded_contract, "reviewing")

    response = api.post(f"/api/contracts/{uploaded_contract}/reparse", headers=auth(admin_token))
    assert response.status_code == 202, response.text

    detail = _detail(api, admin_token, uploaded_contract)
    assert detail["status"] == "analyzed"
    assert detail["review_status"] == "approved"
    assert detail["review_override"] is True
    assert detail["review_override_reason"] == REASON
    assert detail["review_override_fields"] == ["p1_cdl"]
    # The parser read the CDL off the page again, so the override is now moot:
    # the engine clears the contract on its own.
    assert detail["comparison_result"]["can_sign"] is True
    assert "p1_cdl" not in {m["field_id"] for m in detail["comparison_result"]["missing_required"]}
