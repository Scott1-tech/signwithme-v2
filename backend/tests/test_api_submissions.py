"""Editor v2 F3: form submissions through a share link (EDITOR-V2-PLAN §8).

Public submit: allowed fields only, the B3 `field.value` checks (type,
options, max_len, read-only, drawable characters) + `required`, nothing quoted
back; values encrypted at rest. Internal: list (decrypted, PII masked), accept
-> a draft of `field.value` ops -> the editor saves -> content commit -> review
pending; reject; `draft_locked`; authorization; audit without values."""
from __future__ import annotations

import json
from uuid import UUID

import pytest

from tests.conftest import auth
from tests.editor_v2_helpers import commit, doc_url, get_document, session_id, start_draft
from tests.share_helpers import (
    audit_rows,
    client_at,
    commit_ops,
    created,
    db_rows,
    dump_database,
    submissions_url,
    submit,
    with_form,
)
from tests.test_api_signing import _force_signable


@pytest.fixture
def form_contract(api, contractor_token, uploaded_contract) -> str:
    with_form(api, contractor_token, uploaded_contract)
    return uploaded_contract


@pytest.fixture
def lan(api):
    return client_at(api)


@pytest.fixture
def link(api, contractor_token, form_contract) -> dict:
    return created(api, contractor_token, form_contract, max_submissions=5, recipient_label="Bob")


def _contract(api, contract_id):
    from app.models.contract import Contract

    db = api.db_factory()
    try:
        return db.get(Contract, UUID(contract_id))
    finally:
        db.close()


def _fields(api, token, contract_id, version_no):
    response = api.get(doc_url(contract_id, f"/versions/{version_no}/fields"), headers=auth(token))
    assert response.status_code == 200, response.text
    return {(r["name"], r["export_value"]): r["value"] for r in response.json()}


