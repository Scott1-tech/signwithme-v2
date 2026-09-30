"""PDF editor v2 API, B1: document, versions, pages, text, markup, drafts,
access control (EDITOR-V2-PLAN §6, §11 Q1/Q2)."""
from __future__ import annotations

import json
import re
from uuid import UUID

import pytest

from tests.conftest import _promote, _register, auth
from tests.editor_v2_helpers import (
    delete_op,
    doc_url,
    first_page_uid,
    get_document,
    put_draft,
    replace_op,
    session_id,
    start_draft,
    text_model,
)
from tests.test_api_signing import _force_signable


def _login(api, email):
    return api.post("/api/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


@pytest.fixture
def stranger_token(api):
    return _register(api, "stranger@example.com")


@pytest.fixture
def reviewer_token(api):
    _register(api, "reviewer@example.com")
    _promote(api, "reviewer@example.com", "reviewer")
    return _login(api, "reviewer@example.com")


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


def _contract_row(api, contract_id):
    from app.models.contract import Contract

    db = api.db_factory()
    try:
        return db.get(Contract, UUID(contract_id))
    finally:
        db.close()


def _set(api, contract_id, **values):
    from app.models.contract import Contract

    db = api.db_factory()
    try:
        contract = db.get(Contract, UUID(contract_id))
        for key, value in values.items():
            setattr(contract, key, value)
        db.commit()
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


# -- document / versions / pages / text --------------------------------------------------


def test_document_is_created_lazily_with_the_original_as_version_1(api, contractor_token, uploaded_contract):
    detail = api.get(f"/api/contracts/{uploaded_contract}", headers=auth(contractor_token)).json()
    assert detail["document"] is None  # legacy until the editor asks

    doc = get_document(api, contractor_token, uploaded_contract)
    contract = _contract_row(api, uploaded_contract)
    assert doc["current_version"]["version_no"] == 1
    assert doc["current_version"]["kind"] == "original"
    assert doc["current_version"]["file_hash"] == contract.file_hash
    assert doc["versions_count"] == 1
    assert doc["draft"] is None
    assert doc["capabilities"] == {"can_edit": True, "can_edit_markup": True, "reason": None, "can_fill": True}
    assert get_document(api, contractor_token, uploaded_contract)["document_id"] == doc["document_id"]

    detail = api.get(f"/api/contracts/{uploaded_contract}", headers=auth(contractor_token)).json()
    assert detail["document"] == {
        "document_id": doc["document_id"], "current_version_no": 1, "versions_count": 1, "draft_active": False,
    }
    assert _audit(api, uploaded_contract, "document_created")


def test_version_file_is_the_original_bytes(api, contractor_token, uploaded_contract, filled_contract_bytes):
    get_document(api, contractor_token, uploaded_contract)
    response = api.get(doc_url(uploaded_contract, "/versions/1/file"), headers=auth(contractor_token))
    assert response.status_code == 200
    assert response.content == filled_contract_bytes
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-sha256"] == _contract_row(api, uploaded_contract).file_hash
    assert api.get(doc_url(uploaded_contract, "/versions/9/file"), headers=auth(contractor_token)).status_code == 404


def test_pages_have_stable_uids_and_identity_origin(api, contractor_token, uploaded_contract):
    pages = api.get(doc_url(uploaded_contract, "/versions/1/pages"), headers=auth(contractor_token)).json()
    assert len(pages) == 1
    page = pages[0]
    assert re.fullmatch(r"pg_[0-9a-f]{12}", page["page_uid"])
    assert page["position"] == 1 and page["origin_page"] == 1
    assert (page["width_pt"], page["height_pt"]) == (612.0, 792.0)
    assert page["has_text"] and not page["scanned"]
    again = api.get(doc_url(uploaded_contract, "/versions/1/pages"), headers=auth(contractor_token)).json()
    assert again == pages


def test_page_text_model(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    model = text_model(api, contractor_token, uploaded_contract, uid)
    assert model["page_uid"] == uid and model["version_no"] == 1
    texts = [line["text"] for block in model["blocks"] for line in block["lines"]]
    assert "INDEPENDENT CONTRACTOR AGREEMENT" in texts
    assert api.get(doc_url(uploaded_contract, "/versions/1/pages/pg_ffffffffffff/text"),
                   headers=auth(contractor_token)).status_code == 404
    assert api.get(doc_url(uploaded_contract, "/versions/1/pages/../text"),
                   headers=auth(contractor_token)).status_code in (404, 422)


# -- access control (§11 Q2) --------------------------------------------------------------


def test_stranger_sees_nothing(api, contractor_token, stranger_token, uploaded_contract):
    get_document(api, contractor_token, uploaded_contract)
    for method, suffix in (("get", ""), ("get", "/versions"), ("get", "/markup"), ("get", "/versions/1/file")):
        response = getattr(api, method)(doc_url(uploaded_contract, suffix), headers=auth(stranger_token))
        assert response.status_code == 403, suffix
    response = start_draft(api, stranger_token, uploaded_contract, session_id())
    assert response.status_code == 403
    response = api.put(doc_url(uploaded_contract, "/markup"), headers=auth(stranger_token),
                       json={"revision": 0, "elements": []})
    assert response.status_code == 403


def test_unauthenticated_is_401(api, uploaded_contract):
    assert api.get(doc_url(uploaded_contract)).status_code == 401


def test_contractor_by_email_may_mark_up_but_not_edit_content(api, contractor_token, emailed_contract):
    doc = get_document(api, contractor_token, emailed_contract)
    assert doc["capabilities"] == {"can_edit": False, "can_edit_markup": True, "reason": "not_permitted",
                                   "can_fill": True}
    # B3 (§11 Q2): a draft that only FILLS forms is theirs to start; any
    # content/structure op in it is 403 `fill_only` (tests/test_api_fields.py).
    uid = first_page_uid(api, contractor_token, emailed_contract)
    session = session_id()
    started = start_draft(api, contractor_token, emailed_contract, session)
    assert started.status_code == 201, started.text
    model = text_model(api, contractor_token, emailed_contract, uid)
    line = next(line for block in model["blocks"] for line in block["lines"] if line["text"].strip())
    refused = put_draft(api, contractor_token, emailed_contract, session, started.json()["revision"],
                        [delete_op(model, line["text"], page_uid=uid)])
    assert refused.status_code == 403 and refused.json()["error"] == "fill_only", refused.text
    discarded = api.delete(doc_url(emailed_contract, f"/draft?revision={started.json()['revision']}"),
                           headers=auth(contractor_token))
    assert discarded.status_code == 204
    response = api.put(
        doc_url(emailed_contract, "/markup"),
        headers=auth(contractor_token),
        json={"revision": 0, "elements": [{"id": "h1", "kind": "highlight", "page_uid": uid,
                                           "rects": [{"x_pt": 10, "y_pt": 10, "w_pt": 50, "h_pt": 10}]}]},
    )
    assert response.status_code == 200, response.text


def test_reviewer_and_admin_may_edit_content(api, admin_token, reviewer_token, uploaded_contract):
    for token in (admin_token, reviewer_token):
        assert get_document(api, token, uploaded_contract)["capabilities"]["can_edit"] is True


# -- markup -------------------------------------------------------------------------------------


def _highlight(uid, element_id="h1"):
    return {"id": element_id, "kind": "highlight", "page_uid": uid,
            "rects": [{"x_pt": 72, "y_pt": 60, "w_pt": 200, "h_pt": 12}]}


def _text_note(uid, text="N/A", element_id="t1"):
    return {"id": element_id, "kind": "text", "page_uid": uid, "x_pt": 100, "y_pt": 300, "text": text}


def test_markup_round_trip_and_revision(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    url = doc_url(uploaded_contract, "/markup")
    assert api.get(url, headers=auth(contractor_token)).json() == {"revision": 0, "elements": []}
    saved = api.put(url, headers=auth(contractor_token), json={"revision": 0, "elements": [_highlight(uid)]})
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"revision": 1, "review_reset": False}
    got = api.get(url, headers=auth(contractor_token))
    assert got.json() == {"revision": 1, "elements": [_highlight(uid)]}
    assert got.headers["etag"] == '"rev-1"'
    stale = api.put(url, headers=auth(contractor_token), json={"revision": 0, "elements": []})
    assert stale.status_code == 409
    assert stale.json()["error"] == "revision_conflict"
    assert stale.json()["details"]["current_revision"] == 1
    audit_rows = _audit(api, uploaded_contract, "document_markup_saved")
    assert audit_rows and audit_rows[0]["by_kind"] == {"highlight": 1}


def test_markup_validation(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    url = doc_url(uploaded_contract, "/markup")
    bad_page = api.put(url, headers=auth(contractor_token), json={"revision": 0, "elements": [_highlight("pg_ffffffffffff")]})
    assert bad_page.status_code == 422 and bad_page.json()["error"] == "invalid_markup"
    legacy_page = {**_highlight(uid), "page": 1}
    assert api.put(url, headers=auth(contractor_token), json={"revision": 0, "elements": [legacy_page]}).status_code == 422
    unknown = {"id": "x", "kind": "textbox", "page_uid": uid}
    assert api.put(url, headers=auth(contractor_token), json={"revision": 0, "elements": [unknown]}).status_code == 422
    dup = [_highlight(uid), _highlight(uid)]
    assert api.put(url, headers=auth(contractor_token), json={"revision": 0, "elements": dup}).status_code == 422


def test_422_never_echoes_what_the_user_typed(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    secret = "SSN 123-45-6789 ✔"
    response = api.put(doc_url(uploaded_contract, "/markup"), headers=auth(contractor_token),
                       json={"revision": 0, "elements": [_text_note(uid, secret)]})
    assert response.status_code == 422
    assert "123-45-6789" not in response.text


def test_markup_content_change_on_an_approved_contract_reopens_review(api, contractor_token, uploaded_contract):
    """§11 Q1, decided at the markup PUT: a text/date/whiteout/image/strikeout
    element changing on an approved contract sends it back to review; a
    highlight does not."""
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    url = doc_url(uploaded_contract, "/markup")
    _force_signable(api, uploaded_contract)
    highlight = api.put(url, headers=auth(contractor_token), json={"revision": 0, "elements": [_highlight(uid)]})
    assert highlight.json()["review_reset"] is False
    assert _contract_row(api, uploaded_contract).review_status == "approved"

    content = api.put(url, headers=auth(contractor_token),
                      json={"revision": 1, "elements": [_highlight(uid), _text_note(uid)]})
    assert content.status_code == 200 and content.json()["review_reset"] is True
    contract = _contract_row(api, uploaded_contract)
    assert contract.review_status == "pending" and contract.status == "analyzed"
    assert contract.review_override is False
    reset = _audit(api, uploaded_contract, "review_reset_by_edit")
    assert reset and reset[0]["trigger"] == "markup_content"
    assert "N/A" not in json.dumps(_audit(api, uploaded_contract, "document_markup_saved"))


def test_markup_is_refused_on_a_completed_contract(api, contractor_token, uploaded_contract):
    get_document(api, contractor_token, uploaded_contract)
    _set(api, uploaded_contract, status="completed")
    response = api.put(doc_url(uploaded_contract, "/markup"), headers=auth(contractor_token),
                       json={"revision": 0, "elements": []})
    assert response.status_code == 409 and response.json()["error"] == "contract_completed"
    assert get_document(api, contractor_token, uploaded_contract)["capabilities"]["reason"] == "contract_completed"


# -- drafts --------------------------------------------------------------------------------------


def test_draft_lifecycle_lock_and_take_over(api, contractor_token, admin_token, uploaded_contract):
    mine = session_id()
    first = start_draft(api, contractor_token, uploaded_contract, mine)
    assert first.status_code == 201, first.text
    assert first.json()["revision"] == 0 and first.json()["base_version_no"] == 1 and first.json()["is_mine"]
    again = start_draft(api, contractor_token, uploaded_contract, mine)
    assert again.status_code == 200

    locked = start_draft(api, admin_token, uploaded_contract, session_id())
    assert locked.status_code == 409
    assert locked.json()["error"] == "draft_locked"
    assert locked.json()["details"]["owner_name"] == "driver"

    doc = get_document(api, admin_token, uploaded_contract)
    assert doc["draft"]["is_mine"] is False and doc["draft"]["owner_name"] == "driver"

    taken = start_draft(api, admin_token, uploaded_contract, session_id(), take_over=True)
    assert taken.status_code == 200 and taken.json()["revision"] == 1
    lost = put_draft(api, contractor_token, uploaded_contract, mine, 1, [])
    assert lost.status_code == 409 and lost.json()["error"] == "draft_taken_over"
    assert _audit(api, uploaded_contract, "document_draft_taken_over")


def test_expired_lease_can_be_taken_without_force(api, contractor_token, admin_token, uploaded_contract):
    from datetime import datetime, timedelta, timezone

    from app.models.document import DocumentDraft

    assert start_draft(api, contractor_token, uploaded_contract, session_id()).status_code == 201
    db = api.db_factory()
    try:
        draft = db.query(DocumentDraft).one()
        draft.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    assert start_draft(api, admin_token, uploaded_contract, session_id()).status_code == 200


def test_draft_put_validates_and_is_idempotent(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    model = text_model(api, contractor_token, uploaded_contract, uid)
    session = session_id()
    start_draft(api, contractor_token, uploaded_contract, session)
    op = replace_op(model, "INDEPENDENT CONTRACTOR AGREEMENT", "SERVICES AGREEMENT", page_uid=uid)

    saved = put_draft(api, contractor_token, uploaded_contract, session, 0, [op])
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    retry = put_draft(api, contractor_token, uploaded_contract, session, 0, [op])
    assert retry.status_code == 200 and retry.json()["revision"] == 1  # lost response, same payload
    conflict = put_draft(api, contractor_token, uploaded_contract, session, 0, [])
    assert conflict.status_code == 409 and conflict.json()["details"]["current_revision"] == 1

    got = api.get(doc_url(uploaded_contract, "/draft"), headers=auth(contractor_token)).json()
    assert got["ops"][0]["id"] == "op1" and got["revision"] == 1


@pytest.mark.parametrize(
    "mutate, expected_reason",
    [
        (lambda op: {**op, "page_uid": "pg_ffffffffffff"}, "unknown_page"),
        (lambda op: {**op, "target": {**op["target"], "expect_text": "not there"}}, "stale_target"),
        (lambda op: {**op, "runs": [{**op["runs"][0], "text": "tick ✔"}]}, "undrawable_character"),
    ],
)
def test_draft_put_refuses_ops_the_engine_cannot_apply(api, contractor_token, uploaded_contract, mutate, expected_reason):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    model = text_model(api, contractor_token, uploaded_contract, uid)
    session = session_id()
    start_draft(api, contractor_token, uploaded_contract, session)
    op = mutate(replace_op(model, "INDEPENDENT CONTRACTOR AGREEMENT", "X", page_uid=uid))
    response = put_draft(api, contractor_token, uploaded_contract, session, 0, [op])
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "invalid_ops"
    assert response.json()["details"]["ops"] == [{"id": "op1", "reason": expected_reason}]
    assert "tick" not in response.text


def test_draft_put_schema_refusals(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    model = text_model(api, contractor_token, uploaded_contract, uid)
    session = session_id()
    start_draft(api, contractor_token, uploaded_contract, session)
    op = replace_op(model, "INDEPENDENT CONTRACTOR AGREEMENT", "X", page_uid=uid)
    b2_kind = {"id": "r1", "kind": "redact", "page_uid": uid, "rects": [], "fill": "black"}
    assert put_draft(api, contractor_token, uploaded_contract, session, 0, [b2_kind]).status_code == 422
    assert put_draft(api, contractor_token, uploaded_contract, session, 0, [op, op]).status_code == 422
    assert put_draft(api, contractor_token, uploaded_contract, session, 0, [{**op, "layer": "content"}]).status_code == 422
    assert put_draft(api, contractor_token, uploaded_contract, session, 0, [{**op, "fit": "squash"}]).status_code == 422


def test_overflow_is_only_a_warning_while_drafting(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    model = text_model(api, contractor_token, uploaded_contract, uid)
    session = session_id()
    start_draft(api, contractor_token, uploaded_contract, session)
    op = replace_op(model, "Exp:", "Expiry date of the commercial licence, as printed", page_uid=uid)
    response = put_draft(api, contractor_token, uploaded_contract, session, 0, [op])
    assert response.status_code == 200, response.text
    assert any(w["code"] == "text_overflow" for w in response.json()["warnings"])


def test_body_over_2mb_is_413(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    model = text_model(api, contractor_token, uploaded_contract, uid)
    session = session_id()
    start_draft(api, contractor_token, uploaded_contract, session)
    base = replace_op(model, "INDEPENDENT CONTRACTOR AGREEMENT", "x" * 5000, page_uid=uid)
    ops = [{**base, "id": f"op{i}"} for i in range(450)]
    response = put_draft(api, contractor_token, uploaded_contract, session, 0, ops)
    assert response.status_code == 413


def test_lease_discard_and_markup_lock(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    session = session_id()
    start_draft(api, contractor_token, uploaded_contract, session)
    lease = api.post(doc_url(uploaded_contract, "/draft/lease"), headers=auth(contractor_token),
                     json={"revision": 0, "client_session_id": session})
    assert lease.status_code == 200 and "lease_expires_at" in lease.json()

    blocked = api.put(doc_url(uploaded_contract, "/markup"), headers=auth(contractor_token),
                      json={"revision": 0, "elements": [_highlight(uid)]})
    assert blocked.status_code == 409 and blocked.json()["error"] == "draft_active"

    wrong = api.delete(doc_url(uploaded_contract, "/draft?revision=5"), headers=auth(contractor_token))
    assert wrong.status_code == 409
    gone = api.delete(doc_url(uploaded_contract, "/draft?revision=0"), headers=auth(contractor_token))
    assert gone.status_code == 204
    assert api.get(doc_url(uploaded_contract, "/draft"), headers=auth(contractor_token)).status_code == 404
    assert _audit(api, uploaded_contract, "document_draft_discarded")


def test_preview_is_one_page_with_the_block_hidden(api, contractor_token, uploaded_contract):
    from tests.editor_v2_helpers import compact, plumber_text

    uid = first_page_uid(api, contractor_token, uploaded_contract)
    model = text_model(api, contractor_token, uploaded_contract, uid)
    session = session_id()
    start_draft(api, contractor_token, uploaded_contract, session)
    put_draft(api, contractor_token, uploaded_contract, session, 0,
              [delete_op(model, "INDEPENDENT CONTRACTOR AGREEMENT", page_uid=uid)])
    response = api.get(doc_url(uploaded_contract, f"/draft/pages/{uid}/preview?revision=1"),
                       headers=auth(contractor_token))
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    assert "INDEPENDENT" not in plumber_text(response.content)
    stale = api.get(doc_url(uploaded_contract, f"/draft/pages/{uid}/preview?revision=0"), headers=auth(contractor_token))
    assert stale.status_code == 409
    bad_hide = api.get(doc_url(uploaded_contract, f"/draft/pages/{uid}/preview?revision=1&hide=zz"),
                       headers=auth(contractor_token))
    assert bad_hide.status_code == 422
    block = next(b for b in model["blocks"] if any(l["text"].startswith("CDL") for l in b["lines"]))
    hidden = api.get(doc_url(uploaded_contract, f"/draft/pages/{uid}/preview?revision=1&hide={block['id']}"),
                     headers=auth(contractor_token))
    assert hidden.status_code == 200
    assert "CDL" not in compact(plumber_text(hidden.content))


def test_completed_contract_refuses_drafts(api, contractor_token, uploaded_contract):
    get_document(api, contractor_token, uploaded_contract)
    _set(api, uploaded_contract, status="completed")
    response = start_draft(api, contractor_token, uploaded_contract, session_id())
    assert response.status_code == 409 and response.json()["error"] == "contract_completed"


def test_contract_deletion_takes_the_document_with_it(api, contractor_token, admin_token, uploaded_contract):
    from app.models.document import Document, DocumentPage, DocumentVersion

    get_document(api, contractor_token, uploaded_contract)
    start_draft(api, contractor_token, uploaded_contract, session_id())
    assert api.delete(f"/api/contracts/{uploaded_contract}", headers=auth(admin_token)).status_code == 200
    db = api.db_factory()
    try:
        assert db.query(Document).count() == 0
        assert db.query(DocumentVersion).count() == 0
        assert db.query(DocumentPage).count() == 0
    finally:
        db.close()


def test_encrypted_upload_has_no_editor_document(api, contractor_token, uploaded_contract):
    import io

    from pypdf import PdfReader, PdfWriter

    from app.models.document import Document

    contract = _contract_row(api, uploaded_contract)
    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(api.objects[contract.file_path])).pages:
        writer.add_page(page)
    writer.encrypt(user_password="secret", owner_password="owner")
    buffer = io.BytesIO()
    writer.write(buffer)
    api.objects[contract.file_path] = buffer.getvalue()
    response = api.get(doc_url(uploaded_contract), headers=auth(contractor_token))
    assert response.status_code == 422 and response.json()["error"] == "pdf_encrypted"
    db = api.db_factory()
    try:
        assert db.query(Document).count() == 0
    finally:
        db.close()


def test_repaired_file_is_a_warning_not_a_refusal(api, contractor_token, uploaded_contract):
    from tests.editor_v2_helpers import reportlab_pdf

    contract = _contract_row(api, uploaded_contract)
    many = reportlab_pdf(lambda pdf, i: (pdf.setFont("Helvetica", 11), pdf.drawString(72, 700, f"page {i}")), pages=6)
    api.objects[contract.file_path] = many[: int(len(many) * 0.6)]
    doc = get_document(api, contractor_token, uploaded_contract)
    assert {"code": "pdf_repaired"} in doc["warnings"]