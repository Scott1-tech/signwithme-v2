"""Speed fixes: what a page open costs the server.

* a stored file is answered in one piece (with its length), not as thousands
  of tiny chunks -- an 8 MB PDF used to take seconds of pure CPU;
* `GET /document` is asked on every page open and must not download the PDF
  again once its open-warnings are known;
* one signature applied to every slot is many rows with one image: every row
  names that image's hash, so the client downloads it once.
"""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from tests.conftest import auth


@pytest.fixture
def signature_data_url() -> str:
    image = Image.new("RGBA", (240, 60), (0, 0, 0, 0))
    for x in range(10, 230):
        for y in range(28, 34):
            image.putpixel((x, y), (0, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def test_the_contract_file_is_sent_in_one_piece(api, contractor_token, uploaded_contract):
    response = api.get(f"/api/contracts/{uploaded_contract}/file", headers=auth(contractor_token))
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert int(response.headers["content-length"]) == len(response.content)
    assert response.headers["cache-control"] == "no-store"


def test_document_state_does_not_download_the_pdf_again(
    api, contractor_token, uploaded_contract, monkeypatch
):
    from app.services import storage as storage_module

    url = f"/api/contracts/{uploaded_contract}/document"
    assert api.get(url, headers=auth(contractor_token)).status_code == 200

    downloads: list[str] = []
    original = storage_module.storage.get_bytes

    def counting(key):
        downloads.append(key)
        return original(key)

    monkeypatch.setattr(storage_module.storage, "get_bytes", counting)
    again = api.get(url, headers=auth(contractor_token))
    assert again.status_code == 200
    assert downloads == []


def test_signature_rows_name_their_image(api, contractor_token, uploaded_contract, signature_data_url):
    fields = api.get(
        f"/api/contracts/{uploaded_contract}/compare", headers=auth(contractor_token)
    ).json()["signature_slots"]
    slot = next(item for item in fields if item["kind"] == "signature")
    placed = api.post(
        f"/api/contracts/{uploaded_contract}/signatures/bulk",
        headers=auth(contractor_token),
        json={"image_data": signature_data_url, "field_ids": [slot["field_id"]]},
    )
    assert placed.status_code == 200, placed.text
    row = placed.json()["placed"][0]
    assert len(row["signature_hash"]) == 64
    listed = api.get(
        f"/api/contracts/{uploaded_contract}/signatures", headers=auth(contractor_token)
    ).json()
    assert listed[0]["signature_hash"] == row["signature_hash"]
