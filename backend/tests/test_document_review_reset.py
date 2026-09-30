"""PDF editor v2, B1: review integration (EDITOR-V2-PLAN §5, §11 Q1-Q3).

Invariants locked here: an edit re-opens review and clears the override;
`can_sign` comes from the unchanged engine; FMCSA still blocks; reviewer
corrections survive unless the edit touched their region."""
from __future__ import annotations

from uuid import UUID

from tests.conftest import auth
from tests.editor_v2_helpers import (
    commit,
    first_page_uid,
    put_draft,
    replace_op,
    session_id,
    start_draft,
    text_model,
)
from tests.test_api_signing import _force_signable

TITLE = "INDEPENDENT CONTRACTOR AGREEMENT"


def _contract(api, contract_id):
    from app.models.contract import Contract

    db = api.db_factory()
    try:
        return db.get(Contract, UUID(contract_id))
    finally:
        db.close()


def _audit(api, contract_id, action):
    from sqlalchemy import select

    from app.models.audit import AuditLog

    db = api.db_factory()
    try:
        return [
            row.details
            for row in db.scalars(
                select(AuditLog).where(AuditLog.contract_id == UUID(contract_id), AuditLog.action == action)
            )
        ]
    finally:
        db.close()


def _edit(api, token, contract_id, old, new, *, wide=0.0):
    uid = first_page_uid(api, token, contract_id)
    model = text_model(api, token, contract_id, uid)
    session = session_id()
    assert start_draft(api, token, contract_id, session).status_code in (200, 201)
    op = replace_op(model, old, new, page_uid=uid)
    if wide:
        op["box"] = {**op["target"]["bbox"], "w_pt": op["target"]["bbox"]["w_pt"] + wide}
    saved = put_draft(api, token, contract_id, session, 0, [op])
    assert saved.status_code == 200, saved.text
    response = commit(api, token, contract_id, saved.json()["revision"])
    assert response.status_code == 201, response.text
    return response.json()


def _approve_with_override(api, contract_id):
    from datetime import datetime, timezone

    from app.models.contract import Contract

    db = api.db_factory()
    try:
        contract = db.get(Contract, UUID(contract_id))
        contract.review_status = "approved"
        contract.status = "signing"
        contract.reviewed_at = datetime.now(timezone.utc)
        contract.review_override = True
        contract.review_override_reason = "verified against the licence"
        contract.review_override_fields = ["p1_ssn"]
        db.commit()
        return contract.reviewed_by
    finally:
        db.close()


def test_content_commit_resets_an_approved_review_and_clears_the_override(api, contractor_token, uploaded_contract):
    _approve_with_override(api, uploaded_contract)
    before = _contract(api, uploaded_contract)
    result = _edit(api, contractor_token, uploaded_contract, TITLE, "SERVICES AGREEMENT")
    assert result["review_reset"] is True
    after = _contract(api, uploaded_contract)
    assert after.review_status == "pending"
    assert after.review_override is False
    assert after.review_override_reason is None and after.review_override_fields is None
    assert after.reviewed_at == before.reviewed_at  # history kept
    assert after.status == "analyzed"  # re-parsed
    reset = _audit(api, uploaded_contract, "review_reset_by_edit")
    assert reset == [{"previous_review_status": "approved", "trigger": "content_commit", "version_no": 2}]
    versions = api.get(f"/api/contracts/{uploaded_contract}/document/versions", headers=auth(contractor_token)).json()
    assert versions[-1]["review_reset"] is True


def test_pending_review_stays_pending_without_a_reset_entry(api, contractor_token, uploaded_contract):
    assert _contract(api, uploaded_contract).review_status == "pending"
    result = _edit(api, contractor_token, uploaded_contract, TITLE, "SERVICES AGREEMENT")
    assert result["review_reset"] is False
    assert _audit(api, uploaded_contract, "review_reset_by_edit") == []


def test_can_sign_comes_from_the_unchanged_engine(api, contractor_token, uploaded_contract):
    before = _contract(api, uploaded_contract).comparison_result
    _edit(api, contractor_token, uploaded_contract, TITLE, "SERVICES AGREEMENT")
    after = _contract(api, uploaded_contract).comparison_result
    assert after["can_sign"] == before["can_sign"]
    for key in ("missing_required", "inconsistencies", "fmcsa_violations"):
        assert after[key] == before[key], key


def test_fmcsa_still_blocks_after_an_edit(api, contractor_token, admin_token, uploaded_contract, monkeypatch):
    from app.services.comparison_engine import ComparisonEngine

    original = ComparisonEngine.compare

    def with_violation(self, *args, **kwargs):
        data = original(self, *args, **kwargs).to_dict()
        data["fmcsa_violations"] = [
            {"rule": "cdl_expired", "severity": "critical", "issue": "CDL expired", "field_id": "p1_exp"}
        ]
        data["can_sign"] = False

        class Result:
            def to_dict(self):
                return dict(data)

        return Result()

    _approve_with_override(api, uploaded_contract)
    monkeypatch.setattr(ComparisonEngine, "compare", with_violation)
    _edit(api, contractor_token, uploaded_contract, TITLE, "SERVICES AGREEMENT")
    contract = _contract(api, uploaded_contract)
    assert contract.comparison_result["can_sign"] is False
    assert contract.review_override is False

    approve = api.post(
        f"/api/review/{uploaded_contract}",
        headers=auth(admin_token),
        json={"decision": "approved", "notes": "override attempt after edit", "override_missing": True},
    )
    assert approve.status_code == 409
    assert approve.json()["details"]["reason"] == "override_not_allowed"


