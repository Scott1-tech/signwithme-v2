"""Editor v2 F3: share-for-fill links (EDITOR-V2-PLAN §8, §11 Q4 -- LAN only,
fill only). Internal create/list/revoke; the public `/api/share/*` gate: LAN
peer address only (X-Forwarded-For ignored), one identical 404 for every bad
token, 429 after 20 failures per address, security headers, no token anywhere
but the create response (DB, audit, logs), no contract id in public answers."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from tests.conftest import _register, auth
from tests.editor_v2_helpers import doc_url
from tests.share_helpers import (
    FILLABLE,
    R,
    add,
    audit_rows,
    client_at,
    commit_ops,
    create_link,
    created,
    db_rows,
    dump_database,
    links_url,
    submit,
    tok,
    with_form,
)


@pytest.fixture
def form_contract(api, contractor_token, uploaded_contract) -> str:
    with_form(api, contractor_token, uploaded_contract)
    return uploaded_contract


@pytest.fixture
def lan(api):
    return client_at(api)


def _set_link(api, link_id, **values):
    from app.models.share import ShareLink

    db = api.db_factory()
    try:
        link = db.get(ShareLink, UUID(link_id))
        for key, value in values.items():
            setattr(link, key, value)
        db.commit()
    finally:
        db.close()


def _assert_private_headers(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"


# -- create / list / revoke ---------------------------------------------------------


def test_create_returns_the_token_once_and_stores_only_its_hash(api, contractor_token, form_contract):
    response = create_link(api, contractor_token, form_contract, recipient_label="Driver Bob")
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    token = body["token"]
    assert len(token) >= 43
    assert body["url"] == f"http://10.31.0.58:5173/s#t={token}"
    assert body["version_no"] == 2 and body["status"] == "active"
    assert body["allowed_fields"] == FILLABLE and body["max_submissions"] == 1
    assert body["submission_count"] == 0 and body["recipient_label"] == "Driver Bob"
    expires = datetime.fromisoformat(body["expires_at"])
    assert abs((expires - datetime.now(timezone.utc)) - timedelta(hours=72)) < timedelta(minutes=1)

    from app.models.share import ShareLink

    [row] = db_rows(api, ShareLink)
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in dump_database(api)

    listed = api.get(links_url(form_contract), headers=auth(contractor_token))
    assert listed.status_code == 200
    assert "token" not in listed.json()[0] and token not in listed.text and row.token_hash not in listed.text

    [(details, actor, _ip)] = audit_rows(api, "share_link_created")
    assert actor is not None and details["allowed_fields"] == len(FILLABLE) and details["version_no"] == 2
    assert token not in json.dumps(details) and row.token_hash not in json.dumps(details)


def test_expiry_default_max_and_bounds(api, contractor_token, form_contract):
    assert created(api, contractor_token, form_contract, expires_in_hours=336)["status"] == "active"
    too_long = create_link(api, contractor_token, form_contract, expires_in_hours=337)
    assert too_long.status_code == 422 and too_long.json()["error"] == "expiry_too_long"
    assert create_link(api, contractor_token, form_contract, expires_in_hours=0).status_code == 422
    assert create_link(api, contractor_token, form_contract, max_submissions=0).status_code == 422
    assert create_link(api, contractor_token, form_contract, fields=[]).status_code == 422
    assert create_link(api, contractor_token, form_contract, fields=["carrier", "carrier"]).status_code == 422


def test_only_fillable_fields_can_be_shared(api, contractor_token, form_contract):
    response = create_link(api, contractor_token, form_contract,
                           fields=["carrier", "sign_here", "locked", "no such field X"])
    assert response.status_code == 422 and response.json()["error"] == "fields_not_shareable"
    assert response.json()["details"]["fields"] == [
        {"index": 1, "reason": "field_not_fillable"},
        {"index": 2, "reason": "field_readonly"},
        {"index": 3, "reason": "unknown_field"},
    ]
    assert "no such field X" not in response.text


def test_revoke_is_idempotent_and_scoped_to_the_contract(api, contractor_token, admin_token, form_contract,
                                                         ready_template, filled_contract_bytes, lan):
    link = created(api, contractor_token, form_contract)
    assert lan.get("/api/share/session", headers=tok(link["token"])).status_code == 200
    first = api.delete(links_url(form_contract, f"/{link['id']}"), headers=auth(contractor_token))
    assert first.status_code == 204
    again = api.delete(links_url(form_contract, f"/{link['id']}"), headers=auth(contractor_token))
    assert again.status_code == 204
    assert len(audit_rows(api, "share_link_revoked")) == 1
    listed = api.get(links_url(form_contract), headers=auth(contractor_token)).json()
    assert listed[0]["status"] == "revoked" and listed[0]["revoked_at"] is not None
    assert lan.get("/api/share/session", headers=tok(link["token"])).status_code == 404

    other = api.post("/api/contracts", headers=auth(admin_token),
                     files={"file": ("c.pdf", filled_contract_bytes, "application/pdf")},
                     data={"template_id": ready_template}).json()["contract_id"]
    wrong = api.delete(links_url(other, f"/{link['id']}"), headers=auth(admin_token))
    assert wrong.status_code == 404


def test_authorization(api, admin_token, contractor_token, ready_template, filled_contract_bytes):
    """§11 Q2: only an admin, a reviewer or the uploader. The contractor who
    sees the contract by e-mail (fill-only) may not share it."""
    emailed = api.post("/api/contracts", headers=auth(admin_token),
                       files={"file": ("c.pdf", filled_contract_bytes, "application/pdf")},
                       data={"template_id": ready_template, "contractor_email": "driver@example.com"}
                       ).json()["contract_id"]
    with_form(api, admin_token, emailed)
    stranger = _register(api, "stranger@example.com")
    for token, code in ((contractor_token, 403), (stranger, 403)):
        assert create_link(api, token, emailed).status_code == code
        assert api.get(links_url(emailed), headers=auth(token)).status_code == code
    assert api.get(links_url(emailed)).status_code == 401
    assert api.post(links_url(emailed), json={"allowed_fields": ["carrier"]}).status_code == 401
    assert create_link(api, admin_token, emailed).status_code == 201


# -- the public gate ---------------------------------------------------------------


def test_lan_only_by_socket_address_and_forwarded_for_is_ignored(api, contractor_token, form_contract):
    token = created(api, contractor_token, form_contract, max_submissions=5)["token"]
    for ip in ("10.31.0.77", "172.16.4.2", "172.31.255.1", "192.168.1.9", "127.0.0.1", "::ffff:10.1.2.3"):
        response = client_at(api, ip).get("/api/share/session", headers=tok(token))
        assert response.status_code == 200, (ip, response.text)
    for ip in ("8.8.8.8", "172.32.0.1", "100.64.0.1", "::1", "2001:db8::1"):
        response = client_at(api, ip).get("/api/share/session", headers=tok(token))
        assert response.status_code == 404, ip
        _assert_private_headers(response)
    # the default TestClient peer ("testclient") is not an address at all
    assert api.get("/api/share/session", headers=tok(token)).status_code == 404
    # an outside peer claiming a LAN address: still 404
    outside = client_at(api, "203.0.113.5")
    assert outside.get("/api/share/session", headers={**tok(token), "X-Forwarded-For": "10.31.0.5"}).status_code == 404
    assert outside.get("/api/share/file", headers={**tok(token), "X-Forwarded-For": "10.31.0.5"}).status_code == 404
    assert submit(outside, token, {"carrier": "X"}).status_code == 404
    # a LAN peer claiming an outside address: the claim changes nothing
    inside = client_at(api, "10.31.0.9")
    assert inside.get("/api/share/session", headers={**tok(token), "X-Forwarded-For": "8.8.8.8"}).status_code == 200
    # outside requests are not failures of the LAN address
    from app.core.throttle import share_throttle

    assert share_throttle.is_locked("203.0.113.5") == 0


def test_one_identical_404_for_every_bad_token(api, contractor_token, form_contract, lan):
    revoked = created(api, contractor_token, form_contract)
    api.delete(links_url(form_contract, f"/{revoked['id']}"), headers=auth(contractor_token))
    expired = created(api, contractor_token, form_contract)
    _set_link(api, expired["id"], expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    used = created(api, contractor_token, form_contract, max_submissions=1)
    assert submit(lan, used["token"], {"carrier": "ABC"}).status_code == 201

    cases = {
        "unknown": {"X-Share-Token": "A" * 43},
        "malformed": {"X-Share-Token": "not a token"},
        "missing": {},
        "revoked": tok(revoked["token"]),
        "expired": tok(expired["token"]),
        "used_up": tok(used["token"]),
    }
    bodies = set()
    for name, headers in cases.items():
        for method, path, extra in (("get", "/api/share/session", {}), ("get", "/api/share/file", {}),
                                    ("post", "/api/share/submissions", {"json": {"values": {"carrier": "Z"}}})):
            response = getattr(lan, method)(path, headers=headers, **extra)
            assert response.status_code == 404, (name, path, response.text)
            _assert_private_headers(response)
            bodies.add(response.text)
    assert bodies == {json.dumps({"error": "not_found", "message": "Not found", "details": {}}, separators=(",", ":"))}


def test_a_finalised_contract_closes_its_links(api, contractor_token, form_contract, lan):
    link = created(api, contractor_token, form_contract)
    from app.models.contract import Contract

    db = api.db_factory()
    try:
        db.get(Contract, UUID(form_contract)).status = "completed"
        db.commit()
    finally:
        db.close()
    assert lan.get("/api/share/session", headers=tok(link["token"])).status_code == 404
    refused = create_link(api, contractor_token, form_contract)
    assert refused.status_code == 409 and refused.json()["error"] == "contract_completed"


def test_throttle_429_after_20_failures_per_address(api, contractor_token, form_contract):
    token = created(api, contractor_token, form_contract, max_submissions=3)["token"]
    attacker = client_at(api, "10.9.9.9")
    for n in range(20):
        assert attacker.get("/api/share/session", headers={"X-Share-Token": f"{n:0>43}"}).status_code == 404
    locked = attacker.get("/api/share/session", headers=tok(token))
    assert locked.status_code == 429 and locked.json()["error"] == "too_many_attempts"
    _assert_private_headers(locked)
    assert submit(attacker, token, {"carrier": "X"}).status_code == 429
    # another address is not affected
    assert client_at(api, "10.9.9.10").get("/api/share/session", headers=tok(token)).status_code == 200
    # the window passes -> usable again
    from app.core.throttle import share_throttle

    share_throttle.reset("10.9.9.9")
    assert attacker.get("/api/share/session", headers=tok(token)).status_code == 200


def test_session_and_file(api, contractor_token, form_contract, lan):
    link = created(api, contractor_token, form_contract, fields=["carrier", "pay", "agree"])
    session = lan.get("/api/share/session", headers=tok(link["token"]))
    assert session.status_code == 200, session.text
    _assert_private_headers(session)
    body = session.json()
    assert body["document_title"] == "Driver contract" and body["version_no"] == 2
    assert body["submissions_left"] == 1
    assert [(f["name"], f["type"], f["export_value"]) for f in body["fields"]] == [
        ("carrier", "text", None), ("pay", "radio", "Weekly"), ("pay", "radio", "Monthly"), ("agree", "checkbox", "Yes")]
    assert body["fields"][0]["required"] is True and body["fields"][0]["rect"] == R(300.0, 400.0, 200.0, 18.0)
    assert [p["position"] for p in body["pages"]] == [1] and body["pages"][0]["page_uid"] == body["fields"][0]["page_uid"]
    # no contract id, no document id, no contractor anywhere
    from app.models.document import Document

    [document] = db_rows(api, Document)
    for secret in (form_contract, form_contract.replace("-", ""), str(document.id), "Charles Frye", "driver@example.com"):
        assert secret not in session.text

    # the file is the pinned version, byte for byte (no markup, no signature)
    markup = api.put(doc_url(form_contract, "/markup"), headers=auth(contractor_token),
                     json={"revision": 0, "elements": [{"id": "h1", "kind": "highlight", "page_uid": body["pages"][0]["page_uid"],
                                                        "rects": [{"x_pt": 72, "y_pt": 60, "w_pt": 200, "h_pt": 12}]}]})
    version_file = api.get(doc_url(form_contract, "/versions/2/file"), headers=auth(contractor_token)).content
    shared = lan.get("/api/share/file", headers=tok(link["token"]))
    assert shared.status_code == 200 and shared.headers["content-type"] == "application/pdf"
    _assert_private_headers(shared)
    assert shared.content == version_file
    assert markup.status_code == 200, markup.text  # server markup exists -- and is not in the shared file

    # the link stays pinned to version 2 when the document moves on
    uid = body["pages"][0]["page_uid"]
    moved = commit_ops(api, contractor_token, form_contract, [add(uid, "later", "text", R(72, 100, 100, 18), 20)],
                       base_version_no=2)
    assert moved.status_code == 201, moved.text
    again = lan.get("/api/share/session", headers=tok(link["token"])).json()
    assert again["version_no"] == 2 and "later" not in {f["name"] for f in again["fields"]}
    listed = api.get(links_url(form_contract), headers=auth(contractor_token)).json()[0]
    assert listed["last_used_at"] is not None


def test_max_submissions(api, contractor_token, form_contract, lan):
    link = created(api, contractor_token, form_contract, max_submissions=2)
    first = submit(lan, link["token"], {"carrier": "One"})
    assert first.status_code == 201 and first.json() == {"status": "received", "submissions_left": 1}
    assert submit(lan, link["token"], {"carrier": "Two"}).json()["submissions_left"] == 0
    assert submit(lan, link["token"], {"carrier": "Three"}).status_code == 404
    assert lan.get("/api/share/session", headers=tok(link["token"])).status_code == 404
    listed = api.get(links_url(form_contract), headers=auth(contractor_token)).json()[0]
    assert listed["status"] == "used_up" and listed["submission_count"] == 2 and listed["submissions_pending"] == 2


def test_the_token_never_reaches_a_log_or_the_database(api, contractor_token, form_contract, lan, caplog):
    caplog.set_level(logging.DEBUG)
    link = created(api, contractor_token, form_contract, max_submissions=2)
    token = link["token"]
    lan.get("/api/share/session", headers=tok(token))
    lan.get("/api/share/file", headers=tok(token))
    submit(lan, token, {"carrier": "Logged?", "ssn": "123-45-6789"})
    submit(lan, token, {"carrier": "x" * 10, "short": "too long for five"})  # a 422
    lan.get("/api/share/session", headers={"X-Share-Token": token[:-1] + ("A" if token[-1] != "A" else "B")})
    api.delete(links_url(form_contract, f"/{link['id']}"), headers=auth(contractor_token))
    lan.get("/api/share/session", headers=tok(token))  # revoked -> 404
    assert token not in caplog.text
    assert token not in dump_database(api)
    from app.models.share import ShareLink

    [row] = db_rows(api, ShareLink)
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert row.token_hash not in caplog.text


def test_contract_deletion_removes_links_and_submissions(api, admin_token, contractor_token, form_contract, lan):
    link = created(api, contractor_token, form_contract)
    assert submit(lan, link["token"], {"carrier": "ABC"}).status_code == 201
    from app.models.share import FormSubmission, ShareLink

    assert len(db_rows(api, ShareLink)) == 1 and len(db_rows(api, FormSubmission)) == 1
    deleted = api.delete(f"/api/contracts/{form_contract}", headers=auth(admin_token))
    assert deleted.status_code == 200, deleted.text
    assert db_rows(api, ShareLink) == [] and db_rows(api, FormSubmission) == []
    assert lan.get("/api/share/session", headers=tok(link["token"])).status_code == 404
