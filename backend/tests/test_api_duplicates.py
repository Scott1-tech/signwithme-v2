"""Duplicate-upload warning: GET /api/contracts/duplicates and the upload audit.

What must hold: the lookup is by the lowercase hex SHA-256 of the raw PDF bytes
(the value stored in contracts.file_hash); it only ever reports contracts the
caller can open under `assert_can_view_contract` -- a contractor learns
nothing, not even a count, about another user's upload of the same file; the
answer carries no contractor PII; a malformed hash is a 422; and the upload
itself is never blocked, only annotated with `duplicate_of_count`.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from tests.conftest import _promote, _register, auth

ALLOWED_KEYS = {"id", "status", "review_status", "template_id", "template_name", "created_at"}


def _login(api, email: str) -> str:
    return api.post("/api/auth/login", json={"email": email, "password": "password123"}).json()[
        "access_token"
    ]


def _upload(api, token, template_id, data: bytes, **extra) -> str:
    response = api.post(
        "/api/contracts",
        headers=auth(token),
        files={"file": ("contract.pdf", data, "application/pdf")},
        data={"template_id": template_id, **extra},
    )
    assert response.status_code == 202, response.text
    return response.json()["contract_id"]


def _dups(api, token, sha: str):
    return api.get(f"/api/contracts/duplicates?sha256={sha}", headers=auth(token))


def _upload_audit_details(api, contract_id: str) -> dict:
    from app.models.audit import AuditLog
    from app.services import audit

    db = api.db_factory()
    try:
        row = db.scalar(
            select(AuditLog).where(
                AuditLog.action == audit.CONTRACT_UPLOADED,
                AuditLog.contract_id == uuid.UUID(contract_id),
            )
        )
        assert row is not None
        return dict(row.details)
    finally:
        db.close()


def test_matching_hash_lists_contract_without_pii(
    api, contractor_token, uploaded_contract, filled_contract_bytes, ready_template
):
    sha = hashlib.sha256(filled_contract_bytes).hexdigest()
    response = _dups(api, contractor_token, sha)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"duplicates"}
    assert [item["id"] for item in body["duplicates"]] == [uploaded_contract]
    item = body["duplicates"][0]
    assert set(item) == ALLOWED_KEYS
    assert item["template_id"] == ready_template
    assert item["template_name"] == "Driver contract"
    # Nothing the contractor typed or the parser read is in the answer.
    for secret in ("Charles Frye", "CHARLES FRYE", "charles@example.com", "123-45-6789", "driver@"):
        assert secret.lower() not in response.text.lower()


def test_uppercase_hex_matches_lowercase_stored_hash(
    api, contractor_token, uploaded_contract, filled_contract_bytes
):
    sha = hashlib.sha256(filled_contract_bytes).hexdigest().upper()
    assert [d["id"] for d in _dups(api, contractor_token, sha).json()["duplicates"]] == [
        uploaded_contract
    ]


def test_non_matching_hash_is_empty(api, contractor_token, uploaded_contract):
    response = _dups(api, contractor_token, hashlib.sha256(b"other").hexdigest())
    assert response.status_code == 200
    assert response.json() == {"duplicates": []}


def test_malformed_hash_is_422(api, contractor_token):
    for bad in ("", "abc", "g" * 64, "a" * 63, "a" * 65, "a" * 63 + "%20", "0x" + "a" * 62):
        response = api.get(
            "/api/contracts/duplicates", params={"sha256": bad}, headers=auth(contractor_token)
        )
        assert response.status_code == 422, (bad, response.status_code)
    missing = api.get("/api/contracts/duplicates", headers=auth(contractor_token))
    assert missing.status_code == 422


def test_requires_authentication(api, uploaded_contract, filled_contract_bytes):
    sha = hashlib.sha256(filled_contract_bytes).hexdigest()
    assert api.get(f"/api/contracts/duplicates?sha256={sha}").status_code in (401, 403)


def test_contractor_does_not_see_another_users_duplicate(
    api, contractor_token, uploaded_contract, filled_contract_bytes, ready_template
):
    sha = hashlib.sha256(filled_contract_bytes).hexdigest()
    other = _register(api, "other@example.com")

    # Before uploading anything: nothing, not even a count.
    assert _dups(api, other, sha).json() == {"duplicates": []}

    # After uploading the same bytes: only their own contract.
    own = _upload(api, other, ready_template, filled_contract_bytes)
    assert [d["id"] for d in _dups(api, other, sha).json()["duplicates"]] == [own]
    # And the first contractor still sees only theirs.
    assert [d["id"] for d in _dups(api, contractor_token, sha).json()["duplicates"]] == [
        uploaded_contract
    ]
    # The audit count follows the same visibility: the other contractor's
    # upload had no earlier contract *they* could see.
    assert _upload_audit_details(api, own)["duplicate_of_count"] == 0


def test_contractor_email_match_is_visible_like_assert_can_view_contract(
    api, admin_token, ready_template, filled_contract_bytes
):
    """Same rule as GET /api/contracts/{id}: a contract addressed to the
    contractor's email is theirs to see, whoever uploaded it."""
    sha = hashlib.sha256(filled_contract_bytes).hexdigest()
    addressed = _upload(
        api, admin_token, ready_template, filled_contract_bytes, contractor_email="Pat@Example.com"
    )
    pat = _register(api, "pat@example.com")
    assert api.get(f"/api/contracts/{addressed}", headers=auth(pat)).status_code == 200
    assert [d["id"] for d in _dups(api, pat, sha).json()["duplicates"]] == [addressed]


