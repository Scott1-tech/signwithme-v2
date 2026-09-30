"""The upload -> parse -> compare -> review path, end to end over HTTP."""
from tests.conftest import auth


def test_template_upload_returns_an_id_to_poll_not_only_a_task(api, admin_token, blank_template_bytes):
    response = api.post(
        "/api/templates",
        headers=auth(admin_token),
        files={"file": ("template.pdf", blank_template_bytes, "application/pdf")},
        data={"name": "Driver contract"},
    )
    assert response.status_code == 202
    body = response.json()
    # Without template_id the client has nothing to poll or link to.
    assert body["template_id"]
    assert api.get(f"/api/templates/{body['template_id']}", headers=auth(admin_token)).status_code == 200


def test_a_non_pdf_upload_is_rejected_on_content_not_just_extension(api, admin_token):
    response = api.post(
        "/api/templates",
        headers=auth(admin_token),
        files={"file": ("evil.pdf", b"MZ\x90\x00 this is an executable", "application/pdf")},
        data={"name": "Bad"},
    )
    assert response.status_code == 422
    assert "not a valid PDF" in response.json()["message"]


def test_contract_upload_is_refused_while_the_template_is_unanalysed(
    api, admin_token, contractor_token, filled_contract_bytes
):
    from sqlalchemy import select

    from app.models.template import Template

    api.post(
        "/api/templates",
        headers=auth(admin_token),
        files={"file": ("t.pdf", filled_contract_bytes, "application/pdf")},
        data={"name": "Pending"},
    )
    db = api.db_factory()
    try:
        template = db.scalar(select(Template))
        template.status = "processing"
        db.commit()
        template_id = str(template.id)
    finally:
        db.close()

    response = api.post(
        "/api/contracts",
        headers=auth(contractor_token),
        files={"file": ("c.pdf", filled_contract_bytes, "application/pdf")},
        data={"template_id": template_id},
    )
    assert response.status_code == 409


def test_uploaded_contract_is_parsed_and_compared(api, contractor_token, uploaded_contract):
    detail = api.get(f"/api/contracts/{uploaded_contract}", headers=auth(contractor_token)).json()
    assert detail["status"] == "analyzed"

    comparison = api.get(
        f"/api/contracts/{uploaded_contract}/compare", headers=auth(contractor_token)
    ).json()
    assert comparison["field_count"] > 0
    assert comparison["filled_count"] > 0
    # The contract is unsigned: its signature and date slots are reported as
    # pending signing work, not as missing data, so they do not block review.
    slot_ids = {slot["field_id"] for slot in comparison["signature_slots"]}
    assert {"p1_contractor_signature", "p1_date"} <= slot_ids
    missing_ids = {m["field_id"] for m in comparison["missing_required"]}
    assert "p1_contractor_signature" not in missing_ids
    assert "p1_date" not in missing_ids


def test_extracted_values_reach_the_field_rows(api, contractor_token, uploaded_contract):
    fields = api.get(
        f"/api/contracts/{uploaded_contract}/fields", headers=auth(contractor_token)
    ).json()
    by_id = {field["template_field_id"]: field for field in fields}
    assert "CHARLES FRYE" in (by_id["p1_full_name"]["raw_value"] or "")


def test_a_contractor_cannot_read_another_contractors_contract(api, uploaded_contract):
    from tests.conftest import _register

    other = _register(api, "someone-else@example.com")
    response = api.get(f"/api/contracts/{uploaded_contract}", headers=auth(other))
    assert response.status_code == 403
    assert api.get(f"/api/contracts/{uploaded_contract}/file", headers=auth(other)).status_code == 403


def test_a_missing_contract_is_a_404_not_a_500(api, contractor_token):
    response = api.get(
        "/api/contracts/00000000-0000-0000-0000-000000000000", headers=auth(contractor_token)
    )
    assert response.status_code == 404


def test_the_contract_pdf_streams_from_the_api(api, contractor_token, uploaded_contract):
    """The browser must never need to reach the object store directly -- a
    presigned link to an internal host is unreachable from a browser, and a
    public one bypasses authorisation."""
    response = api.get(f"/api/contracts/{uploaded_contract}/file", headers=auth(contractor_token))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert response.headers["cache-control"] == "no-store"
