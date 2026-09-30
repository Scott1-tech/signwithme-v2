"""Editor v2 B3: `POST .../document/detect-fields` -- detector suggestions in
field geometry, nothing stored, existing widgets left out, access control."""
from __future__ import annotations

import re

import pytest

from app.schemas.document import FIELD_NAME_PATTERN
from tests.conftest import _register, auth
from tests.editor_v2_helpers import commit, doc_url, first_page_uid, put_draft, session_id, start_draft


def _detect(api, token, contract_id, body=None):
    return api.post(doc_url(contract_id, "/detect-fields"), headers=auth(token), json=body or {})


def test_suggestions_for_the_contract_page(api, contractor_token, uploaded_contract):
    response = _detect(api, contractor_token, uploaded_contract)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version_no"] == 1
    suggestions = body["suggestions"]
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    by_label = {s["label"]: s for s in suggestions}
    assert {"Full Name", "CDL", "Exp", "Email", "SSN", "Contractor Signature", "Date"} <= set(by_label)
    assert by_label["Contractor Signature"]["type"] == "signature"
    assert by_label["Full Name"]["type"] == "text"
    assert sum(1 for s in suggestions if s["type"] == "checkbox") == 3  # the W-9 squares
    for s in suggestions:
        assert s["page_uid"] == uid
        assert re.fullmatch(FIELD_NAME_PATTERN, s["name"])
        assert re.fullmatch(r"sg_[0-9a-f]{12}", s["suggestion_id"])
        assert 0 < s["confidence"] <= 1
        rect = s["rect"]
        assert rect["x_pt"] >= 0 and rect["y_pt"] >= 0 and rect["x_pt"] + rect["w_pt"] <= 612.01
    assert len({s["name"] for s in suggestions}) == len(suggestions)
    # the "Email" fill region sits on its underscores (x 200 in reportlab
    # space, baseline y 620 -> top-left baseline 172)
    email = by_label["Email"]["rect"]
    assert email["x_pt"] == pytest.approx(200, abs=0.5) and 150 <= email["y_pt"] <= 172 <= email["y_pt"] + 20
    # stable ids, nothing stored
    again = _detect(api, contractor_token, uploaded_contract).json()["suggestions"]
    assert [s["suggestion_id"] for s in again] == [s["suggestion_id"] for s in suggestions]
    versions = api.get(doc_url(uploaded_contract, "/versions"), headers=auth(contractor_token)).json()
    assert len(versions) == 1
    assert api.get(doc_url(uploaded_contract, "/draft"), headers=auth(contractor_token)).status_code == 404


def test_accepted_suggestion_becomes_a_field_and_is_not_suggested_again(api, contractor_token, uploaded_contract):
    suggestions = _detect(api, contractor_token, uploaded_contract).json()["suggestions"]
    chosen = next(s for s in suggestions if s["label"] == "Full Name")
    session = session_id()
    started = start_draft(api, contractor_token, uploaded_contract, session)
    saved = put_draft(api, contractor_token, uploaded_contract, session, started.json()["revision"], [
        {"id": "acc1", "kind": "field.add", "page_uid": chosen["page_uid"],
         "field": {"name": chosen["name"], "type": chosen["type"], "rect": chosen["rect"], "tab_index": 0}}])
    assert saved.status_code == 200, saved.text
    assert commit(api, contractor_token, uploaded_contract, saved.json()["revision"]).status_code == 201
    after = _detect(api, contractor_token, uploaded_contract).json()
    assert after["version_no"] == 2
    assert chosen["suggestion_id"] not in {s["suggestion_id"] for s in after["suggestions"]}
    assert "Full Name" not in {s["label"] for s in after["suggestions"]}
    # a suggested name never collides with an existing field
    assert chosen["name"] not in {s["name"] for s in after["suggestions"]}


def test_page_filter_and_unknown_page(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    assert _detect(api, contractor_token, uploaded_contract, {"page_uids": [uid]}).status_code == 200
    unknown = _detect(api, contractor_token, uploaded_contract, {"page_uids": ["pg_0000000000ff"]})
    assert unknown.status_code == 422 and unknown.json()["error"] == "unknown_page"
    assert _detect(api, contractor_token, uploaded_contract, {"page_uids": ["bad"]}).status_code == 422
    assert _detect(api, contractor_token, uploaded_contract, {"extra": 1}).status_code == 422


def test_access_control(api, admin_token, contractor_token, ready_template, filled_contract_bytes, uploaded_contract):
    stranger = _register(api, "stranger@example.com")
    assert _detect(api, stranger, uploaded_contract).status_code == 403
    assert api.post(doc_url(uploaded_contract, "/detect-fields"), json={}).status_code == 401
    emailed = api.post(
        "/api/contracts",
        headers=auth(admin_token),
        files={"file": ("contract.pdf", filled_contract_bytes, "application/pdf")},
        data={"template_id": ready_template, "contractor_email": "driver@example.com"},
    ).json()["contract_id"]
    # the contractor-by-email fills forms but does not lay them out
    assert _detect(api, contractor_token, emailed).status_code == 403
    assert _detect(api, admin_token, emailed).status_code == 200


def test_cropbox_offset_is_removed():
    """pdfplumber boxes are in the DISPLAYED MediaBox; suggestions are in field
    geometry (top-left of the CropBox)."""
    import pymupdf  # test-only: build the page

    from app.services import form_suggestions

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.set_cropbox(pymupdf.Rect(20, 30, 500, 700))
    page.insert_text((100, 200), "Full Name: ____________________", fontsize=12)
    data = doc.tobytes()
    underscores = [ch for b in pymupdf.open(stream=data)[0].get_text("rawdict")["blocks"] for l in b["lines"]
                   for s in l["spans"] for ch in s["chars"] if ch["c"] == "_"]
    suggestions = form_suggestions.suggest(data, [(1, "pg_000000000001")])
    rect = next(s["rect"] for s in suggestions if s["label"] == "Full Name")
    assert rect["x_pt"] == pytest.approx(underscores[0]["bbox"][0], abs=0.5)
    assert rect["x_pt"] + rect["w_pt"] == pytest.approx(underscores[-1]["bbox"][2], abs=0.5)
