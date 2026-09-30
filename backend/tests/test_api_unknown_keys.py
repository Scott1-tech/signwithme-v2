"""An unknown key on a request body is refused, not ignored.

Two bodies here have the same shape: every key optional, and a handler that
acts on `model_dump(exclude_unset=True)`. Left open, a misspelt key is not an
error at all -- it is dropped before the handler ever sees it, the request
succeeds, and what the user did is gone without a word:

* `POST /api/contracts/{id}/finalize` -- `annotation` instead of `annotations`
  (or `date` instead of `dates`, `layout_` instead of `layout`) produces a 200
  and a FINAL, SIGNED PDF without the marks the user placed on it. Nobody is
  told; it is found by reading the signed contract afterwards.
* `PATCH /api/contracts/{id}/fields/{field_id}` -- `valu` instead of `value`
  keeps the parser's own reading and still stamps the field `verified` with
  confidence 1.0 and the validation error cleared, i.e. the reviewer is told
  the correction was saved while the wrong value walks into the signing gate.

So both are `extra="forbid"`, like every part they are made of already is
(`FinalizeLayout`, `AnnotationIn`, `BBoxModel`). The other half of each pair
below is the guard on the fix: the body the frontend really sends -- every key
of `signaturesApi.finalize` (frontend/src/api/signatures.ts:175),
`editorApi.finalize` (frontend/src/api/editor.ts:36) and
`contractsApi.correctField` (frontend/src/api/contracts.ts:106) -- still has to
be accepted, or the fix would have broken signing instead of protecting it.

The refusal must also stay quiet about the value, as app/main.py requires:
an unknown key can carry anything the user typed.
"""
import base64
import io
from uuid import UUID

import pytest
from PIL import Image

from tests.conftest import auth
from tests.test_api_signing import _force_signable

# Planted as the VALUE of the unknown key. Nothing in a 422 may contain it.
SECRET = "SSN 123-45-6789"


