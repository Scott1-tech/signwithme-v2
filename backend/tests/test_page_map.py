"""Editor v2 B2: the page map end to end (EDITOR-V2-PLAN §5.4-5.6).

A 3-page package whose every page carries the conftest form (a signature
slot + a date on each page). Page ops go through the real draft/commit API;
what is checked is what the rest of the system then believes:
`document_pages`, the re-parse (`extract(page_map)`, `template_match`),
the comparison (`page_removed_with_slots`, slots only on present pages),
`/compare` (`page` = position, `page_uid`), `/date-fields`, signature
placement (`page_removed`), finalize (dates at the mapped position), and the
slot-page deletion rule (reviewer/admin only).
"""
from __future__ import annotations

import io

import pytest

from tests.conftest import _make_pdf, _promote, _register, auth
from tests.editor_v2_helpers import commit, doc_url, put_draft, session_id, start_draft
from tests.test_api_annotations import signature_data_url  # noqa: F401 - fixture

PAGES = 3


@pytest.fixture
def package(api, admin_token):
    """(template_id, contract_id, uploader token) for a 3-page package."""
    template = api.post(
        "/api/templates", headers=auth(admin_token),
        files={"file": ("t.pdf", _make_pdf(filled=False, pages=PAGES), "application/pdf")},
        data={"name": "Package"},
    ).json()["template_id"]
    token = _register(api, "uploader@example.com")
    contract = api.post(
        "/api/contracts", headers=auth(token),
        files={"file": ("c.pdf", _make_pdf(filled=True, pages=PAGES), "application/pdf")},
        data={"template_id": template, "contractor_name": "Charles Frye"},
    ).json()["contract_id"]
    return template, contract, token


def _pages(api, token, contract, version_no=None):
    if version_no is None:
        version_no = api.get(doc_url(contract), headers=auth(token)).json()["current_version"]["version_no"]
    return api.get(doc_url(contract, f"/versions/{version_no}/pages"), headers=auth(token)).json()


def _commit_ops(api, token, contract, ops, **extra):
    version = api.get(doc_url(contract), headers=auth(token)).json()["current_version"]["version_no"]
    session = session_id()
    started = start_draft(api, token, contract, session, base_version_no=version)
    assert started.status_code in (200, 201), started.text
    saved = put_draft(api, token, contract, session, started.json()["revision"], ops)
    if saved.status_code != 200:
        return saved
    return commit(api, token, contract, saved.json()["revision"], **extra)


