"""Admin-only permanent deletion: contracts, and unreferenced templates (?purge=true).

What must hold: only an admin can delete; a contract the parser still owns
cannot be deleted; the audit trail is never deleted (only its FKs are nulled)
and gains one event; stored files go too; a file-storage failure after the
commit is logged, not reported as a failed deletion; and a template DELETE
without ``purge`` keeps its old archive behaviour exactly.
"""
from __future__ import annotations

import base64
import io
import os
import uuid

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.core.exceptions import StorageError
from app.services.storage import LocalStorage
from tests.conftest import _promote, _register, auth
from tests.test_api_signing import _force_signable


@pytest.fixture(autouse=True)
def prefix_calls(api, monkeypatch):
    """The harness stores objects in memory; keep folder removal off the real disk."""
    from app.services import storage as storage_module

    calls: list[str] = []

    def fake_delete_prefix(prefix: str) -> None:
        calls.append(prefix)
        for key in [k for k in api.objects if k.startswith(prefix + "/")]:
            api.objects.pop(key, None)

    monkeypatch.setattr(storage_module.storage, "delete_prefix", fake_delete_prefix)
    return calls


def _login(api, email: str) -> str:
    return api.post("/api/auth/login", json={"email": email, "password": "password123"}).json()[
        "access_token"
    ]


@pytest.fixture
def reviewer_token(api) -> str:
    _register(api, "reviewer@example.com")
    _promote(api, "reviewer@example.com", "reviewer")
    return _login(api, "reviewer@example.com")


