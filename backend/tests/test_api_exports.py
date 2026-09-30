"""Editor v2 B2: `POST/GET/DELETE /document/exports`, `/file`."""
from __future__ import annotations

import hashlib

import pytest

from tests.conftest import _register, auth
from tests.editor_v2_helpers import all_extractors, compact, doc_url, first_page_uid


def _export(api, token, contract, **body):
    return api.post(doc_url(contract, "/exports"), headers=auth(token), json=body)


def _markup(api, token, contract, elements):
    revision = api.get(doc_url(contract, "/markup"), headers=auth(token)).json()["revision"]
    response = api.put(doc_url(contract, "/markup"), headers=auth(token),
                       json={"revision": revision, "elements": elements})
    assert response.status_code == 200, response.text


def test_export_without_markup_is_the_version_file(api, contractor_token, uploaded_contract):
    response = _export(api, contractor_token, uploaded_contract)
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "done" and job["version_no"] == 1 and job["include_markup"] is True
    version = api.get(doc_url(uploaded_contract, "/versions/1/file"), headers=auth(contractor_token)).content
    data = api.get(doc_url(uploaded_contract, f"/exports/{job['id']}/file"), headers=auth(contractor_token))
    assert data.status_code == 200 and data.content == version
    assert data.headers["x-content-sha256"] == hashlib.sha256(version).hexdigest() == job["result_sha256"]
    assert "attachment" in data.headers["content-disposition"]


def test_export_with_markup_draws_all_of_it_and_is_reused(api, contractor_token, admin_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    _markup(api, contractor_token, uploaded_contract, [
        {"id": "tb", "kind": "textbox", "page_uid": uid, "x_pt": 72, "y_pt": 30, "w_pt": 300, "h_pt": 30,
         "runs": [{"text": "EXPORTED NOTE", "size_pt": 12}]},
        {"id": "tx", "kind": "text", "page_uid": uid, "x_pt": 72, "y_pt": 90, "text": "LEGACY MARK"},
    ])
    first = _export(api, contractor_token, uploaded_contract).json()
    text = compact(all_extractors(api.get(doc_url(uploaded_contract, f"/exports/{first['id']}/file"),
                                          headers=auth(contractor_token)).content)[0])
    assert "EXPORTEDNOTE" in text and "LEGACYMARK" in text
    # Same version, same options, same markup revision -> the same job.
    again = _export(api, contractor_token, uploaded_contract).json()
    assert again["id"] == first["id"]
    # Without markup: another job, the plain version.
    bare = _export(api, contractor_token, uploaded_contract, include_markup=False).json()
    assert bare["id"] != first["id"]
    plain = api.get(doc_url(uploaded_contract, f"/exports/{bare['id']}/file"), headers=auth(contractor_token)).content
    assert "EXPORTEDNOTE" not in compact(all_extractors(plain)[0])
    # A markup change -> a new export.
    _markup(api, contractor_token, uploaded_contract, [])
    assert _export(api, contractor_token, uploaded_contract).json()["id"] != first["id"]
    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    exported = [e for e in trail if e["action"] == "document_exported"]
    assert exported and "EXPORTED" not in str(exported)


def test_cancel_discards_the_file(api, contractor_token, uploaded_contract):
    job = _export(api, contractor_token, uploaded_contract).json()
    assert api.delete(doc_url(uploaded_contract, f"/exports/{job['id']}"), headers=auth(contractor_token)).status_code == 204
    status = api.get(doc_url(uploaded_contract, f"/exports/{job['id']}"), headers=auth(contractor_token)).json()
    assert status["status"] == "cancelled"
    file = api.get(doc_url(uploaded_contract, f"/exports/{job['id']}/file"), headers=auth(contractor_token))
    assert file.status_code == 409 and file.json()["error"] == "export_not_ready"
    assert not any(key.endswith(f"export-{job['id']}.pdf") for key in api.objects)
    # A cancelled job is not reused.
    assert _export(api, contractor_token, uploaded_contract).json()["id"] != job["id"]


def test_exports_are_scoped_to_their_contract(api, contractor_token, uploaded_contract, admin_token,
                                              ready_template):
    job = _export(api, contractor_token, uploaded_contract).json()
    other = _register(api, "eve@example.com")
    assert _export(api, other, uploaded_contract).status_code == 403
    assert api.get(doc_url(uploaded_contract, f"/exports/{job['id']}"), headers=auth(other)).status_code == 403
    # A job id under another contract (the admin's) is a 404.
    from tests.conftest import _make_pdf

    second = api.post("/api/contracts", headers=auth(admin_token),
                      files={"file": ("c.pdf", _make_pdf(filled=True), "application/pdf")},
                      data={"template_id": ready_template}).json()["contract_id"]
    api.get(doc_url(second), headers=auth(admin_token))
    assert api.get(doc_url(second, f"/exports/{job['id']}"), headers=auth(admin_token)).status_code == 404
    assert api.get(doc_url(uploaded_contract, "/exports/zz"), headers=auth(admin_token)).status_code == 422


@pytest.mark.parametrize("body", [{"include_markup": "yes"}, {"version_no": 0}, {"format": "png"}])
def test_bad_export_bodies(api, contractor_token, uploaded_contract, body):
    assert _export(api, contractor_token, uploaded_contract, **body).status_code == 422


def test_unknown_version_is_404(api, contractor_token, uploaded_contract):
    response = _export(api, contractor_token, uploaded_contract, version_no=7)
    assert response.status_code == 404 and response.json()["error"] == "version_not_found"
