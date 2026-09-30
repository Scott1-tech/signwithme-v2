"""Editor v2 B2: the image/stamp gallery (`/api/assets`) and gallery -> contract copies."""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from tests.conftest import _register, auth
from tests.test_api_annotations import _ready, signature_data_url  # noqa: F401 - fixture


def _png(color=(0, 128, 255, 255), size=(30, 20), extra: bytes = b"") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue() + extra


def _data_url(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode()


def _add(api, token, **body):
    return api.post("/api/assets", headers=auth(token), json={"image_data": _data_url(_png()), **body})


def test_add_list_get_delete_own_assets(api, contractor_token):
    added = _add(api, contractor_token, kind="stamp", label="Company seal")
    assert added.status_code == 201, added.text
    asset = added.json()
    assert (asset["kind"], asset["label"], asset["width_px"], asset["height_px"]) == ("stamp", "Company seal", 30, 20)
    listed = api.get("/api/assets", headers=auth(contractor_token)).json()
    assert [a["asset_id"] for a in listed] == [asset["asset_id"]]
    image = api.get(f"/api/assets/{asset['asset_id']}/image", headers=auth(contractor_token))
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "no-store"
    assert api.delete(f"/api/assets/{asset['asset_id']}", headers=auth(contractor_token)).status_code == 204
    assert api.get("/api/assets", headers=auth(contractor_token)).json() == []
    assert api.get(f"/api/assets/{asset['asset_id']}/image", headers=auth(contractor_token)).status_code == 404
    # Soft: the row stays for the audit trail, the bytes are gone.
    from uuid import UUID

    from app.models.asset import Asset

    db = api.db_factory()
    try:
        row = db.get(Asset, UUID(hex=asset["asset_id"]))
        assert row is not None and row.deleted_at is not None
        assert row.storage_key not in api.objects
    finally:
        db.close()


def test_multipart_upload_is_re_encoded(api, contractor_token):
    polyglot = _png(extra=b"<script>alert(1)</script>")
    response = api.post("/api/assets", headers=auth(contractor_token),
                        files={"file": ("x.png", polyglot, "image/png")}, data={"kind": "image"})
    assert response.status_code == 201, response.text
    stored = api.get(f"/api/assets/{response.json()['asset_id']}/image", headers=auth(contractor_token)).content
    assert b"<script>" not in stored and stored.startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "body, status",
    [
        ({"image_data": "data:image/svg+xml;base64,PHN2Zz4="}, 422),
        ({"image_data": _data_url(b"GIF89a....")}, 422),
        ({"image_data": _data_url(_png()), "kind": "logo"}, 422),
        ({"image_data": _data_url(_png()), "unknown": 1}, 422),
        ({"image_data": "A" * (3 * 1024 * 1024)}, 413),
    ],
)
def test_bad_uploads_are_refused(api, contractor_token, body, status):
    assert api.post("/api/assets", headers=auth(contractor_token), json=body).status_code == status


def test_idor_other_users_assets_are_404_everywhere(api, contractor_token, uploaded_contract, signature_data_url):
    mine = _add(api, contractor_token).json()["asset_id"]
    other = _register(api, "mallory@example.com")
    assert api.get("/api/assets", headers=auth(other)).json() == []
    assert api.get(f"/api/assets/{mine}/image", headers=auth(other)).status_code == 404
    assert api.delete(f"/api/assets/{mine}", headers=auth(other)).status_code == 404
    assert api.get(f"/api/assets/{'0' * 32}/image", headers=auth(other)).status_code == 404
    assert api.get("/api/assets/not-hex/image", headers=auth(other)).status_code == 422
    # Mallory cannot copy it onto a contract she can see either -- nor can she
    # see the contractor's contract at all.
    assert api.post(f"/api/contracts/{uploaded_contract}/annotation-assets", headers=auth(other),
                    json={"from_asset_id": mine}).status_code == 403
    # Still there for its owner.
    assert api.get(f"/api/assets/{mine}/image", headers=auth(contractor_token)).status_code == 200
    assert api.get("/api/assets", headers=auth(contractor_token)).json()[0]["asset_id"] == mine


def test_gallery_copy_onto_a_contract_survives_gallery_deletion(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    mine = _add(api, contractor_token, kind="stamp").json()["asset_id"]
    copy = api.post(f"/api/contracts/{uploaded_contract}/annotation-assets", headers=auth(contractor_token),
                    json={"from_asset_id": mine})
    assert copy.status_code == 201, copy.text
    placed_id = copy.json()["asset_id"]
    assert placed_id != mine
    assert api.delete(f"/api/assets/{mine}", headers=auth(contractor_token)).status_code == 204
    # The placed copy still resolves and draws.
    image = api.get(f"/api/contracts/{uploaded_contract}/annotation-assets/{placed_id}", headers=auth(contractor_token))
    assert image.status_code == 200
    final = api.post(f"/api/contracts/{uploaded_contract}/finalize", headers=auth(contractor_token), json={
        "contract_date": "2026-09-29",
        "annotations": [{"id": "i", "kind": "image", "page": 1, "asset_id": placed_id,
                         "x_pt": 100, "y_pt": 100, "w_pt": 60, "h_pt": 40}],
    })
    assert final.status_code == 200, final.text
    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    added = [e for e in trail if e["action"] == "annotation_asset_added"]
    assert added[-1]["details"]["from_asset_id"] == mine


def test_from_asset_id_and_image_data_together_or_neither_is_422(api, contractor_token, uploaded_contract,
                                                                  signature_data_url):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    url = f"/api/contracts/{uploaded_contract}/annotation-assets"
    assert api.post(url, headers=auth(contractor_token), json={}).status_code == 422
    assert api.post(url, headers=auth(contractor_token),
                    json={"image_data": _data_url(_png()), "from_asset_id": "a" * 32}).status_code == 422
    assert api.post(url, headers=auth(contractor_token), json={"from_asset_id": "a" * 32}).status_code == 404


def test_gallery_audit_carries_no_image(api, contractor_token, admin_token):
    _add(api, contractor_token, label="secret label")
    from app.models.audit import AuditLog

    db = api.db_factory()
    try:
        rows = [r for r in db.query(AuditLog).all() if r.action == "asset_added"]
    finally:
        db.close()
    assert len(rows) == 1
    assert set(rows[0].details) == {"asset_id", "scope", "kind", "sha256", "width_px", "height_px"}
