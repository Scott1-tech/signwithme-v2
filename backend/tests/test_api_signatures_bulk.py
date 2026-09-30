"""Bulk signature placement: one image, one decode, N slots.

The endpoint exists for cost (an "apply to all" click used to re-upload the
same 2MB image once per slot), but the tests here are mostly about the cost
saving not buying a weaker gate. Every control the single-placement endpoint
enforces is re-proven against the bulk route: the signing gate, the access
check, "`field_id` must be a signature slot of *this* template", coordinates
from the template rather than the client, no overwrite of a signed slot, and
`placed_by_user_id` on every row (the delete authorisation depends on it).
"""
import base64
import io
from uuid import UUID

import pytest
from PIL import Image
from sqlalchemy import select

from tests.conftest import _make_pdf, auth
from tests.test_api_signing import _force_signable

SLOTS = ["p1_contractor_signature", "p2_contractor_signature", "p3_contractor_signature"]


@pytest.fixture
def signature_data_url() -> str:
    image = Image.new("RGBA", (240, 60), (0, 0, 0, 0))
    for x in range(10, 230):
        image.putpixel((x, 30), (0, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def multi_slot_contract(api, admin_token, contractor_token) -> str:
    """A three-page package: one signature slot and one date slot per page.

    The single-page fixture in conftest has exactly one signature slot, which
    cannot show the difference between "all placed" and "some placed".
    """
    template = api.post(
        "/api/templates",
        headers=auth(admin_token),
        files={"file": ("template.pdf", _make_pdf(filled=False, pages=3), "application/pdf")},
        data={"name": "Three-slot package"},
    )
    assert template.status_code == 202, template.text
    template_id = template.json()["template_id"]

    contract = api.post(
        "/api/contracts",
        headers=auth(contractor_token),
        files={"file": ("contract.pdf", _make_pdf(filled=True, pages=3), "application/pdf")},
        data={"template_id": template_id, "contractor_name": "Charles Frye"},
    )
    assert contract.status_code == 202, contract.text
    contract_id = contract.json()["contract_id"]
    _force_signable(api, contract_id)
    return contract_id


def _bulk(api, token, contract_id, image, field_ids, **extra):
    return api.post(
        f"/api/contracts/{contract_id}/signatures/bulk",
        headers=auth(token),
        json={"image_data": image, "field_ids": field_ids, **extra},
    )


def _template_fields(api) -> dict:
    from app.models.template import Template
    from app.services.comparison_engine import _index_schema

    db = api.db_factory()
    try:
        # The three-page template is the second one uploaded in this database.
        templates = list(db.scalars(select(Template)))
        return _index_schema(templates[-1].field_schema)
    finally:
        db.close()


def _rows(api, contract_id):
    from app.models.signature import Signature

    db = api.db_factory()
    try:
        return list(
            db.scalars(
                select(Signature).where(Signature.contract_id == UUID(contract_id))
            )
        )
    finally:
        db.close()


# -- 1. every empty slot is filled -----------------------------------------


def test_every_empty_slot_is_filled_from_the_template_coordinates(
    api, contractor_token, multi_slot_contract, signature_data_url
):
    response = _bulk(api, contractor_token, multi_slot_contract, signature_data_url, SLOTS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [p["field_id"] for p in body["placed"]] == SLOTS
    assert body["skipped"] == []
    assert body["failed"] == []
    assert body["contract_id"] == multi_slot_contract

    index = _template_fields(api)
    me = api.get("/api/auth/me", headers=auth(contractor_token)).json()
    rows = {row.field_id: row for row in _rows(api, multi_slot_contract)}
    assert set(rows) == set(SLOTS)
    for page_number, field_id in enumerate(SLOTS, start=1):
        row = rows[field_id]
        assert row.bbox == index[field_id]["bbox"], field_id
        assert row.page_number == page_number
        # The delete authorisation added 2026-09-10 reads this column; a row
        # without it would be admin-only to remove.
        assert str(row.placed_by_user_id) == me["id"]
        assert row.signature_image_path in api.objects

    # One storage object per row, identical bytes: removing one signature must
    # not strip the image out from under the others.
    paths = {row.signature_image_path for row in rows.values()}
    assert len(paths) == len(SLOTS)
    assert len({api.objects[p] for p in paths}) == 1
    assert len({row.signature_hash for row in rows.values()}) == 1

    # The response carries the same coordinates, so the UI needs no re-fetch.
    for entry in body["placed"]:
        assert entry["bbox"] == index[entry["field_id"]]["bbox"]


# -- 2. an already-signed slot is skipped, not overwritten -------------------


def test_an_already_signed_slot_is_skipped_and_keeps_its_signature(
    api, contractor_token, multi_slot_contract, signature_data_url
):
    first = api.post(
        f"/api/contracts/{multi_slot_contract}/signatures",
        headers=auth(contractor_token),
        json={"field_id": "p2_contractor_signature", "image_data": signature_data_url},
    )
    assert first.status_code == 201, first.text

    response = _bulk(api, contractor_token, multi_slot_contract, signature_data_url, SLOTS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [p["field_id"] for p in body["placed"]] == [
        "p1_contractor_signature",
        "p3_contractor_signature",
    ]
    assert body["skipped"] == [
        {"field_id": "p2_contractor_signature", "reason": "already_signed"}
    ]
    assert body["failed"] == []

    rows = _rows(api, multi_slot_contract)
    assert len(rows) == 3  # not four: the taken slot was not signed twice
    kept = [row for row in rows if row.field_id == "p2_contractor_signature"]
    assert [str(row.id) for row in kept] == [first.json()["id"]]


# -- 3. a bad field id fails that slot only ---------------------------------


def test_an_unknown_or_non_signature_field_fails_only_its_own_slot(
    api, contractor_token, multi_slot_contract, signature_data_url
):
    """Security: a text field must never be recorded as a signed slot, and one
    bad id must not cost the caller the placements that were valid."""
    response = _bulk(
        api,
        contractor_token,
        multi_slot_contract,
        signature_data_url,
        ["p1_contractor_signature", "not_a_field", "p1_full_name", "p3_contractor_signature"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [p["field_id"] for p in body["placed"]] == [
        "p1_contractor_signature",
        "p3_contractor_signature",
    ]
    assert body["skipped"] == []
    assert body["failed"] == [
        {"field_id": "not_a_field", "reason": "unknown_field"},
        {"field_id": "p1_full_name", "reason": "not_a_signature_field"},
    ]

    stored = {row.field_id for row in _rows(api, multi_slot_contract)}
    assert stored == {"p1_contractor_signature", "p3_contractor_signature"}


def test_a_date_slot_cannot_be_signed_in_bulk(
    api, contractor_token, multi_slot_contract, signature_data_url
):
    response = _bulk(
        api, contractor_token, multi_slot_contract, signature_data_url, ["p1_date"]
    )
    assert response.status_code == 200, response.text
    assert response.json()["failed"] == [
        {"field_id": "p1_date", "reason": "not_a_signature_field"}
    ]
    assert _rows(api, multi_slot_contract) == []


# -- 4. image failures reject the whole request -----------------------------


def test_a_bad_image_rejects_the_whole_request(
    api, contractor_token, multi_slot_contract
):
    """There is one image, so an image failure is a request failure -- no slot
    is signed with a half-validated file."""
    svg = base64.b64encode(
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    ).decode()
    response = _bulk(
        api, contractor_token, multi_slot_contract, "data:image/svg+xml;base64," + svg, SLOTS
    )
    assert response.status_code == 422, response.text
    assert response.json()["message"] == "Unsupported image format"
    assert _rows(api, multi_slot_contract) == []

    oversized = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024 + 1)).decode()
    response = _bulk(api, contractor_token, multi_slot_contract, oversized, SLOTS)
    assert response.status_code == 413, response.text
    assert _rows(api, multi_slot_contract) == []

    bomb = Image.new("1", (10000, 10000), 1)
    buffer = io.BytesIO()
    bomb.save(buffer, format="PNG")
    response = _bulk(
        api,
        contractor_token,
        multi_slot_contract,
        base64.b64encode(buffer.getvalue()).decode(),
        SLOTS,
    )
    assert response.status_code == 422, response.text
    assert _rows(api, multi_slot_contract) == []


# -- 5. the signing gate ----------------------------------------------------


def test_bulk_placement_is_refused_before_approval(
    api, contractor_token, admin_token, ready_template, filled_contract_bytes, signature_data_url
):
    contract = api.post(
        "/api/contracts",
        headers=auth(contractor_token),
        files={"file": ("contract.pdf", filled_contract_bytes, "application/pdf")},
        data={"template_id": ready_template, "contractor_name": "Charles Frye"},
    )
    contract_id = contract.json()["contract_id"]
    response = _bulk(
        api, contractor_token, contract_id, signature_data_url, ["p1_contractor_signature"]
    )
    assert response.status_code == 409, response.text
    assert _rows(api, contract_id) == []


def test_bulk_placement_is_refused_on_a_finalised_contract(
    api, contractor_token, multi_slot_contract, signature_data_url
):
    placed = _bulk(api, contractor_token, multi_slot_contract, signature_data_url, SLOTS)
    assert placed.status_code == 200, placed.text
    finalized = api.post(
        f"/api/contracts/{multi_slot_contract}/finalize",
        headers=auth(contractor_token),
        json={"contract_date": "2026-08-29", "apply_paired_dates": True},
    )
    assert finalized.status_code == 200, finalized.text

    again = _bulk(api, contractor_token, multi_slot_contract, signature_data_url, SLOTS)
    assert again.status_code == 409, again.text
    assert len(_rows(api, multi_slot_contract)) == 3


# -- 6. access control ------------------------------------------------------


def test_bulk_placement_is_refused_to_an_unrelated_contractor(
    api, multi_slot_contract, signature_data_url
):
    from tests.conftest import _register

    stranger = _register(api, "stranger@example.com")
    response = _bulk(api, stranger, multi_slot_contract, signature_data_url, SLOTS)
    assert response.status_code == 403, response.text
    assert response.json()["error"] == "forbidden"
    assert _rows(api, multi_slot_contract) == []


# -- 7. abuse bounds on the slot list ---------------------------------------


def test_an_empty_oversized_or_duplicated_slot_list_is_rejected(
    api, contractor_token, multi_slot_contract, signature_data_url
):
    from app.schemas.contract import MAX_BULK_SIGNATURE_SLOTS

    assert _bulk(api, contractor_token, multi_slot_contract, signature_data_url, []).status_code == 422

    too_many = [f"p{n}_contractor_signature" for n in range(MAX_BULK_SIGNATURE_SLOTS + 1)]
    assert (
        _bulk(api, contractor_token, multi_slot_contract, signature_data_url, too_many).status_code
        == 422
    )

    duplicated = ["p1_contractor_signature", "p1_contractor_signature"]
    assert (
        _bulk(api, contractor_token, multi_slot_contract, signature_data_url, duplicated).status_code
        == 422
    )
    assert _rows(api, multi_slot_contract) == []


def test_a_client_supplied_bbox_is_ignored_by_the_bulk_endpoint(
    api, contractor_token, multi_slot_contract, signature_data_url
):
    """Security: this endpoint has no bbox/page field at all, so an extra key
    cannot steer a signature onto a different clause."""
    response = _bulk(
        api,
        contractor_token,
        multi_slot_contract,
        signature_data_url,
        ["p1_contractor_signature"],
        bbox={"x": 0, "y": 0, "width": 1, "height": 1},
        page=3,
    )
    assert response.status_code == 200, response.text
    index = _template_fields(api)
    row = _rows(api, multi_slot_contract)[0]
    assert row.bbox == index["p1_contractor_signature"]["bbox"]
    assert row.page_number == 1


# -- 8. the image is decoded exactly once -----------------------------------


def test_the_image_is_decoded_once_for_the_whole_request(
    api, contractor_token, multi_slot_contract, signature_data_url, monkeypatch
):
    """The whole point of the endpoint: N slots, one Pillow decode/re-encode."""
    from app.api import signatures as signatures_module

    calls: list[int] = []
    original = signatures_module.decode_signature_image

    def counted(image_data: str) -> bytes:
        calls.append(len(image_data))
        return original(image_data)

    monkeypatch.setattr(signatures_module, "decode_signature_image", counted)

    response = _bulk(api, contractor_token, multi_slot_contract, signature_data_url, SLOTS)
    assert response.status_code == 200, response.text
    assert len(response.json()["placed"]) == 3
    assert len(calls) == 1

    # Free the slots again, then fill them the way "apply to all" used to: one
    # request -- and one full decode/re-encode -- per slot. That is the cost
    # this endpoint removes.
    for entry in response.json()["placed"]:
        assert (
            api.delete(
                f"/api/contracts/{multi_slot_contract}/signatures/{entry['signature_id']}",
                headers=auth(contractor_token),
            ).status_code
            == 200
        )
    calls.clear()
    for field_id in SLOTS:
        single = api.post(
            f"/api/contracts/{multi_slot_contract}/signatures",
            headers=auth(contractor_token),
            json={"field_id": field_id, "image_data": signature_data_url},
        )
        assert single.status_code == 201, single.text
    assert len(calls) == 3
    # ...and the client had to upload the image three times to do it.
    assert sum(calls) == 3 * len(signature_data_url)


# -- 9. the audit trail -----------------------------------------------------


def test_bulk_placement_keeps_a_per_signature_audit_entry(
    api, contractor_token, admin_token, multi_slot_contract, signature_data_url
):
    response = _bulk(
        api,
        contractor_token,
        multi_slot_contract,
        signature_data_url,
        SLOTS + ["not_a_field"],
    )
    assert response.status_code == 200, response.text

    trail = api.get(f"/api/review/{multi_slot_contract}/audit", headers=auth(admin_token)).json()
    added = [entry for entry in trail if entry["action"] == "signature_added"]
    assert sorted(entry["details"]["field_id"] for entry in added) == sorted(SLOTS)

    summary = [entry for entry in trail if entry["action"] == "signatures_bulk_added"]
    assert len(summary) == 1
    assert summary[0]["details"]["requested"] == 4
    assert summary[0]["details"]["placed"] == 3
    assert summary[0]["details"]["failed"] == 1


def test_no_bulk_audit_entry_when_nothing_was_placed(
    api, contractor_token, admin_token, multi_slot_contract, signature_data_url
):
    response = _bulk(
        api, contractor_token, multi_slot_contract, signature_data_url, ["not_a_field"]
    )
    assert response.status_code == 200, response.text
    trail = api.get(f"/api/review/{multi_slot_contract}/audit", headers=auth(admin_token)).json()
    assert [e for e in trail if e["action"] in ("signature_added", "signatures_bulk_added")] == []


# -- 10. bulk then finalize -------------------------------------------------


def test_bulk_placement_finalises_into_a_pdf_carrying_every_signature(
    api, contractor_token, multi_slot_contract, signature_data_url
):
    response = _bulk(api, contractor_token, multi_slot_contract, signature_data_url, SLOTS)
    assert response.status_code == 200, response.text

    finalized = api.post(
        f"/api/contracts/{multi_slot_contract}/finalize",
        headers=auth(contractor_token),
        json={"contract_date": "2026-08-29", "apply_paired_dates": True},
    )
    assert finalized.status_code == 200, finalized.text
    assert len(finalized.json()["file_hash"]) == 64

    download = api.get(f"/api/download/{multi_slot_contract}/final", headers=auth(contractor_token))
    assert download.status_code == 200
    assert download.content.startswith(b"%PDF-")

    # Every slot signature reached the document: one image XObject per page.
    import pdfplumber

    with pdfplumber.open(io.BytesIO(download.content)) as pdf:
        assert sum(len(page.images) for page in pdf.pages) >= len(SLOTS)


def test_a_bulk_placed_signature_can_be_removed_by_its_placer(
    api, contractor_token, multi_slot_contract, signature_data_url
):
    """The delete path is unchanged, which only holds because every bulk row
    records `placed_by_user_id`."""
    response = _bulk(api, contractor_token, multi_slot_contract, signature_data_url, SLOTS)
    placed = response.json()["placed"]
    target = placed[0]["signature_id"]
    other_paths = {
        row.signature_image_path
        for row in _rows(api, multi_slot_contract)
        if str(row.id) != target
    }

    removed = api.delete(
        f"/api/contracts/{multi_slot_contract}/signatures/{target}",
        headers=auth(contractor_token),
    )
    assert removed.status_code == 200, removed.text

    remaining = _rows(api, multi_slot_contract)
    assert len(remaining) == 2
    # The other two still have their own image objects.
    assert other_paths <= set(api.objects)
