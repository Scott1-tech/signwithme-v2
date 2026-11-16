"""PDF editor: a PDF saved from the standalone editor becomes the contract's next version."""
from __future__ import annotations

from uuid import uuid4

from tests.conftest import auth
from tests.editor_v2_helpers import doc_url, get_document, reportlab_pdf


def _pdf(pages: int, text: str) -> bytes:
    def draw(pdf, _index):
        pdf.setFont("Helvetica", 12)
        pdf.drawString(72, 700, text)

    return reportlab_pdf(draw, pages=pages)


def _page_count(api, token, contract_id: str) -> int:
    response = api.get(doc_url(contract_id, "/versions/1/pages"), headers=auth(token))
    assert response.status_code == 200, response.text
    return len(response.json())


def _upload(api, token, contract_id: str, data: bytes, *, base_version_no: int = 1,
            request_id: str | None = None, **extra):
    return api.post(
        doc_url(contract_id, "/upload"),
        headers=auth(token),
        files={"file": ("edited.pdf", data, "application/pdf")},
        data={
            "base_version_no": str(base_version_no),
            "commit_request_id": request_id or "upload_" + uuid4().hex[:16],
            **extra,
        },
    )


def test_an_edited_pdf_becomes_the_next_version(api, contractor_token, uploaded_contract):
    data = _pdf(_page_count(api, contractor_token, uploaded_contract), "Edited in the editor")
    response = _upload(api, contractor_token, uploaded_contract, data)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["version"]["version_no"] == 2 and body["version"]["kind"] == "edit"
    assert body["content_changed"] is True and body["reparse_started"] is True

    stored = api.get(doc_url(uploaded_contract, "/versions/2/file"), headers=auth(contractor_token))
    assert stored.content == data  # the stored file is the uploaded one, byte for byte
    assert get_document(api, contractor_token, uploaded_contract)["current_version"]["version_no"] == 2


def test_the_pages_keep_their_identity(api, contractor_token, uploaded_contract):
    data = _pdf(_page_count(api, contractor_token, uploaded_contract), "Same pages, new text")
    assert _upload(api, contractor_token, uploaded_contract, data).status_code == 201
    pages_1 = api.get(doc_url(uploaded_contract, "/versions/1/pages"), headers=auth(contractor_token)).json()
    pages_2 = api.get(doc_url(uploaded_contract, "/versions/2/pages"), headers=auth(contractor_token)).json()
    assert [page["page_uid"] for page in pages_1] == [page["page_uid"] for page in pages_2]


def test_an_upload_against_an_old_version_is_refused(api, contractor_token, uploaded_contract):
    pages = _page_count(api, contractor_token, uploaded_contract)
    assert _upload(api, contractor_token, uploaded_contract, _pdf(pages, "first")).status_code == 201
    stale = _upload(api, contractor_token, uploaded_contract, _pdf(pages, "second"), base_version_no=1)
    assert stale.status_code == 409 and stale.json()["error"] == "base_outdated", stale.text


def test_an_upload_must_keep_every_page(api, contractor_token, uploaded_contract):
    pages = _page_count(api, contractor_token, uploaded_contract)
    response = _upload(api, contractor_token, uploaded_contract, _pdf(pages + 1, "one page too many"))
    assert response.status_code == 422 and response.json()["error"] == "page_count_changed", response.text


def test_an_upload_must_be_a_pdf(api, contractor_token, uploaded_contract):
    response = _upload(api, contractor_token, uploaded_contract, b"this is not a pdf")
    assert response.status_code == 422, response.text


def test_the_same_request_id_makes_one_version(api, contractor_token, uploaded_contract):
    pages = _page_count(api, contractor_token, uploaded_contract)
    data = _pdf(pages, "saved once")
    first = _upload(api, contractor_token, uploaded_contract, data, request_id="upload_replay_0001")
    again = _upload(api, contractor_token, uploaded_contract, data, request_id="upload_replay_0001")
    assert first.status_code == 201 and again.status_code == 201, again.text
    assert again.json()["version"]["version_no"] == 2
    assert get_document(api, contractor_token, uploaded_contract)["versions_count"] == 2
