"""Re-running detection on a stored template and re-reading a stored contract.

POST /api/templates/{id}/reanalyze rebuilds field_schema from source.pdf with
the current detector; POST /api/contracts/{id}/reparse re-reads original.pdf
against the template's current schema. Both are audited, and a re-parse must
never undo review state or a reviewer's manual corrections.
"""
from __future__ import annotations

from sqlalchemy import select

from tests.conftest import _register, auth


def _audit_actions(api, contract_id: str, token: str) -> list[str]:
    trail = api.get(f"/api/review/{contract_id}/audit", headers=auth(token)).json()
    return [entry["action"] for entry in trail]


def _set_status(api, contract_id: str, status: str) -> None:
    from uuid import UUID

    from app.models.contract import Contract

    db = api.db_factory()
    try:
        contract = db.get(Contract, UUID(contract_id))
        contract.status = status
        db.commit()
    finally:
        db.close()


# -- templates ---------------------------------------------------------------


def test_reanalyze_rebuilds_the_schema_and_is_audited(api, admin_token, ready_template):
    from uuid import UUID

    from app.models.audit import AuditLog

    before = api.get(f"/api/templates/{ready_template}/fields", headers=auth(admin_token)).json()
    # A manual nudge that re-analysis is documented to discard.
    response = api.put(
        f"/api/templates/{ready_template}/fields/p1_full_name",
        headers=auth(admin_token),
        json={"label": "Renamed by hand"},
    )
    assert response.status_code == 200

    response = api.post(f"/api/templates/{ready_template}/reanalyze", headers=auth(admin_token))
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "processing"

    detail = api.get(f"/api/templates/{ready_template}", headers=auth(admin_token)).json()
    assert detail["status"] == "ready"
    after = detail["field_schema"]
    assert after["field_count"] == before["field_count"]
    names = {f["field_id"]: f["label"] for page in after["pages"] for f in page["fields"]}
    assert names["p1_full_name"] != "Renamed by hand"
    # Baselines are rebuilt with the schema, not left behind.
    signature = next(
        f for page in after["pages"] for f in page["fields"] if f["field_type"] == "signature"
    )
    assert "template_baseline" in signature

    db = api.db_factory()
    try:
        actions = [
            entry.action
            for entry in db.scalars(
                select(AuditLog)
                .where(AuditLog.template_id == UUID(ready_template))
                .order_by(AuditLog.timestamp, AuditLog.id)
            )
        ]
        reanalyzed = db.scalar(
            select(AuditLog).where(
                AuditLog.template_id == UUID(ready_template),
                AuditLog.action == "template_reanalyzed",
            )
        )
    finally:
        db.close()
    assert actions.count("template_reanalyzed") == 1
    assert actions.count("template_analyzed") == 2
    assert reanalyzed.actor_email == "admin@example.com"
    assert reanalyzed.details["previous_field_count"] == before["field_count"]


def test_reanalyze_is_admin_only_and_404s_on_unknown_ids(api, admin_token, contractor_token, ready_template):
    assert (
        api.post(f"/api/templates/{ready_template}/reanalyze", headers=auth(contractor_token)).status_code
        == 403
    )
    assert (
        api.post(
            "/api/templates/00000000-0000-0000-0000-000000000000/reanalyze", headers=auth(admin_token)
        ).status_code
        == 404
    )


# -- contracts ---------------------------------------------------------------


def test_reparse_rereads_the_pdf_and_keeps_review_state_and_corrections(
    api, admin_token, contractor_token, uploaded_contract
):
    reviewer = _register(api, "reviewer@example.com")
    from tests.conftest import _promote

    _promote(api, "reviewer@example.com", "reviewer")
    reviewer = api.post(
        "/api/auth/login", json={"email": "reviewer@example.com", "password": "password123"}
    ).json()["access_token"]

    # A reviewer corrects a misread field, then rejects the contract.
    response = api.patch(
        f"/api/contracts/{uploaded_contract}/fields/p1_email",
        headers=auth(reviewer),
        json={"value": "corrected@example.com"},
    )
    assert response.status_code == 200 and response.json()["status"] == "verified"
    response = api.post(
        f"/api/review/{uploaded_contract}",
        headers=auth(reviewer),
        json={"decision": "rejected", "notes": "fix the email"},
    )
    assert response.status_code == 200 and response.json()["review_status"] == "rejected"
    assert response.json()["status"] == "reviewing"

    response = api.post(f"/api/contracts/{uploaded_contract}/reparse", headers=auth(reviewer))
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "parsing"

    detail = api.get(f"/api/contracts/{uploaded_contract}", headers=auth(reviewer)).json()
    assert detail["status"] == "analyzed"
    assert detail["review_status"] == "rejected"
    assert detail["review_notes"] == "fix the email"
    assert detail["comparison_result"]["field_count"] > 0

    fields = {
        f["template_field_id"]: f
        for f in api.get(f"/api/contracts/{uploaded_contract}/fields", headers=auth(reviewer)).json()
    }
    # The parser reads 'charles@example.com' off the page; the correction wins.
    assert fields["p1_email"]["raw_value"] == "corrected@example.com"
    assert fields["p1_email"]["status"] == "verified"
    # ...and a field nobody touched was re-read from the PDF.
    assert "CHARLES FRYE" in (fields["p1_full_name"]["raw_value"] or "")
    assert detail["extracted_data"]["p1_email"]["value"] == "corrected@example.com"

    actions = _audit_actions(api, uploaded_contract, admin_token)
    assert actions.count("contract_reparsed") == 1
    assert actions.count("contract_parsed") == 2
    assert actions.count("field_corrected") == 1
    assert actions.count("review_submitted") == 1
    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    reparsed = next(e for e in trail if e["action"] == "contract_reparsed")
    assert reparsed["actor_email"] == "reviewer@example.com"
    assert reparsed["details"]["previous_status"] == "reviewing"
    parsed_again = [e for e in trail if e["action"] == "contract_parsed"][-1]
    assert parsed_again["details"]["corrections_kept"] == ["p1_email"]