def test_reviewer_and_admin_see_every_duplicate(
    api, admin_token, contractor_token, uploaded_contract, filled_contract_bytes, ready_template
):
    sha = hashlib.sha256(filled_contract_bytes).hexdigest()
    other = _register(api, "other@example.com")
    second = _upload(api, other, ready_template, filled_contract_bytes)

    _register(api, "reviewer@example.com")
    _promote(api, "reviewer@example.com", "reviewer")
    reviewer = _login(api, "reviewer@example.com")

    for token in (admin_token, reviewer):
        ids = {d["id"] for d in _dups(api, token, sha).json()["duplicates"]}
        assert ids == {uploaded_contract, second}


def test_newest_first_and_capped_at_20(api, admin_token, ready_template):
    from app.models.contract import Contract
    from app.models.user import User

    sha = hashlib.sha256(b"synthetic").hexdigest()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = api.db_factory()
    try:
        admin = db.scalar(select(User).where(User.email == "admin@example.com"))
        ids = []
        for index in range(23):
            contract = Contract(
                id=uuid.uuid4(),
                template_id=uuid.UUID(ready_template),
                file_hash=sha,
                file_path=f"synthetic/{index}",
                uploaded_by=admin.id,
                created_at=base + timedelta(minutes=index),
            )
            db.add(contract)
            ids.append(str(contract.id))
        db.commit()
    finally:
        db.close()

    listed = [d["id"] for d in _dups(api, admin_token, sha).json()["duplicates"]]
    assert listed == list(reversed(ids))[:20]


def test_upload_is_not_blocked_and_audit_counts_earlier_duplicates(
    api, contractor_token, uploaded_contract, filled_contract_bytes, ready_template
):
    assert _upload_audit_details(api, uploaded_contract)["duplicate_of_count"] == 0

    again = _upload(api, contractor_token, ready_template, filled_contract_bytes)
    details = _upload_audit_details(api, again)
    assert details["duplicate_of_count"] == 1
    # Existing audit keys are unchanged.
    assert details["file_hash"] == hashlib.sha256(filled_contract_bytes).hexdigest()
    assert details["bytes"] == len(filled_contract_bytes)

    third = _upload(api, contractor_token, ready_template, filled_contract_bytes)
    assert _upload_audit_details(api, third)["duplicate_of_count"] == 2


def test_duplicates_route_does_not_shadow_contract_detail(api, contractor_token, uploaded_contract):
    assert api.get(f"/api/contracts/{uploaded_contract}", headers=auth(contractor_token)).status_code == 200
