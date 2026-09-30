"""Editor v2 B2: typed signatures (`POST /api/signatures {typed}`)."""
from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from tests.conftest import auth


def _typed(api, token, text="Charles Frye", font="GreatVibes", **extra):
    return api.post("/api/signatures", headers=auth(token), json={"typed": {"text": text, "font": font}, **extra})


@pytest.mark.parametrize("font", ["GreatVibes", "Allura", "Sacramento"])
def test_typed_signature_is_rendered_normalised_and_stored_as_typed(api, contractor_token, font):
    response = _typed(api, contractor_token, font=font)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source"] == "typed"
    png = api.get(f"/api/signatures/{body['id']}/image", headers=auth(contractor_token)).content
    with Image.open(io.BytesIO(png)) as image:
        assert image.mode == "RGBA"
        alpha = image.getchannel("A")
        # Cropped to the ink (the signature chain): ink touches every edge band.
        bbox = alpha.point(lambda v: 255 if v >= 8 else 0).getbbox()
        assert bbox is not None
        assert bbox[0] <= 2 and bbox[1] <= 2 and image.size[0] - bbox[2] <= 2
        assert image.size[0] > image.size[1] > 20


def test_the_typed_text_is_stored_nowhere(api, contractor_token, admin_token):
    secret = "Zyxwv Qutsr"
    response = _typed(api, contractor_token, text=secret)
    assert response.status_code == 201
    from app.models.audit import AuditLog
    from app.models.user_signature import UserSignature

    db = api.db_factory()
    try:
        rows = db.query(UserSignature).all()
        audit = [(r.action, r.details) for r in db.query(AuditLog).all()]
    finally:
        db.close()
    dumped = json.dumps([{c.name: str(getattr(r, c.name)) for c in r.__table__.columns} for r in rows]) + json.dumps(
        audit, default=str)
    assert secret not in dumped and "Zyxwv" not in dumped
    assert ("user_signature_saved", {"signature_id": str(rows[0].id), "signature_hash": rows[0].signature_hash,
                                     "source": "typed"}) in audit


def test_typed_and_drawn_are_the_same_chain_for_signing(api, contractor_token, uploaded_contract):
    """The library PNG of a typed signature can be placed like any other."""
    import base64

    from tests.test_api_signing import _force_signable

    _force_signable(api, uploaded_contract)
    saved = _typed(api, contractor_token).json()
    png = api.get(f"/api/signatures/{saved['id']}/image", headers=auth(contractor_token)).content
    placed = api.post(f"/api/contracts/{uploaded_contract}/signatures", headers=auth(contractor_token),
                      json={"field_id": "p1_contractor_signature",
                            "image_data": "data:image/png;base64," + base64.b64encode(png).decode()})
    assert placed.status_code == 201, placed.text


@pytest.mark.parametrize(
    "typed",
    [
        {"text": "", "font": "GreatVibes"},
        {"text": "   ", "font": "GreatVibes"},
        {"text": "x" * 65, "font": "GreatVibes"},
        {"text": "Name", "font": "Arial"},
        {"text": "Name 😀", "font": "GreatVibes"},
        {"text": "Имя", "font": "Sacramento"},
        {"text": "a‮b", "font": "GreatVibes"},
        {"text": "Name", "font": "GreatVibes", "size": 20},
    ],
)
def test_typed_refusals(api, contractor_token, typed):
    response = api.post("/api/signatures", headers=auth(contractor_token), json={"typed": typed})
    assert response.status_code == 422
    assert "Name" not in json.dumps(response.json()) or typed.get("text") != "Name 😀"
    assert all("input" not in entry for entry in response.json()["detail"])


def test_exactly_one_of_image_or_typed(api, contractor_token):
    assert api.post("/api/signatures", headers=auth(contractor_token), json={}).status_code == 422
    both = api.post("/api/signatures", headers=auth(contractor_token),
                    json={"image_data": "data:image/png;base64,AAAA", "typed": {"text": "A", "font": "Allura"}})
    assert both.status_code == 422
    source_on_typed = api.post("/api/signatures", headers=auth(contractor_token),
                               json={"typed": {"text": "A", "font": "Allura"}, "source": "drawn"})
    assert source_on_typed.status_code == 422


def test_cyrillic_typed_in_great_vibes(api, contractor_token):
    assert _typed(api, contractor_token, text="Иван Oʻzbek", font="GreatVibes").status_code == 201


def test_drawn_signature_can_say_drawn(api, contractor_token):
    import base64

    buffer = io.BytesIO()
    image = Image.new("RGBA", (120, 40), (0, 0, 0, 0))
    for x in range(10, 110):
        image.putpixel((x, 20), (0, 0, 0, 255))
        image.putpixel((x, 21), (0, 0, 0, 255))
    image.save(buffer, format="PNG")
    url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    response = api.post("/api/signatures", headers=auth(contractor_token), json={"image_data": url, "source": "drawn"})
    assert response.status_code == 201 and response.json()["source"] == "drawn"
    legacy = api.post("/api/signatures", headers=auth(contractor_token), json={"image_data": url})
    assert legacy.status_code == 201 and legacy.json()["source"] is None


@pytest.mark.parametrize("font", ["GreatVibes", "Allura", "Sacramento"])
def test_the_longest_typed_signature_still_fits_the_pixel_limits(api, contractor_token, font):
    response = _typed(api, contractor_token, text="W" * 64, font=font)
    assert response.status_code == 201, response.text