def test_signing_is_blocked_until_re_approved(api, contractor_token, uploaded_contract):
    _force_signable(api, uploaded_contract)
    _edit(api, contractor_token, uploaded_contract, TITLE, "SERVICES AGREEMENT")
    response = api.post(f"/api/contracts/{uploaded_contract}/finalize", headers=auth(contractor_token), json={})
    assert response.status_code == 409


def _field_near(api, token, contract_id, top_pt):
    fields = api.get(f"/api/contracts/{contract_id}/fields", headers=auth(token)).json()
    candidates = [
        f for f in fields
        if f.get("bbox") and f["page_number"] == 1 and abs(f["bbox"]["y"] - top_pt) < 12
        and f["field_type"] not in ("signature", "checkbox")
    ]
    assert candidates, [(f["template_field_id"], f.get("bbox")) for f in fields]
    return candidates[0]["template_field_id"]


def test_corrections_are_dropped_only_where_the_page_changed(api, contractor_token, uploaded_contract):
    name_field = _field_near(api, contractor_token, uploaded_contract, 792 - 680 - 10)
    email_field = _field_near(api, contractor_token, uploaded_contract, 792 - 620 - 10)
    assert name_field != email_field
    for field_id, value in ((name_field, "Corrected Name"), (email_field, "fixed@example.com")):
        response = api.patch(f"/api/contracts/{uploaded_contract}/fields/{field_id}",
                             headers=auth(contractor_token), json={"value": value, "mark_verified": True})
        assert response.status_code == 200, response.text

    _edit(api, contractor_token, uploaded_contract, "CHARLES FRYE", "JOHN SMITH", wide=60)

    fields = {f["template_field_id"]: f for f in
              api.get(f"/api/contracts/{uploaded_contract}/fields", headers=auth(contractor_token)).json()}
    assert fields[email_field]["raw_value"] == "fixed@example.com"  # untouched region: kept
    assert fields[email_field]["status"] == "verified"
    assert fields[name_field]["raw_value"] != "Corrected Name"  # edited region: re-read from the PDF
    assert "JOHN" in (fields[name_field]["raw_value"] or "")
    dropped = _audit(api, uploaded_contract, "corrections_dropped")
    assert dropped and dropped[0]["field_ids"] == [name_field]
    assert dropped[0]["corrected_counts"] == {name_field: 1}

    # A NEWER correction of the same field counts again.
    response = api.patch(f"/api/contracts/{uploaded_contract}/fields/{name_field}",
                         headers=auth(contractor_token), json={"value": "Again", "mark_verified": True})
    assert response.status_code == 200
    from app.services.template_mapper import reviewer_corrections

    db = api.db_factory()
    try:
        assert name_field in reviewer_corrections(db, UUID(uploaded_contract))
    finally:
        db.close()


def test_present_pages_filters_slots_on_removed_pages():
    from app.services.comparison_engine import pending_signature_slots

    schema = {"pages": [
        {"page_number": 1, "fields": [{"field_id": "s1", "field_type": "signature", "bbox": {"x": 1, "y": 1, "width": 5, "height": 5}}]},
        {"page_number": 2, "fields": [{"field_id": "s2", "field_type": "signature", "bbox": {"x": 1, "y": 1, "width": 5, "height": 5}}]},
    ]}
    assert [s["field_id"] for s in pending_signature_slots({}, schema)] == ["s1", "s2"]
    assert [s["field_id"] for s in pending_signature_slots({}, schema, present_pages={2})] == ["s2"]


def test_page_map_interfaces_are_identity_in_b1(api, contractor_token, uploaded_contract):
    from app.services import document_review
    from app.services.documents import signing_source

    first_page_uid(api, contractor_token, uploaded_contract)  # creates the document
    from app.models.contract import Contract

    db = api.db_factory()
    try:
        source = signing_source(db.get(Contract, UUID(uploaded_contract)))
        assert not source.legacy and source.version_no == 1
        assert document_review.page_map(source) is None
        assert document_review.present_pages(source) is None
        assert source.position_for(1) == 1
    finally:
        db.close()


def test_parser_page_map_reads_the_mapped_page(filled_contract_path):
    from app.services.pdf_parser import PdfParser

    schema = {"pages": [{"page_number": 1, "fields": [
        {"field_id": "x", "field_type": "text_line", "bbox": {"x": 195, "y": 100, "width": 190, "height": 16}}]}]}
    parser = PdfParser()
    identity = parser.extract(filled_contract_path, schema)
    assert parser.extract(filled_contract_path, schema, {1: 1}) == identity
    assert parser.extract(filled_contract_path, schema, {1: None}) == {}