def test_reparse_picks_up_a_reanalysed_template_schema(api, admin_token, uploaded_contract, ready_template):
    """The point of the pair: after the template is re-analysed, a re-parse
    reads the contract with the NEW field boxes, where POST .../compare would
    only re-score the old values."""
    from uuid import UUID

    from app.models.contract import Contract

    # Move the full-name box off the page's text: a fresh parse must lose the value.
    response = api.put(
        f"/api/templates/{ready_template}/fields/p1_full_name",
        headers=auth(admin_token),
        json={"bbox": {"x": 400, "y": 5, "width": 100, "height": 14}},
    )
    assert response.status_code == 200
    response = api.post(f"/api/contracts/{uploaded_contract}/reparse", headers=auth(admin_token))
    assert response.status_code == 202
    db = api.db_factory()
    try:
        contract = db.get(Contract, UUID(uploaded_contract))
        assert contract.status == "analyzed"
        assert "p1_full_name" in {m["field_id"] for m in contract.comparison_result["missing_required"]}
    finally:
        db.close()

    # Re-analysing restores the detected box; another re-parse reads the name again.
    assert api.post(f"/api/templates/{ready_template}/reanalyze", headers=auth(admin_token)).status_code == 202
    assert api.post(f"/api/contracts/{uploaded_contract}/reparse", headers=auth(admin_token)).status_code == 202
    detail = api.get(f"/api/contracts/{uploaded_contract}", headers=auth(admin_token)).json()
    assert detail["status"] == "analyzed"
    assert "p1_full_name" not in {m["field_id"] for m in detail["comparison_result"]["missing_required"]}


def test_a_finalised_contract_is_never_reparsed(api, admin_token, uploaded_contract):
    _set_status(api, uploaded_contract, "completed")
    response = api.post(f"/api/contracts/{uploaded_contract}/reparse", headers=auth(admin_token))
    assert response.status_code == 409
    assert "finalised" in response.json()["message"]
    detail = api.get(f"/api/contracts/{uploaded_contract}", headers=auth(admin_token)).json()
    assert detail["status"] == "completed"
    assert "contract_reparsed" not in _audit_actions(api, uploaded_contract, admin_token)


def test_reparse_is_for_reviewers_and_admins_only(api, contractor_token, uploaded_contract):
    # The uploading contractor can read the contract but must not re-run it.
    response = api.post(f"/api/contracts/{uploaded_contract}/reparse", headers=auth(contractor_token))
    assert response.status_code == 403
    other = _register(api, "stranger@example.com")
    assert api.post(f"/api/contracts/{uploaded_contract}/reparse", headers=auth(other)).status_code == 403


def test_reparse_of_an_unknown_contract_is_a_404(api, admin_token):
    response = api.post(
        "/api/contracts/00000000-0000-0000-0000-000000000000/reparse", headers=auth(admin_token)
    )
    assert response.status_code == 404


def test_reparse_waits_for_a_parse_in_flight(api, admin_token, uploaded_contract):
    _set_status(api, uploaded_contract, "parsing")
    response = api.post(f"/api/contracts/{uploaded_contract}/reparse", headers=auth(admin_token))
    assert response.status_code == 409


def test_reparse_is_blocked_while_a_contract_is_being_signed(api, admin_token, uploaded_contract):
    _set_status(api, uploaded_contract, "signing")
    response = api.post(f"/api/contracts/{uploaded_contract}/reparse", headers=auth(admin_token))
    assert response.status_code == 409
    assert "being signed" in response.json()["message"]
    detail = api.get(f"/api/contracts/{uploaded_contract}", headers=auth(admin_token)).json()
    assert detail["status"] == "signing"
    assert "contract_reparsed" not in _audit_actions(api, uploaded_contract, admin_token)


def test_first_parse_is_unchanged_when_there_are_no_corrections(api, admin_token, uploaded_contract):
    """The correction-preserving path is a no-op on a fresh upload."""
    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    parsed = next(e for e in trail if e["action"] == "contract_parsed")
    assert parsed["details"]["corrections_kept"] == []