def _slots(api, token, contract):
    response = api.get(f"/api/contracts/{contract}/compare", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


def _reviewer(api) -> str:
    _register(api, "rev@example.com")
    _promote(api, "rev@example.com", "reviewer")
    return api.post("/api/auth/login", json={"email": "rev@example.com", "password": "password123"}).json()[
        "access_token"
    ]


def test_version_pages_after_insert_delete_move(api, package, admin_token):
    _template, contract, token = package
    before = _pages(api, token, contract)
    uids = [p["page_uid"] for p in before]
    assert [p["origin_page"] for p in before] == [1, 2, 3]
    new_uid = "pg_00000000abcd"
    response = _commit_ops(api, admin_token, contract, [
        {"id": "i", "kind": "page.insert", "new_page_uid": new_uid, "after_page_uid": uids[0]},
        {"id": "d", "kind": "page.delete", "page_uid": uids[1]},
        {"id": "m", "kind": "page.move", "page_uid": uids[2], "after_page_uid": None},
    ])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["content_changed"] is True and body["version"]["page_count"] == 3
    after = _pages(api, admin_token, contract)
    assert [(p["page_uid"], p["origin_page"], p["position"]) for p in after] == [
        (uids[2], 3, 1), (uids[0], 1, 2), (new_uid, None, 3),
    ]
    # The file itself carries the ids.
    from pypdf import PdfReader

    data = api.get(doc_url(contract, "/versions/2/file"), headers=auth(admin_token)).content
    assert [str(p["/SWMPageId"]) for p in PdfReader(io.BytesIO(data)).pages] == [uids[2], uids[0], new_uid]


def test_reparse_reads_fields_through_the_map_and_flags_the_removed_slot_page(api, package, admin_token):
    _template, contract, token = package
    uids = [p["page_uid"] for p in _pages(api, token, contract)]
    before = _slots(api, token, contract)
    # All three pages are filled in the contract, only the slots are empty.
    assert {s["page"] for s in before["signature_slots"]} == {1, 2, 3}
    response = _commit_ops(api, admin_token, contract, [
        {"id": "d", "kind": "page.delete", "page_uid": uids[1]},
        {"id": "m", "kind": "page.move", "page_uid": uids[2], "after_page_uid": None},
    ])
    assert response.status_code == 201, response.text
    comparison = _slots(api, admin_token, contract)
    removed = [i for i in comparison["inconsistencies"] if i["type"] == "page_removed_with_slots"]
    assert [(i["pages"], i["severity"]) for i in removed] == [([2], "critical")]
    assert comparison["can_sign"] is False
    # Slots: template pages 1 and 3 only, at their NEW positions, with the page ids.
    by_page = {(s["page"], s["page_uid"]) for s in comparison["signature_slots"]}
    assert by_page == {(2, uids[0]), (1, uids[2])}
    # Page 3's fields were read from position 1 (not missing).
    missing_pages = {m["page"] for m in comparison["missing_required"]}
    assert 3 not in missing_pages and 1 not in missing_pages
    # template_match compares the pages in TEMPLATE order: a removed page is empty.
    from app.models.contract import Contract

    db = api.db_factory()
    try:
        stored = db.get(Contract, __import__("uuid").UUID(contract)).comparison_result
    finally:
        db.close()
    assert stored["template_match"]["page_similarity"] == 0.0


def test_date_fields_and_signature_slots_follow_positions(api, package, admin_token):
    _template, contract, token = package
    uids = [p["page_uid"] for p in _pages(api, token, contract)]
    _commit_ops(api, admin_token, contract, [
        {"id": "d", "kind": "page.delete", "page_uid": uids[0]},
    ])
    dates = api.get(f"/api/contracts/{contract}/date-fields", headers=auth(admin_token)).json()
    assert {(d["page"], d["page_uid"]) for d in dates} == {(1, uids[1]), (2, uids[2])}


def _approve(api, token, contract):
    response = api.post(
        f"/api/review/{contract}", headers=auth(token),
        json={"decision": "approved", "notes": "page removed on purpose", "override_missing": True},
    )
    return response


def test_signature_on_a_removed_page_is_page_removed(api, package, admin_token, signature_data_url):
    _template, contract, token = package
    uids = [p["page_uid"] for p in _pages(api, token, contract)]
    _commit_ops(api, admin_token, contract, [{"id": "d", "kind": "page.delete", "page_uid": uids[1]}])
    approved = _approve(api, admin_token, contract)
    assert approved.status_code == 200, approved.text
    slots = _slots(api, admin_token, contract)["signature_slots"]
    template_page_2 = next(fid for fid in _slot_ids(api, admin_token, contract) if fid.startswith("p2_"))
    response = api.post(f"/api/contracts/{contract}/signatures", headers=auth(admin_token),
                        json={"field_id": template_page_2, "image_data": signature_data_url})
    assert response.status_code == 422
    assert response.json()["details"]["reason"] == "page_removed"
    # A slot of template page 3 lands at position 2 with its page id.
    page_3_slot = next(s for s in slots if s["page_uid"] == uids[2] and s["kind"] == "signature")
    placed = api.post(f"/api/contracts/{contract}/signatures", headers=auth(admin_token),
                      json={"field_id": page_3_slot["field_id"], "image_data": signature_data_url})
    assert placed.status_code == 201, placed.text
    assert placed.json()["page_number"] == 2


def _slot_ids(api, token, contract) -> list[str]:
    from app.models.contract import Contract
    from app.models.template import Template

    db = api.db_factory()
    try:
        c = db.get(Contract, __import__("uuid").UUID(contract))
        schema = db.get(Template, c.template_id).field_schema
    finally:
        db.close()
    return [f["field_id"] for page in schema["pages"] for f in page["fields"] if f["field_type"] == "signature"]


def test_uploader_cannot_delete_a_slot_page_reviewer_can(api, package):
    _template, contract, token = package
    uids = [p["page_uid"] for p in _pages(api, token, contract)]
    session = session_id()
    started = start_draft(api, token, contract, session)
    assert started.status_code == 201, started.text
    refused = put_draft(api, token, contract, session, started.json()["revision"],
                        [{"id": "d", "kind": "page.delete", "page_uid": uids[1]}])
    assert refused.status_code == 403 and refused.json()["error"] == "page_has_slots"
    assert refused.json()["details"] == {"pages": [2]}
    # Inserting / moving is fine for the uploader.
    ok = put_draft(api, token, contract, session, started.json()["revision"],
                   [{"id": "m", "kind": "page.move", "page_uid": uids[2], "after_page_uid": None}])
    assert ok.status_code == 200, ok.text
    api.delete(doc_url(contract, f"/draft?revision={ok.json()['revision']}"), headers=auth(token))
    reviewer = _reviewer(api)
    response = _commit_ops(api, reviewer, contract, [{"id": "d", "kind": "page.delete", "page_uid": uids[1]}])
    assert response.status_code == 201, response.text


def test_finalize_prints_dates_at_the_mapped_position(api, package, admin_token, signature_data_url):
    """Page 1 deleted: template page 2's date lands on position 1."""
    _template, contract, token = package
    uids = [p["page_uid"] for p in _pages(api, token, contract)]
    _commit_ops(api, admin_token, contract, [{"id": "d", "kind": "page.delete", "page_uid": uids[0]}])
    assert _approve(api, admin_token, contract).status_code == 200
    slots = _slots(api, admin_token, contract)["signature_slots"]
    signature_ids = [s["field_id"] for s in slots if s["kind"] == "signature"]
    bulk = api.post(f"/api/contracts/{contract}/signatures/bulk", headers=auth(admin_token),
                    json={"field_ids": signature_ids, "image_data": signature_data_url})
    assert bulk.status_code == 200 and not bulk.json()["failed"], bulk.text
    final = api.post(f"/api/contracts/{contract}/finalize", headers=auth(admin_token),
                     json={"contract_date": "2026-09-29"})
    assert final.status_code == 200, final.text
    pdf = api.get(f"/api/download/{contract}/final", headers=auth(admin_token))
    from pypdf import PdfReader

    data = pdf.content if pdf.headers.get("content-type", "").startswith("application/pdf") else \
        api.objects[f"final/{contract}/signed.pdf"]
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 2
    for page in reader.pages:
        assert "09/29/2026" in (page.extract_text() or "")


def test_structure_commit_keeps_signatures_and_rotates_their_boxes(api, package, admin_token, signature_data_url):
    _template, contract, token = package
    assert _approve(api, admin_token, contract).status_code == 200
    slots = _slots(api, admin_token, contract)["signature_slots"]
    slot = next(s for s in slots if s["kind"] == "signature" and s["page"] == 1)
    placed = api.post(f"/api/contracts/{contract}/signatures", headers=auth(admin_token),
                      json={"field_id": slot["field_id"], "image_data": signature_data_url})
    assert placed.status_code == 201, placed.text
    before = placed.json()["bbox"]
    uids = [p["page_uid"] for p in _pages(api, admin_token, contract)]
    response = _commit_ops(api, admin_token, contract,
                           [{"id": "r", "kind": "page.rotate", "page_uid": uids[0], "rotation": 90}])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["content_changed"] is False and body["review_reset"] is False
    assert body["remapped"] == {"signatures_kept": 1, "signatures_removed": 0}
    assert {"code": "signatures_rotated_with_page", "count": 1} in body["warnings"]
    rows = api.get(f"/api/contracts/{contract}/signatures", headers=auth(admin_token)).json()
    after = rows[0]["bbox"]
    # 90 deg on a 612 x 792 page: (x, y) -> (792 - y - h, x), w <-> h.
    assert after["x"] == pytest.approx(792 - before["y"] - before["height"])
    assert after["y"] == pytest.approx(before["x"])
    assert (after["width"], after["height"]) == pytest.approx((before["height"], before["width"]))
    detail = api.get(f"/api/contracts/{contract}", headers=auth(admin_token)).json()
    assert detail["review_status"] == "approved"