def _pending(api, token, contract_id):
    response = api.get(submissions_url(contract_id, "?status=received"), headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


GOOD = {"carrier": "ABC Transport LLC", "pay": "Monthly", "agree": True, "plan": "Plus",
        "notes": "Line one\nLine two", "short": "12345"}


# -- validation --------------------------------------------------------------------


def test_validation_refuses_without_quoting_and_without_using_the_link(api, contractor_token, form_contract, link, lan):
    token = link["token"]
    cases = [
        ({"carrier": "OK", "locked": "sneaky"}, {"fields": [], "not_allowed": 1}),
        ({"carrier": "OK", "sign_here": "x"}, {"fields": [], "not_allowed": 1}),
        ({"carrier": "OK", "who knows SECRETNAME": "x"}, {"fields": [], "not_allowed": 1}),
        ({"carrier": "OK", "plan": "Gold"}, {"fields": [{"name": "plan", "reason": "invalid_field_value"}], "not_allowed": 0}),
        ({"carrier": "OK", "short": "123456"}, {"fields": [{"name": "short", "reason": "invalid_field_value"}], "not_allowed": 0}),
        ({"carrier": "Привет"}, {"fields": [{"name": "carrier", "reason": "undrawable_character"}], "not_allowed": 0}),
        ({"carrier": "a\nb"}, {"fields": [{"name": "carrier", "reason": "invalid_field_value"}], "not_allowed": 0}),
        ({"carrier": "OK", "agree": "yes"}, {"fields": [{"name": "agree", "reason": "invalid_field_value"}], "not_allowed": 0}),
        ({"carrier": "OK", "pay": "Yearly"}, {"fields": [{"name": "pay", "reason": "invalid_field_value"}], "not_allowed": 0}),
        ({"notes": "no carrier"}, {"fields": [{"name": "carrier", "reason": "required"}], "not_allowed": 0}),
        ({"carrier": "   "}, {"fields": [{"name": "carrier", "reason": "required"}], "not_allowed": 0}),
    ]
    for values, details in cases:
        response = submit(lan, token, values)
        assert response.status_code == 422, (values, response.text)
        assert response.json()["error"] == "invalid_submission", response.text
        assert response.json()["details"] == details, (values, response.text)
        for value in values.values():
            if isinstance(value, str) and value.strip() and value not in ("OK",):
                assert value not in response.text
        assert "SECRETNAME" not in response.text
    empty = submit(lan, token, {})
    assert empty.status_code == 422 and empty.json()["error"] == "empty_submission"
    # a schema-level refusal (too long for any field) does not echo either
    huge = submit(lan, token, {"notes": "Q" * 5001, "carrier": "OK"})
    assert huge.status_code == 422 and "QQQQ" not in huge.text
    bad_mail = submit(lan, token, {"carrier": "OK"}, submitter_email="not-an-email")
    assert bad_mail.status_code == 422 and "not-an-email" not in bad_mail.text
    # none of these used up a submission
    assert lan.get("/api/share/session", headers={"X-Share-Token": token}).json()["submissions_left"] == 5
    from app.models.share import FormSubmission

    assert db_rows(api, FormSubmission) == []


def test_values_are_encrypted_at_rest_and_masked_when_sensitive(api, contractor_token, form_contract, link, lan):
    response = submit(lan, link["token"], {**GOOD, "ssn": "123-45-6789"},
                      submitter_name="Bob Driver", submitter_email="bob@example.com")
    assert response.status_code == 201, response.text
    assert response.json() == {"status": "received", "submissions_left": 4}

    from app.models.share import FormSubmission

    [row] = db_rows(api, FormSubmission)
    assert row.values_encrypted.startswith("enc:v1:") and row.field_count == 7
    assert row.ip_address == "10.31.0.77" and row.status == "received"
    dumped = dump_database(api)
    for plain in ("ABC Transport LLC", "123-45-6789", "Line one", "Line two"):
        assert plain not in dumped

    [listed] = _pending(api, contractor_token, form_contract)
    assert listed["values_readable"] is True and listed["recipient_label"] == "Bob" and listed["version_no"] == 2
    assert listed["values"]["carrier"] == "ABC Transport LLC" and listed["values"]["agree"] is True
    assert listed["values"]["ssn"] == "XXX-XX-6789"
    assert listed["submitter_name"] == "Bob Driver" and listed["submitter_email"] == "bob@example.com"

    [(details, actor, ip)] = audit_rows(api, "form_submission_received")
    assert actor is None and ip == "10.31.0.77"
    assert details == {"submission_id": listed["id"], "share_link_id": link["id"], "fields": 7}


def test_a_changed_key_makes_values_unreadable_not_wrong(api, contractor_token, form_contract, link, lan, monkeypatch):
    assert submit(lan, link["token"], GOOD).status_code == 201
    from app.config import settings

    monkeypatch.setattr(settings, "pii_encryption_key", "another-key-entirely")
    [listed] = _pending(api, contractor_token, form_contract)
    assert listed["values"] is None and listed["values_readable"] is False
    refused = api.post(submissions_url(form_contract, f"/{listed['id']}/accept"), headers=auth(contractor_token),
                       json={"client_session_id": session_id()})
    assert refused.status_code == 409 and refused.json()["error"] == "submission_unreadable"


# -- accept / reject -----------------------------------------------------------------


def test_accept_makes_a_draft_the_editor_saves_and_review_reopens(api, contractor_token, form_contract, link, lan):
    _force_signable(api, form_contract)
    assert _contract(api, form_contract).review_status == "approved"
    assert submit(lan, link["token"], GOOD).status_code == 201
    [pending] = _pending(api, contractor_token, form_contract)

    session = session_id()
    accepted = api.post(submissions_url(form_contract, f"/{pending['id']}/accept"), headers=auth(contractor_token),
                        json={"client_session_id": session})
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["submission"]["status"] == "accepted" and body["submission"]["reviewed_by_name"]
    draft = body["draft"]
    assert draft["base_version_no"] == 2 and draft["revision"] == 0 and draft["is_mine"] is True
    assert {op["kind"] for op in draft["ops"]} == {"field.value"}
    assert {op["name"]: op["value"] for op in draft["ops"]} == GOOD
    # nothing changed yet: the review stands until the editor saves
    assert _contract(api, form_contract).review_status == "approved"
    assert get_document(api, contractor_token, form_contract)["draft"]["is_mine"] is True

    # the editor resumes the draft (same session) and saves it
    resumed = start_draft(api, contractor_token, form_contract, session, base_version_no=2)
    assert resumed.status_code == 200 and len(resumed.json()["ops"]) == len(GOOD)
    done = commit(api, contractor_token, form_contract, resumed.json()["revision"])
    assert done.status_code == 201, done.text
    assert done.json()["content_changed"] is True and done.json()["review_reset"] is True
    assert _contract(api, form_contract).review_status == "pending"
    values = _fields(api, contractor_token, form_contract, 3)
    assert values[("carrier", None)] == "ABC Transport LLC" and values[("pay", "Monthly")] == "Monthly"
    assert values[("agree", "Yes")] is True and values[("plan", None)] == "Plus"
    assert values[("notes", None)] == "Line one\nLine two"

    [(details, actor, _ip)] = audit_rows(api, "form_submission_accepted")
    assert actor is not None and details["fields"] == len(GOOD) and details["base_version_no"] == 2
    assert "ABC Transport" not in json.dumps(details)
    assert _pending(api, contractor_token, form_contract) == []
    again = api.post(submissions_url(form_contract, f"/{pending['id']}/accept"), headers=auth(contractor_token),
                     json={"client_session_id": session_id()})
    assert again.status_code == 409 and again.json()["error"] == "submission_not_pending"


def test_accept_refuses_while_another_draft_exists(api, contractor_token, form_contract, link, lan):
    assert submit(lan, link["token"], GOOD).status_code == 201
    [pending] = _pending(api, contractor_token, form_contract)
    other = start_draft(api, contractor_token, form_contract, session_id(), base_version_no=2)
    assert other.status_code == 201
    locked = api.post(submissions_url(form_contract, f"/{pending['id']}/accept"), headers=auth(contractor_token),
                      json={"client_session_id": session_id()})
    assert locked.status_code == 409 and locked.json()["error"] == "draft_locked"
    assert locked.json()["details"]["is_mine"] is True and locked.json()["details"]["lease_active"] is True
    assert _pending(api, contractor_token, form_contract)[0]["status"] == "received"


def test_accept_checks_the_values_against_the_current_version(api, contractor_token, form_contract, link, lan):
    assert submit(lan, link["token"], GOOD).status_code == 201
    [pending] = _pending(api, contractor_token, form_contract)
    removed = commit_ops(api, contractor_token, form_contract,
                         [{"id": "rm", "kind": "field.remove", "name": "plan"}], base_version_no=2)
    assert removed.status_code == 201, removed.text
    refused = api.post(submissions_url(form_contract, f"/{pending['id']}/accept"), headers=auth(contractor_token),
                       json={"client_session_id": session_id()})
    assert refused.status_code == 422 and refused.json()["error"] == "invalid_ops"
    assert refused.json()["details"]["fields"] == [{"name": "plan", "reason": "unknown_field"}]
    assert "Plus" not in refused.text
    assert _pending(api, contractor_token, form_contract)[0]["status"] == "received"


def test_reject(api, contractor_token, form_contract, link, lan):
    assert submit(lan, link["token"], GOOD).status_code == 201
    [pending] = _pending(api, contractor_token, form_contract)
    blank = api.post(submissions_url(form_contract, f"/{pending['id']}/reject"), headers=auth(contractor_token),
                     json={"reason": "   "})
    assert blank.status_code == 422
    rejected = api.post(submissions_url(form_contract, f"/{pending['id']}/reject"), headers=auth(contractor_token),
                        json={"reason": "Wrong carrier name, please resend"})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["reject_reason"] == "Wrong carrier name, please resend"
    [(details, _actor, _ip)] = audit_rows(api, "form_submission_rejected")
    assert "Wrong carrier" not in json.dumps(details) and details["reason_chars"] == 33
    again = api.post(submissions_url(form_contract, f"/{pending['id']}/accept"), headers=auth(contractor_token),
                     json={"client_session_id": session_id()})
    assert again.status_code == 409 and again.json()["error"] == "submission_not_pending"
    listed = api.get(submissions_url(form_contract, "?status=rejected"), headers=auth(contractor_token)).json()
    assert [s["id"] for s in listed] == [pending["id"]]
    # the document did not change
    assert get_document(api, contractor_token, form_contract)["current_version"]["version_no"] == 2


def test_authorization(api, admin_token, contractor_token, ready_template, filled_contract_bytes, lan):
    """Fill-only contractor (by e-mail) and a stranger: 403 on every internal
    route; a submission id of another contract: 404."""
    from tests.conftest import _register

    emailed = api.post("/api/contracts", headers=auth(admin_token),
                       files={"file": ("c.pdf", filled_contract_bytes, "application/pdf")},
                       data={"template_id": ready_template, "contractor_email": "driver@example.com"}
                       ).json()["contract_id"]
    with_form(api, admin_token, emailed)
    link = created(api, admin_token, emailed)
    assert submit(lan, link["token"], GOOD).status_code == 201
    [pending] = _pending(api, admin_token, emailed)
    stranger = _register(api, "stranger@example.com")
    for token in (contractor_token, stranger):
        assert api.get(submissions_url(emailed), headers=auth(token)).status_code == 403
        assert api.post(submissions_url(emailed, f"/{pending['id']}/accept"), headers=auth(token),
                        json={"client_session_id": session_id()}).status_code == 403
        assert api.post(submissions_url(emailed, f"/{pending['id']}/reject"), headers=auth(token),
                        json={"reason": "no"}).status_code == 403
    assert api.get(submissions_url(emailed)).status_code == 401

    other = api.post("/api/contracts", headers=auth(admin_token),
                     files={"file": ("c.pdf", filled_contract_bytes, "application/pdf")},
                     data={"template_id": ready_template}).json()["contract_id"]
    wrong = api.post(submissions_url(other, f"/{pending['id']}/reject"), headers=auth(admin_token),
                     json={"reason": "no"})
    assert wrong.status_code == 404
    assert api.post(submissions_url(emailed, f"/{pending['id']}/reject"), headers=auth(admin_token),
                    json={"reason": "no"}).status_code == 200
