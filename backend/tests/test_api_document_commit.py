"""PDF editor v2, B1: the atomic commit (EDITOR-V2-PLAN §2 "Atomik commit")."""
from __future__ import annotations

import json
import re
from uuid import UUID, uuid4

import pytest

from tests.conftest import auth
from tests.editor_v2_helpers import (
    all_extractors,
    commit,
    compact,
    doc_url,
    edit_and_commit,
    first_page_uid,
    get_document,
    put_draft,
    replace_op,
    session_id,
    start_draft,
    text_model,
)
from tests.test_api_signing import _force_signable

TITLE = "INDEPENDENT CONTRACTOR AGREEMENT"
NEW_TITLE = "SERVICES AGREEMENT"


def _version_keys(api):
    return sorted(key for key in api.objects if re.search(r"/version-\d+-[0-9a-f]{8}\.pdf$", key))


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


def _drafted(api, token, contract_id, new_text=NEW_TITLE, fit="wrap"):
    uid = first_page_uid(api, token, contract_id)
    model = text_model(api, token, contract_id, uid)
    session = session_id()
    assert start_draft(api, token, contract_id, session).status_code in (200, 201)
    response = put_draft(api, token, contract_id, session, 0,
                         [replace_op(model, TITLE, new_text, page_uid=uid, fit=fit)])
    assert response.status_code == 200, response.text
    return response.json()["revision"]


