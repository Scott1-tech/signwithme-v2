"""Editor v2 B3 API: `GET .../versions/{no}/fields`, field ops through the
draft, review (field.value = content re-opens it, field.add = structure does
not), fill-only contractor drafts (§11 Q2), final and export flattening,
audit without values."""
from __future__ import annotations

import io
import json
from uuid import UUID

import pytest
from pypdf import PdfReader

from tests.conftest import _promote, _register, auth
from tests.editor_v2_helpers import (
    all_extractors,
    commit,
    compact,
    doc_url,
    first_page_uid,
    get_document,
    put_draft,
    session_id,
    start_draft,
)
from tests.test_api_signing import _force_signable


def R(x, y, w, h):
    return {"x_pt": x, "y_pt": y, "w_pt": w, "h_pt": h}


def add(uid, name, ftype, rect, tab, op_id=None, **extra):
    return {"id": op_id or f"a_{name}_{tab}", "kind": "field.add", "page_uid": uid,
            "field": {"name": name, "type": ftype, "rect": rect, "tab_index": tab, **extra}}


def value(name, val, op_id=None):
    return {"id": op_id or f"v_{name}", "kind": "field.value", "name": name, "value": val}


def _login(api, email):
    return api.post("/api/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


@pytest.fixture
def stranger_token(api):
    return _register(api, "stranger@example.com")


@pytest.fixture
def emailed_contract(api, admin_token, ready_template, filled_contract_bytes):
    """Uploaded by the admin FOR driver@example.com (contractor-by-email)."""
    response = api.post(
        "/api/contracts",
        headers=auth(admin_token),
        files={"file": ("contract.pdf", filled_contract_bytes, "application/pdf")},
        data={"template_id": ready_template, "contractor_name": "Charles Frye",
              "contractor_email": "driver@example.com"},
    )
    assert response.status_code == 202, response.text
    return response.json()["contract_id"]


def _commit_ops(api, token, contract_id, ops, base_version_no=1, **commit_extra):
    session = session_id()
    started = start_draft(api, token, contract_id, session, base_version_no=base_version_no)
    assert started.status_code in (200, 201), started.text
    saved = put_draft(api, token, contract_id, session, started.json()["revision"], ops)
    assert saved.status_code == 200, saved.text
    return commit(api, token, contract_id, saved.json()["revision"], **commit_extra)


def _fields(api, token, contract_id, version_no):
    response = api.get(doc_url(contract_id, f"/versions/{version_no}/fields"), headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


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
        return [row.details for row in db.scalars(
            select(AuditLog).where(AuditLog.contract_id == UUID(contract_id), AuditLog.action == action))]
    finally:
        db.close()


FORM = [
    ("carrier", "text", R(300, 400, 200, 18), {}),
    ("pay", "radio", R(300, 430, 14, 14), {"export_value": "Weekly"}),
    ("pay", "radio", R(340, 430, 14, 14), {"export_value": "Monthly"}),
    ("agree", "checkbox", R(300, 460, 14, 14), {}),
    ("sign_here", "signature", R(300, 480, 180, 36), {}),
]


def _form_ops(uid):
    return [add(uid, name, ftype, rect, n, **extra) for n, (name, ftype, rect, extra) in enumerate(FORM)]


def test_fields_endpoint_lists_nothing_then_the_committed_fields(api, contractor_token, uploaded_contract):
    assert _fields(api, contractor_token, uploaded_contract, 1) == []
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    done = _commit_ops(api, contractor_token, uploaded_contract, _form_ops(uid))
    assert done.status_code == 201, done.text
    assert done.json()["content_changed"] is False and done.json()["review_reset"] is False
    rows = _fields(api, contractor_token, uploaded_contract, 2)
    assert [(r["name"], r["type"], r["export_value"], r["tab_index"], r["page_uid"]) for r in rows] == [
        ("carrier", "text", None, 0, uid), ("pay", "radio", "Weekly", 1, uid), ("pay", "radio", "Monthly", 2, uid),
        ("agree", "checkbox", "Yes", 3, uid), ("sign_here", "signature", None, 4, uid)]
    assert rows[0]["rect"] == R(300.0, 400.0, 200.0, 18.0) and rows[0]["value"] == "" and rows[0]["source"] == "editor"
    assert api.get(doc_url(uploaded_contract, "/versions/9/fields"), headers=auth(contractor_token)).status_code == 404


def test_field_add_keeps_review_field_value_reopens_it(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    _force_signable(api, uploaded_contract)
    added = _commit_ops(api, contractor_token, uploaded_contract, _form_ops(uid))
    assert added.status_code == 201, added.text
    assert _contract(api, uploaded_contract).review_status == "approved"  # structure: review stays

    filled = _commit_ops(api, contractor_token, uploaded_contract,
                         [value("carrier", "ABC Transport LLC"), value("pay", "Monthly"), value("agree", True)],
                         base_version_no=2)
    assert filled.status_code == 201, filled.text
    body = filled.json()
    assert body["content_changed"] is True and body["review_reset"] is True
    assert _contract(api, uploaded_contract).review_status != "approved"
    rows = {(r["name"], r["export_value"]): r for r in _fields(api, contractor_token, uploaded_contract, 3)}
    assert rows[("carrier", None)]["value"] == "ABC Transport LLC"
    assert rows[("pay", "Monthly")]["value"] == "Monthly" and rows[("agree", "Yes")]["value"] is True
    version = api.get(doc_url(uploaded_contract, "/versions"), headers=auth(contractor_token)).json()[-1]
    assert version["op_summary"]["by_layer"] == {"content": 3}

    # audit: counts, never names or values
    committed = _audit(api, uploaded_contract, "document_version_committed")
    assert committed[-1]["fields"] == {"total": 3, "by_kind": {"field.value": 3}, "by_type": {}}
    assert committed[0]["fields"]["by_type"] == {"checkbox": 1, "radio": 2, "signature": 1, "text": 1}
    dumped = json.dumps(committed)
    assert "ABC Transport" not in dumped and "carrier" not in dumped


def test_field_value_with_signatures_needs_discard(api, contractor_token, uploaded_contract):
    from tests.test_finalize_versions import _signature_data_url

    uid = first_page_uid(api, contractor_token, uploaded_contract)
    assert _commit_ops(api, contractor_token, uploaded_contract, _form_ops(uid)).status_code == 201
    _force_signable(api, uploaded_contract)
    placed = api.post(f"/api/contracts/{uploaded_contract}/signatures", headers=auth(contractor_token),
                      json={"field_id": "p1_contractor_signature", "image_data": _signature_data_url()})
    assert placed.status_code == 201, placed.text
    # a field.add is structure: the signature stays
    kept = _commit_ops(api, contractor_token, uploaded_contract, [add(uid, "extra", "text", R(300, 520, 100, 18), 9)],
                       base_version_no=2)
    assert kept.status_code == 201 and kept.json()["remapped"]["signatures_kept"] == 1
    refused = _commit_ops(api, contractor_token, uploaded_contract, [value("carrier", "X")], base_version_no=3)
    assert refused.status_code == 409 and refused.json()["error"] == "signatures_present"


def test_errors_at_save_and_commit(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    session = session_id()
    started = start_draft(api, contractor_token, uploaded_contract, session)
    saved = put_draft(api, contractor_token, uploaded_contract, session, started.json()["revision"], [
        add(uid, "a", "text", R(72, 100, 100, 20), 0),
        add(uid, "a", "text", R(72, 130, 100, 20), 1, op_id="dup"),
        value("a", "Привет", op_id="cyr"),
        value("sign", "x", op_id="nope"),
    ])
    assert saved.status_code == 422
    problems = sorted((p["id"], p["reason"]) for p in saved.json()["details"]["ops"])
    assert problems == [("cyr", "undrawable_character"), ("dup", "field_name_taken"), ("nope", "unknown_field")]
    assert "Привет" not in saved.text
    # schema-level: a dropdown without options, a bad name
    bad = put_draft(api, contractor_token, uploaded_contract, session, started.json()["revision"], [
        add(uid, "d", "dropdown", R(72, 100, 100, 20), 0)])
    assert bad.status_code == 422
    bad = put_draft(api, contractor_token, uploaded_contract, session, started.json()["revision"], [
        add(uid, "bad name", "text", R(72, 100, 100, 20), 0)])
    assert bad.status_code == 422


def test_fill_only_contractor(api, admin_token, contractor_token, emailed_contract):
    """§11 Q2: the contractor who sees the contract by e-mail fills forms --
    `field.value` only; never structure/content ops, never discard signatures."""
    uid = first_page_uid(api, admin_token, emailed_contract)
    assert _commit_ops(api, admin_token, emailed_contract, _form_ops(uid)).status_code == 201
    doc = get_document(api, contractor_token, emailed_contract)
    assert doc["capabilities"]["can_edit"] is False and doc["capabilities"]["can_fill"] is True
    assert _fields(api, contractor_token, emailed_contract, 2)[0]["name"] == "carrier"

    session = session_id()
    started = start_draft(api, contractor_token, emailed_contract, session, base_version_no=2)
    assert started.status_code == 201, started.text
    revision = started.json()["revision"]
    refused = put_draft(api, contractor_token, emailed_contract, session, revision,
                        [value("carrier", "Me"), add(uid, "mine", "text", R(72, 100, 100, 20), 9)])
    assert refused.status_code == 403 and refused.json()["error"] == "fill_only"
    assert refused.json()["details"]["ops"] == [{"id": "a_mine_9", "reason": "fill_only"}]
    saved = put_draft(api, contractor_token, emailed_contract, session, revision,
                      [value("carrier", "Me Trucking"), value("pay", "Weekly")])
    assert saved.status_code == 200, saved.text
    preview = api.get(doc_url(emailed_contract, f"/draft/preview.pdf?revision={saved.json()['revision']}"),
                      headers=auth(contractor_token))
    assert preview.status_code == 200
    assert PdfReader(io.BytesIO(preview.content)).get_fields()["carrier"]["/V"] == "Me Trucking"
    no_discard = commit(api, contractor_token, emailed_contract, saved.json()["revision"], discard_signatures=True)
    assert no_discard.status_code == 403 and no_discard.json()["error"] == "fill_only"
    done = commit(api, contractor_token, emailed_contract, saved.json()["revision"])
    assert done.status_code == 201, done.text
    rows = {(r["name"], r["export_value"]): r["value"] for r in _fields(api, contractor_token, emailed_contract, 3)}
    assert rows[("carrier", None)] == "Me Trucking" and rows[("pay", "Weekly")] == "Weekly"


def test_fill_only_user_cannot_read_or_take_over_an_editors_draft(api, admin_token, contractor_token, emailed_contract):
    uid = first_page_uid(api, admin_token, emailed_contract)
    session = session_id()
    started = start_draft(api, admin_token, emailed_contract, session)
    put_draft(api, admin_token, emailed_contract, session, started.json()["revision"],
              [add(uid, "x", "text", R(72, 100, 100, 20), 0)])
    got = api.get(doc_url(emailed_contract, "/draft"), headers=auth(contractor_token))
    assert got.status_code == 403 and got.json()["error"] == "fill_only"
    taken = start_draft(api, contractor_token, emailed_contract, session_id(), take_over=True)
    assert taken.status_code == 403 and taken.json()["error"] == "fill_only"


def test_access_control(api, contractor_token, stranger_token, uploaded_contract):
    assert api.get(doc_url(uploaded_contract, "/versions/1/fields"), headers=auth(stranger_token)).status_code == 403
    assert api.get(doc_url(uploaded_contract, "/versions/1/fields")).status_code == 401
    assert start_draft(api, stranger_token, uploaded_contract, session_id()).status_code == 403


def test_final_flattens_the_fields_and_draws_the_signature_once(api, contractor_token, uploaded_contract):
    from tests.test_finalize_versions import _final, _image_draws, _sign_and_finalize

    uid = first_page_uid(api, contractor_token, uploaded_contract)
    ops = _form_ops(uid) + [value("carrier", "ABC Transport LLC"), value("agree", True)]
    assert _commit_ops(api, contractor_token, uploaded_contract, ops).status_code == 201
    _sign_and_finalize(api, contractor_token, uploaded_contract)
    final = _final(api, contractor_token, uploaded_contract)
    reader = PdfReader(io.BytesIO(final))
    assert not reader.get_fields()
    assert all(not any(a.get_object().get("/Subtype") == "/Widget" for a in page.get("/Annots") or [])
               for page in reader.pages)
    for text in all_extractors(final):
        assert "ABCTransportLLC" in compact(text)
    assert _image_draws(final) == 1  # the one signature row, drawn once


def test_export_flatten_forms_option(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    ops = _form_ops(uid) + [value("carrier", "ABC Transport LLC")]
    assert _commit_ops(api, contractor_token, uploaded_contract, ops).status_code == 201

    def export(flatten):
        job = api.post(doc_url(uploaded_contract, "/exports"), headers=auth(contractor_token),
                       json={"flatten_forms": flatten})
        assert job.status_code == 202, job.text
        return api.get(doc_url(uploaded_contract, f"/exports/{job.json()['id']}/file"),
                       headers=auth(contractor_token)).content

    interactive = PdfReader(io.BytesIO(export(False))).get_fields()
    assert interactive["carrier"]["/V"] == "ABC Transport LLC"
    flat = export(True)
    assert not PdfReader(io.BytesIO(flat)).get_fields()
    assert all("ABCTransportLLC" in compact(t) for t in all_extractors(flat))


def test_reviewer_sees_fields(api, contractor_token, uploaded_contract):
    _register(api, "reviewer@example.com")
    _promote(api, "reviewer@example.com", "reviewer")
    reviewer = _login(api, "reviewer@example.com")
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    assert _commit_ops(api, reviewer, uploaded_contract, [add(uid, "r", "text", R(72, 100, 100, 20), 0)]).status_code == 201
    assert [r["name"] for r in _fields(api, contractor_token, uploaded_contract, 2)] == ["r"]
