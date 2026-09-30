"""Sensitive values must not sit in plaintext anywhere a reader can reach."""
from sqlalchemy import select

from app.core import pii
from tests.conftest import auth

SSN = "123-45-6789"


def test_ssn_is_encrypted_in_the_database(api, contractor_token, uploaded_contract):
    from app.models.field import ContractField

    db = api.db_factory()
    try:
        row = db.scalar(
            select(ContractField).where(ContractField.template_field_id == "p1_ssn")
        )
        assert row is not None, "the fixture contract carries an SSN field"
        assert row.raw_value is not None
        assert SSN not in row.raw_value
        assert pii.is_encrypted(row.raw_value)
        # ... and decrypting really returns the value, rather than losing it.
        assert "6789" in pii.decrypt(row.raw_value)
    finally:
        db.close()


def test_the_api_returns_a_masked_ssn(api, contractor_token, uploaded_contract):
    fields = api.get(
        f"/api/contracts/{uploaded_contract}/fields", headers=auth(contractor_token)
    ).json()
    ssn_field = next(f for f in fields if f["template_field_id"] == "p1_ssn")
    assert SSN not in (ssn_field["raw_value"] or "")
    assert ssn_field["raw_value"].startswith("XXX-XX-")
    assert ssn_field["normalized_value"] is None


def test_the_cached_extract_holds_no_plaintext(api, contractor_token, uploaded_contract):
    detail = api.get(f"/api/contracts/{uploaded_contract}", headers=auth(contractor_token)).json()
    assert SSN not in str(detail["extracted_data"])
    assert detail["extracted_data"]["p1_ssn"]["redacted"] is True


def test_the_comparison_result_holds_no_plaintext(api, contractor_token, uploaded_contract):
    comparison = api.get(
        f"/api/contracts/{uploaded_contract}/compare", headers=auth(contractor_token)
    ).json()
    assert SSN not in str(comparison)


def test_correcting_an_ssn_does_not_write_it_into_the_audit_log(
    api, contractor_token, uploaded_contract, admin_token
):
    """The audit table is append-only; a plaintext SSN written there is
    permanent."""
    api.patch(
        f"/api/contracts/{uploaded_contract}/fields/p1_ssn",
        headers=auth(contractor_token),
        json={"value": "987-65-4321"},
    )
    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    serialised = str(trail)
    assert "987-65-4321" not in serialised
    assert "987654321" not in serialised
    assert SSN not in serialised


def test_only_an_admin_can_reveal_a_sensitive_value(api, contractor_token, uploaded_contract):
    response = api.get(
        f"/api/contracts/{uploaded_contract}/fields/p1_ssn/reveal", headers=auth(contractor_token)
    )
    assert response.status_code == 403


def test_revealing_returns_the_plaintext_and_is_audited(
    api, uploaded_contract, admin_token
):
    response = api.get(
        f"/api/contracts/{uploaded_contract}/fields/p1_ssn/reveal", headers=auth(admin_token)
    )
    assert response.status_code == 200
    assert "6789" in (response.json()["value"] or "")

    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    assert any(entry["action"] == "field_revealed" for entry in trail)
    # The reveal is logged; the revealed value is not.
    assert SSN not in str(trail)


def test_a_rerun_comparison_does_not_invent_warnings_from_masked_values(
    api, contractor_token, uploaded_contract
):
    """The cache holds 'XXX-XX-6789'; format-checking that would report a bogus
    invalid-SSN warning on every re-run."""
    first = api.get(
        f"/api/contracts/{uploaded_contract}/compare", headers=auth(contractor_token)
    ).json()
    second = api.post(
        f"/api/contracts/{uploaded_contract}/compare", headers=auth(contractor_token)
    ).json()
    ssn_warnings = [w for w in second["warnings"] if w.get("field_id") == "p1_ssn"]
    assert ssn_warnings == []
    assert second["can_sign"] == first["can_sign"]