def _signature_data_url():
    import base64
    import io

    from PIL import Image

    image = Image.new("RGBA", (240, 60), (0, 0, 0, 0))
    for x in range(10, 230):
        for y in range(28, 34):
            image.putpixel((x, y), (0, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def test_commit_creates_version_2_and_starts_the_reparse(api, contractor_token, uploaded_contract):
    revision = _drafted(api, contractor_token, uploaded_contract)
    response = commit(api, contractor_token, uploaded_contract, revision)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["version"]["version_no"] == 2 and body["version"]["kind"] == "edit"
    assert body["content_changed"] is True and body["reparse_started"] is True
    assert body["version"]["op_summary"] == {"total": 1, "by_kind": {"text.replace": 1}, "by_layer": {"content": 1}}
    assert any(w["code"] == "font_substituted" for w in body["warnings"])

    keys = _version_keys(api)
    assert keys == [f"contracts/{uploaded_contract}/version-2-{body['version']['file_hash'][:8]}.pdf"]
    new_pdf = api.get(doc_url(uploaded_contract, "/versions/2/file"), headers=auth(contractor_token)).content
    for text in all_extractors(new_pdf):
        assert TITLE not in text and compact(NEW_TITLE) in compact(text)
    old_pdf = api.get(doc_url(uploaded_contract, "/versions/1/file"), headers=auth(contractor_token)).content
    assert TITLE in compact(all_extractors(old_pdf)[0]) or "INDEPENDENT" in all_extractors(old_pdf)[0]

    doc = get_document(api, contractor_token, uploaded_contract)
    assert doc["current_version"]["version_no"] == 2 and doc["versions_count"] == 2 and doc["draft"] is None
    pages_1 = api.get(doc_url(uploaded_contract, "/versions/1/pages"), headers=auth(contractor_token)).json()
    pages_2 = api.get(doc_url(uploaded_contract, "/versions/2/pages"), headers=auth(contractor_token)).json()
    assert [p["page_uid"] for p in pages_1] == [p["page_uid"] for p in pages_2]  # stable ids

    detail = api.get(f"/api/contracts/{uploaded_contract}", headers=auth(contractor_token)).json()
    assert detail["status"] == "analyzed"  # inline reparse finished
    assert detail["document"]["current_version_no"] == 2


def test_audit_carries_no_document_text(api, contractor_token, uploaded_contract):
    revision = _drafted(api, contractor_token, uploaded_contract)
    assert commit(api, contractor_token, uploaded_contract, revision).status_code == 201
    committed = _audit(api, uploaded_contract, "document_version_committed")
    assert len(committed) == 1
    entry = committed[0]
    assert entry["version_no"] == 2 and entry["parent_version_no"] == 1
    assert entry["changed_regions"] and entry["changed_regions"][0]["origin_page"] == 1
    everything = json.dumps(
        [_audit(api, uploaded_contract, action) for action in
         ("document_version_committed", "document_created", "review_reset_by_edit", "corrections_dropped")]
    )
    for word in ("SERVICES", "INDEPENDENT", "AGREEMENT"):
        assert word not in everything


def test_commit_request_id_is_idempotent(api, contractor_token, uploaded_contract):
    revision = _drafted(api, contractor_token, uploaded_contract)
    first = commit(api, contractor_token, uploaded_contract, revision, request_id="req-idem-0001")
    assert first.status_code == 201
    again = commit(api, contractor_token, uploaded_contract, revision, request_id="req-idem-0001")
    assert again.status_code == 201
    assert again.json()["version"]["version_no"] == 2
    assert {"code": "replayed"} in again.json()["warnings"]
    assert len(_version_keys(api)) == 1
    assert get_document(api, contractor_token, uploaded_contract)["versions_count"] == 2


def test_revision_conflict(api, contractor_token, uploaded_contract):
    revision = _drafted(api, contractor_token, uploaded_contract)
    response = commit(api, contractor_token, uploaded_contract, revision - 1)
    assert response.status_code == 409 and response.json()["error"] == "revision_conflict"
    assert _version_keys(api) == []


def test_completed_contract_is_409(api, contractor_token, uploaded_contract):
    from app.models.contract import Contract

    revision = _drafted(api, contractor_token, uploaded_contract)
    db = api.db_factory()
    try:
        db.get(Contract, UUID(uploaded_contract)).status = "completed"
        db.commit()
    finally:
        db.close()
    response = commit(api, contractor_token, uploaded_contract, revision)
    assert response.status_code == 409 and response.json()["error"] == "contract_completed"
    assert _version_keys(api) == []


def test_engine_refusal_leaves_the_draft_and_no_file(api, contractor_token, uploaded_contract):
    revision = _drafted(api, contractor_token, uploaded_contract,
                        new_text="A title far too long to fit on the single line the original occupied, " * 2)
    response = commit(api, contractor_token, uploaded_contract, revision)
    assert response.status_code == 422 and response.json()["error"] == "text_overflow"
    assert _version_keys(api) == []
    assert api.get(doc_url(uploaded_contract, "/draft"), headers=auth(contractor_token)).status_code == 200


def test_database_failure_removes_the_new_file(api, contractor_token, uploaded_contract, monkeypatch):
    """Atomicity: the version file is written before the transaction; any
    failure inside it rolls the rows back AND deletes the file."""
    from app.services import audit

    revision = _drafted(api, contractor_token, uploaded_contract)
    real_log = audit.log

    def failing_log(db, action, **kwargs):
        if action == audit.DOCUMENT_VERSION_COMMITTED:
            raise RuntimeError("injected database failure")
        return real_log(db, action, **kwargs)

    monkeypatch.setattr(audit, "log", failing_log)
    with pytest.raises(RuntimeError, match="injected"):
        commit(api, contractor_token, uploaded_contract, revision)
    monkeypatch.setattr(audit, "log", real_log)

    assert _version_keys(api) == []
    doc = get_document(api, contractor_token, uploaded_contract)
    assert doc["current_version"]["version_no"] == 1 and doc["versions_count"] == 1
    assert doc["draft"] is not None  # the user's work survived


def test_lost_race_on_the_version_pointer_is_409_and_removes_the_file(api, contractor_token, uploaded_contract, monkeypatch):
    """CAS: `UPDATE documents SET current_version_id=new WHERE current_version_id=base`
    changing nothing means someone else committed first."""
    from app.models.document import Document
    from app.services import documents

    revision = _drafted(api, contractor_token, uploaded_contract)
    monkeypatch.setattr(documents, "_assert_base_current", lambda draft, document: None)
    db = api.db_factory()
    try:
        document = db.query(Document).one()
        document.current_version_id = uuid4()  # "another commit won"
        db.commit()
    finally:
        db.close()
    response = commit(api, contractor_token, uploaded_contract, revision)
    assert response.status_code == 409 and response.json()["error"] == "base_outdated"
    assert _version_keys(api) == []


def test_base_outdated_before_the_engine_runs(api, contractor_token, uploaded_contract):
    from app.models.document import DocumentDraft

    revision = _drafted(api, contractor_token, uploaded_contract)
    db = api.db_factory()
    try:
        db.query(DocumentDraft).one().base_version_id = uuid4()
        db.commit()
    finally:
        db.close()
    response = commit(api, contractor_token, uploaded_contract, revision)
    assert response.status_code == 409 and response.json()["error"] == "base_outdated"


def test_signatures_block_a_content_commit_until_discarded(api, contractor_token, uploaded_contract):
    _force_signable(api, uploaded_contract)
    placed = api.post(f"/api/contracts/{uploaded_contract}/signatures", headers=auth(contractor_token),
                      json={"field_id": "p1_contractor_signature", "image_data": _signature_data_url()})
    assert placed.status_code == 201, placed.text
    signature_key = next(key for key in api.objects if key.startswith("signatures/"))

    revision = _drafted(api, contractor_token, uploaded_contract)
    refused = commit(api, contractor_token, uploaded_contract, revision)
    assert refused.status_code == 409
    assert refused.json()["error"] == "signatures_present" and refused.json()["details"] == {"count": 1}
    assert _version_keys(api) == []

    accepted = commit(api, contractor_token, uploaded_contract, revision, discard_signatures=True)
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["remapped"] == {"signatures_kept": 0, "signatures_removed": 1}
    listed = api.get(f"/api/contracts/{uploaded_contract}/signatures", headers=auth(contractor_token)).json()
    assert listed == []
    assert signature_key not in api.objects
    removed = _audit(api, uploaded_contract, "signature_removed")
    assert removed and removed[0]["reason"] == "superseded_by_version" and removed[0]["version_no"] == 2
    assert _audit(api, uploaded_contract, "signatures_superseded") == [{"count": 1, "version_no": 2}]


def test_new_signature_rows_carry_the_page_uid(api, contractor_token, uploaded_contract):
    from app.models.signature import Signature

    uid = first_page_uid(api, contractor_token, uploaded_contract)
    _force_signable(api, uploaded_contract)
    placed = api.post(f"/api/contracts/{uploaded_contract}/signatures", headers=auth(contractor_token),
                      json={"field_id": "p1_contractor_signature", "image_data": _signature_data_url()})
    assert placed.status_code == 201
    db = api.db_factory()
    try:
        assert db.query(Signature).one().page_uid == uid
    finally:
        db.close()


def test_markup_only_commit_creates_no_version(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    session = session_id()
    start_draft(api, contractor_token, uploaded_contract, session)
    markup = [{"id": "h1", "kind": "highlight", "page_uid": uid,
               "rects": [{"x_pt": 72, "y_pt": 60, "w_pt": 200, "h_pt": 12}]}]
    saved = put_draft(api, contractor_token, uploaded_contract, session, 0, [], markup=markup)
    assert saved.status_code == 200
    response = commit(api, contractor_token, uploaded_contract, saved.json()["revision"])
    assert response.status_code == 201, response.text
    assert response.json()["version"]["version_no"] == 1
    assert response.json()["content_changed"] is False and response.json()["reparse_started"] is False
    assert api.get(doc_url(uploaded_contract, "/markup"), headers=auth(contractor_token)).json() == {
        "revision": 1, "elements": markup}
    assert _version_keys(api) == []


def test_contractor_by_email_cannot_commit(api, admin_token, contractor_token, ready_template, filled_contract_bytes):
    response = api.post(
        "/api/contracts",
        headers=auth(admin_token),
        files={"file": ("contract.pdf", filled_contract_bytes, "application/pdf")},
        data={"template_id": ready_template, "contractor_email": "driver@example.com"},
    )
    contract_id = response.json()["contract_id"]
    # B3: no draft -> 404 (a fill-only draft of their own is theirs to commit,
    # tests/test_api_fields.py); an editor's draft is never theirs: 403.
    assert commit(api, contractor_token, contract_id, 0).status_code == 404
    started = start_draft(api, admin_token, contract_id, session_id())
    assert started.status_code == 201, started.text
    refused = commit(api, contractor_token, contract_id, started.json()["revision"])
    assert refused.status_code == 403 and refused.json()["error"] == "fill_only"


def test_sweep_orphan_versions_only_touches_unreferenced_version_files(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base
    from app.services import documents
    from app.services.storage import LocalStorage

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, future=True)()
    local = LocalStorage.__new__(LocalStorage)
    local.root = tmp_path
    monkeypatch.setattr(documents, "storage", local)
    cid = str(uuid4())
    folder = tmp_path / "contracts" / cid
    folder.mkdir(parents=True)
    (folder / "original.pdf").write_bytes(b"x")
    (folder / "version-3-deadbeef.pdf").write_bytes(b"x")
    deleted = documents.sweep_orphan_versions(db, [cid])
    assert deleted == [f"contracts/{cid}/version-3-deadbeef.pdf"]
    assert (folder / "original.pdf").exists() and not (folder / "version-3-deadbeef.pdf").exists()
