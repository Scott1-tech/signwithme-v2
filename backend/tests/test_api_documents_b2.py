"""Editor v2 B2 read endpoints: page images, page links, the whole-draft PDF."""
from __future__ import annotations

import io

from PIL import Image

from tests.conftest import _register, auth
from tests.editor_v2_helpers import commit, doc_url, first_page_uid, put_draft, session_id, start_draft


def test_images_links_and_draft_pdf(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    assert api.get(doc_url(uploaded_contract, f"/versions/1/pages/{uid}/images"),
                   headers=auth(contractor_token)).json() == []
    session = session_id()
    started = start_draft(api, contractor_token, uploaded_contract, session)
    new_uid = "pg_0000000abcde"
    saved = put_draft(api, contractor_token, uploaded_contract, session, started.json()["revision"], [
        {"id": "i", "kind": "page.insert", "new_page_uid": new_uid, "after_page_uid": uid},
        {"id": "l", "kind": "link.add", "page_uid": uid, "rect": {"x_pt": 72, "y_pt": 72, "w_pt": 80, "h_pt": 14},
         "target": {"type": "page", "page_uid": new_uid}},
        {"id": "r", "kind": "page.rotate", "page_uid": uid, "rotation": 180},
    ])
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]
    pdf = api.get(doc_url(uploaded_contract, f"/draft/preview.pdf?revision={revision}"), headers=auth(contractor_token))
    assert pdf.status_code == 200 and pdf.headers["x-page-uids"] == f"{uid},{new_uid}"
    from pypdf import PdfReader

    assert len(PdfReader(io.BytesIO(pdf.content)).pages) == 2
    stale = api.get(doc_url(uploaded_contract, f"/draft/preview.pdf?revision={revision - 1}"),
                    headers=auth(contractor_token))
    assert stale.status_code == 409
    other = _register(api, "nosy@example.com")
    assert api.get(doc_url(uploaded_contract, f"/draft/preview.pdf?revision={revision}"),
                   headers=auth(other)).status_code == 403
    done = commit(api, contractor_token, uploaded_contract, revision)
    assert done.status_code == 201, done.text
    links = api.get(doc_url(uploaded_contract, f"/versions/2/pages/{uid}/links"), headers=auth(contractor_token)).json()
    assert [(l["target"]["type"], l["target"]["page_uid"], l["target"]["position"]) for l in links] == [
        ("page", new_uid, 2)]
    assert links[0]["link_id"].startswith("lk")
    pages = api.get(doc_url(uploaded_contract, "/versions/2/pages"), headers=auth(contractor_token)).json()
    assert [(p["page_uid"], p["rotation"], p["origin_page"]) for p in pages] == [(uid, 180, 1), (new_uid, 0, None)]
    assert api.get(doc_url(uploaded_contract, "/versions/2/pages/pg_000000000999/links"),
                   headers=auth(contractor_token)).status_code == 404


def test_draft_with_an_unknown_link_or_image_is_refused_at_save(api, contractor_token, uploaded_contract):
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    session = session_id()
    started = start_draft(api, contractor_token, uploaded_contract, session)
    response = put_draft(api, contractor_token, uploaded_contract, session, started.json()["revision"], [
        {"id": "x", "kind": "link.remove", "page_uid": uid, "link_id": "lk123"},
        {"id": "y", "kind": "image.remove", "page_uid": uid, "xref": 5,
         "bbox": {"x_pt": 1, "y_pt": 1, "w_pt": 5, "h_pt": 5}},
    ])
    assert response.status_code == 422
    assert sorted((p["id"], p["reason"]) for p in response.json()["details"]["ops"]) == [
        ("x", "unknown_link"), ("y", "stale_target")]