def _signature_data_url() -> str:
    image = Image.new("RGBA", (240, 60), (0, 0, 0, 0))
    for x in range(10, 230):
        image.putpixel((x, 30), (0, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _signed_and_finalised(api, contractor_token, contract_id: str) -> None:
    _force_signable(api, contract_id)
    placed = api.post(
        f"/api/contracts/{contract_id}/signatures",
        headers=auth(contractor_token),
        json={
            "page": 1,
            "bbox": {"x": 200, "y": 580, "width": 150, "height": 34},
            "field_id": "p1_contractor_signature",
            "image_data": _signature_data_url(),
        },
    )
    assert placed.status_code == 201, placed.text
    finalized = api.post(
        f"/api/contracts/{contract_id}/finalize",
        headers=auth(contractor_token),
        json={"contract_date": "2026-08-29", "apply_paired_dates": True},
    )
    assert finalized.status_code == 200, finalized.text


def _set_status(api, contract_id: str, status: str) -> None:
    from app.models.contract import Contract

    db = api.db_factory()
    try:
        db.get(Contract, uuid.UUID(contract_id)).status = status
        db.commit()
    finally:
        db.close()


# --------------------------------------------------------------------------
# DELETE /api/contracts/{id}
# --------------------------------------------------------------------------


def test_admin_deletes_a_contract_its_rows_and_files_but_not_its_audit_trail(
    api, admin_token, contractor_token, uploaded_contract, prefix_calls
):
    from app.models.audit import AuditLog
    from app.models.contract import Contract
    from app.models.field import ContractField
    from app.models.signature import Signature

    _signed_and_finalised(api, contractor_token, uploaded_contract)
    cid = uuid.UUID(uploaded_contract)

    db = api.db_factory()
    try:
        contract = db.get(Contract, cid)
        stored_keys = [contract.file_path, contract.final_file_path] + [
            s.signature_image_path for s in db.scalars(select(Signature).where(Signature.contract_id == cid))
        ]
        template_key = contract.template.file_path
        audit_ids = set(db.scalars(select(AuditLog.id).where(AuditLog.contract_id == cid)))
        total_audit = db.scalar(select(func.count()).select_from(AuditLog))
        assert db.scalar(select(func.count()).select_from(ContractField).where(ContractField.contract_id == cid))
    finally:
        db.close()
    assert len(stored_keys) == 3 and all(key in api.objects for key in stored_keys)
    assert audit_ids

    response = api.delete(f"/api/contracts/{uploaded_contract}", headers=auth(admin_token))
    assert response.status_code == 200, response.text
    assert response.json() == {"id": uploaded_contract, "deleted": True}

    assert api.get(f"/api/contracts/{uploaded_contract}", headers=auth(admin_token)).status_code == 404
    for key in stored_keys:
        assert key not in api.objects
    assert template_key in api.objects  # the template is not touched
    assert set(prefix_calls) == {
        f"contracts/{uploaded_contract}",
        f"signatures/{uploaded_contract}",
        f"final/{uploaded_contract}",
    }

    db = api.db_factory()
    try:
        assert db.get(Contract, cid) is None
        assert not db.scalar(select(func.count()).select_from(ContractField).where(ContractField.contract_id == cid))
        assert not db.scalar(select(func.count()).select_from(Signature).where(Signature.contract_id == cid))
        # Every earlier audit row still exists, now detached from the contract.
        survivors = list(db.scalars(select(AuditLog).where(AuditLog.id.in_(audit_ids))))
        assert len(survivors) == len(audit_ids)
        assert all(row.contract_id is None for row in survivors)
        assert db.scalar(select(func.count()).select_from(AuditLog)) == total_audit + 1
        event = db.scalar(select(AuditLog).where(AuditLog.action == "contract_deleted"))
        assert event.actor_email == "admin@example.com"
        assert event.details["contract_id"] == uploaded_contract
        assert event.details["status"] == "completed"
        assert set(event.details) == {"contract_id", "status"}
    finally:
        db.close()


def test_contractor_and_reviewer_cannot_delete_a_contract(
    api, contractor_token, reviewer_token, uploaded_contract
):
    for token in (contractor_token, reviewer_token):
        response = api.delete(f"/api/contracts/{uploaded_contract}", headers=auth(token))
        assert response.status_code == 403, response.text
    assert api.get(f"/api/contracts/{uploaded_contract}", headers=auth(contractor_token)).status_code == 200


def test_deleting_an_unknown_contract_is_404(api, admin_token):
    response = api.delete(f"/api/contracts/{uuid.uuid4()}", headers=auth(admin_token))
    assert response.status_code == 404


@pytest.mark.parametrize("status", ["uploaded", "parsing"])
def test_a_contract_still_being_parsed_cannot_be_deleted(api, admin_token, uploaded_contract, status):
    _set_status(api, uploaded_contract, status)
    response = api.delete(f"/api/contracts/{uploaded_contract}", headers=auth(admin_token))
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "conflict"
    assert api.get(f"/api/contracts/{uploaded_contract}", headers=auth(admin_token)).status_code == 200


def test_a_file_storage_failure_after_commit_does_not_fail_the_deletion(
    api, admin_token, uploaded_contract, monkeypatch
):
    from app.services import storage as storage_module

    def broken(_key):
        raise StorageError("disk on fire")

    monkeypatch.setattr(storage_module.storage, "delete", broken)
    monkeypatch.setattr(storage_module.storage, "delete_prefix", broken)
    response = api.delete(f"/api/contracts/{uploaded_contract}", headers=auth(admin_token))
    assert response.status_code == 200, response.text
    assert api.get(f"/api/contracts/{uploaded_contract}", headers=auth(admin_token)).status_code == 404


# --------------------------------------------------------------------------
# DELETE /api/templates/{id}[?purge=true]
# --------------------------------------------------------------------------


def test_purging_a_referenced_template_is_409_with_the_contract_count(
    api, admin_token, ready_template, uploaded_contract
):
    response = api.delete(f"/api/templates/{ready_template}?purge=true", headers=auth(admin_token))
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"] == "conflict"
    assert body["details"] == {"contract_count": 1}
    detail = api.get(f"/api/templates/{ready_template}", headers=auth(admin_token))
    assert detail.status_code == 200 and detail.json()["status"] == "ready"


def test_purging_an_unreferenced_template_deletes_it(api, admin_token, ready_template, prefix_calls):
    from app.models.audit import AuditLog
    from app.models.template import Template

    db = api.db_factory()
    try:
        key = db.get(Template, uuid.UUID(ready_template)).file_path
        audit_ids = set(
            db.scalars(select(AuditLog.id).where(AuditLog.template_id == uuid.UUID(ready_template)))
        )
    finally:
        db.close()
    assert key in api.objects and audit_ids

    response = api.delete(f"/api/templates/{ready_template}?purge=true", headers=auth(admin_token))
    assert response.status_code == 200, response.text
    assert response.json() == {"id": ready_template, "deleted": True}
    assert api.get(f"/api/templates/{ready_template}", headers=auth(admin_token)).status_code == 404
    assert key not in api.objects
    assert prefix_calls == [f"templates/{ready_template}"]

    db = api.db_factory()
    try:
        survivors = list(db.scalars(select(AuditLog).where(AuditLog.id.in_(audit_ids))))
        assert len(survivors) == len(audit_ids)
        assert all(row.template_id is None for row in survivors)
        event = db.scalar(select(AuditLog).where(AuditLog.action == "template_deleted"))
        assert event.details["template_id"] == ready_template
    finally:
        db.close()


def test_delete_without_purge_still_only_archives(api, admin_token, ready_template, uploaded_contract):
    from app.models.template import Template

    response = api.delete(f"/api/templates/{ready_template}", headers=auth(admin_token))
    assert response.status_code == 200, response.text
    assert response.json() == {"template_id": ready_template, "status": "archived"}
    db = api.db_factory()
    try:
        template = db.get(Template, uuid.UUID(ready_template))
        assert template is not None and template.status == "archived"
        assert template.file_path in api.objects
    finally:
        db.close()
    # purge=false is the same as no parameter.
    again = api.delete(f"/api/templates/{ready_template}?purge=false", headers=auth(admin_token))
    assert again.json() == {"template_id": ready_template, "status": "archived"}


def test_template_purge_requires_admin_and_an_existing_template(
    api, admin_token, contractor_token, ready_template
):
    denied = api.delete(f"/api/templates/{ready_template}?purge=true", headers=auth(contractor_token))
    assert denied.status_code == 403
    missing = api.delete(f"/api/templates/{uuid.uuid4()}?purge=true", headers=auth(admin_token))
    assert missing.status_code == 404


# --------------------------------------------------------------------------
# LocalStorage.delete_prefix
# --------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> LocalStorage:
    backend = LocalStorage(root=str(tmp_path / "storage"))
    backend.ensure_bucket()
    return backend


def test_delete_prefix_removes_one_id_folder_only(store):
    a, b = uuid.uuid4(), uuid.uuid4()
    store.put_bytes(f"signatures/{a}/one.png", b"1")
    store.put_bytes(f"signatures/{a}/two.png", b"2")
    store.put_bytes(f"signatures/{b}/keep.png", b"3")
    store.delete_prefix(f"signatures/{a}")
    assert not (store.root / "signatures" / str(a)).exists()
    assert store.get_bytes(f"signatures/{b}/keep.png") == b"3"
    store.delete_prefix(f"final/{uuid.uuid4()}")  # absent folder: no error


@pytest.mark.parametrize(
    "prefix",
    ["", "contracts", "contracts/", "contracts/../templates", "contracts/a/b", "../x/y", "other/abc", "C:/x"],
)
def test_delete_prefix_refuses_anything_wider_than_one_id_folder(store, prefix):
    with pytest.raises(StorageError):
        store.delete_prefix(prefix)