@pytest.fixture
def signature_data_url() -> str:
    image = Image.new("RGBA", (240, 60), (0, 0, 0, 0))
    for x in range(10, 230):
        image.putpixel((x, 30), (0, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _contract(api, contract_id):
    from app.models.contract import Contract

    db = api.db_factory()
    try:
        return db.get(Contract, UUID(contract_id))
    finally:
        db.close()


def _ready(api, token, contract_id, image):
    """A contract that is one `finalize` call away from being signed."""
    _force_signable(api, contract_id)
    response = api.post(
        f"/api/contracts/{contract_id}/signatures",
        headers=auth(token),
        json={"field_id": "p1_contractor_signature", "image_data": image},
    )
    assert response.status_code == 201, response.text


def _finalize(api, token, contract_id, body):
    return api.post(f"/api/contracts/{contract_id}/finalize", headers=auth(token), json=body)


def _field(api, token, contract_id, field_id) -> dict:
    fields = api.get(f"/api/contracts/{contract_id}/fields", headers=auth(token)).json()
    return next(f for f in fields if f["template_field_id"] == field_id)


def _unknown_keys(response) -> list[str]:
    """The keys the server refused, from the `extra_forbidden` entries."""
    return [
        entry["loc"][-1]
        for entry in response.json()["detail"]
        if entry["type"] == "extra_forbidden"
    ]


# -- finalize -----------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["annotation", "annotaions", "dates_", "date", "layout_", "Annotations", "signatures"],
)
def test_a_misspelled_finalize_key_is_422_and_nothing_is_finalised(
    api, contractor_token, uploaded_contract, signature_data_url, key
):
    """The bug this closes: the contract WOULD have been finalised -- 200, a
    downloadable signed PDF -- with the misspelt key's contents missing."""
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    note = {"id": "a1", "kind": "text", "page": 1, "x_pt": 120.5, "y_pt": 640.0, "text": "N/A"}
    response = _finalize(
        api, contractor_token, uploaded_contract, {"contract_date": "2026-09-24", key: [note]}
    )
    assert response.status_code == 422, response.text
    assert _unknown_keys(response) == [key]
    contract = _contract(api, uploaded_contract)
    assert contract.status != "completed"
    assert contract.final_file_path is None


def test_a_refused_finalize_key_does_not_echo_what_it_carried(
    api, contractor_token, uploaded_contract
):
    """An unknown key's value is still the user's data (app/main.py): the name
    of the key comes back, never what was under it."""
    response = _finalize(
        api, contractor_token, uploaded_contract, {"notes": SECRET, "annotation": SECRET}
    )
    assert response.status_code == 422, response.text
    assert SECRET not in response.text
    for entry in response.json()["detail"]:
        assert set(entry) == {"loc", "msg", "type"}
    assert sorted(_unknown_keys(response)) == ["annotation", "notes"]


def test_every_key_the_frontend_sends_at_finalize_is_still_accepted(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """The union of both finalize callers in the frontend, in one body:
    `contract_date`, `dates`, `apply_paired_dates`, `layout` (every key
    `layoutForRequest` can produce, frontend/src/lib/layout.ts:239) and
    `annotations`."""
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(
        api,
        contractor_token,
        uploaded_contract,
        {
            "contract_date": "2026-09-24",
            "dates": [{"field_id": "p1_date", "value": "2026-09-10", "page": 1}],
            "apply_paired_dates": False,
            "layout": {
                "signature_offset_pt": 1.5,
                "date_offset_pt": -0.5,
                "slot_offsets_pt": {"p1_contractor_signature": 2.0},
                "slot_offsets_x_pt": {"p1_contractor_signature": 1.0},
                "signature_scale": 1.1,
                "slot_scales": {"p1_contractor_signature": 1.25},
                "date_font": "Times-Roman",
                "date_font_size": 12.0,
            },
            "annotations": [
                {
                    "id": "a1",
                    "kind": "text",
                    "page": 1,
                    "x_pt": 120.5,
                    "y_pt": 640.0,
                    "text": "N/A",
                    "font": "Courier",
                    "size_pt": 11.0,
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["file_hash"]) == 64
    assert _contract(api, uploaded_contract).status == "completed"


def test_an_empty_finalize_body_is_still_valid_input(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """Every key is optional and stays optional: `{}` is not a schema error.

    What it gets is the signing gate's 409 naming the date field nobody filled
    -- a refusal about the contract, not about the body.
    """
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract, {})
    assert response.status_code == 409, response.text
    assert [f["field_id"] for f in response.json()["details"]["missing_date_fields"]] == ["p1_date"]


# -- field correction ---------------------------------------------------------


@pytest.mark.parametrize("key", ["valu", "values", "Value", "new_value", "text"])
def test_a_misspelled_correction_key_is_422_and_the_field_is_untouched(
    api, admin_token, uploaded_contract, key
):
    """Before the fix this answered 200 with `status: verified`, having stored
    nothing: the parser's reading stayed and was marked as checked by a human."""
    before = _field(api, admin_token, uploaded_contract, "p1_email")
    response = api.patch(
        f"/api/contracts/{uploaded_contract}/fields/p1_email",
        headers=auth(admin_token),
        json={key: "corrected@example.com"},
    )
    assert response.status_code == 422, response.text
    assert _unknown_keys(response) == [key]
    after = _field(api, admin_token, uploaded_contract, "p1_email")
    assert after["raw_value"] == before["raw_value"]
    assert after["status"] == before["status"] != "verified"


def test_a_refused_correction_does_not_echo_the_value(api, admin_token, uploaded_contract):
    response = api.patch(
        f"/api/contracts/{uploaded_contract}/fields/p1_email",
        headers=auth(admin_token),
        json={"valu": SECRET},
    )
    assert response.status_code == 422, response.text
    assert SECRET not in response.text


def test_every_key_a_correction_declares_is_still_accepted(api, admin_token, uploaded_contract):
    """`contractsApi.correctField` sends `value` (and may send `selected` /
    `checked`); `mark_verified` is the fourth declared key."""
    response = api.patch(
        f"/api/contracts/{uploaded_contract}/fields/p1_email",
        headers=auth(admin_token),
        json={"value": "corrected@example.com"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["raw_value"] == "corrected@example.com"
    assert response.json()["status"] == "verified"

    response = api.patch(
        f"/api/contracts/{uploaded_contract}/fields/p1_email",
        headers=auth(admin_token),
        json={
            "value": "again@example.com",
            "selected": ["individual"],
            "checked": True,
            "mark_verified": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["raw_value"] == "again@example.com"
    assert response.json()["status"] == "filled"
