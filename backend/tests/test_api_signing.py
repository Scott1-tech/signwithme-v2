"""The signing gate and finalisation, over HTTP.

The frontend hides the finalise button when a contract cannot be signed. These
tests cover the control that actually matters: a client posting straight to the
API.
"""
import base64
import io

import pytest
from PIL import Image
from sqlalchemy import select

from tests.conftest import auth


@pytest.fixture
def signature_data_url() -> str:
    image = Image.new("RGBA", (240, 60), (0, 0, 0, 0))
    for x in range(10, 230):
        image.putpixel((x, 30), (0, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _force_signable(api, contract_id: str) -> None:
    """Put the contract in the state a clean review would leave it in."""
    from datetime import datetime, timezone

    from app.models.contract import Contract

    db = api.db_factory()
    try:
        contract = db.get(Contract, __import__("uuid").UUID(contract_id))
        result = dict(contract.comparison_result)
        result.update(
            {"can_sign": True, "missing_required": [], "inconsistencies": [], "fmcsa_violations": []}
        )
        contract.comparison_result = result
        contract.review_status = "approved"
        contract.reviewed_at = datetime.now(timezone.utc)
        contract.status = "signing"
        db.commit()
    finally:
        db.close()


def test_signing_is_refused_while_critical_issues_remain(
    api, contractor_token, uploaded_contract, signature_data_url
):
    response = api.post(
        f"/api/contracts/{uploaded_contract}/signatures",
        headers=auth(contractor_token),
        json={"page": 1, "bbox": {"x": 200, "y": 580, "width": 150, "height": 34},
              "image_data": signature_data_url},
    )
    assert response.status_code == 409
    assert "approved" in response.json()["message"] or "critical" in response.json()["message"]


def test_finalize_is_refused_before_approval(api, contractor_token, uploaded_contract):
    response = api.post(
        f"/api/contracts/{uploaded_contract}/finalize",
        headers=auth(contractor_token),
        json={"contract_date": "2026-08-29"},
    )
    assert response.status_code == 409


def _corrupt_data(api, contract_id: str, drop_field: str = "p1_cdl") -> None:
    """Remove a required data field from the stored extraction, as if the
    parser had never read it, so the engine's data gate has something to block."""
    from app.models.contract import Contract

    db = api.db_factory()
    try:
        contract = db.get(Contract, __import__("uuid").UUID(contract_id))
        data = dict(contract.extracted_data)
        data.pop(drop_field, None)
        contract.extracted_data = data  # reassign so the JSON column sees the change
        db.commit()
    finally:
        db.close()


def test_a_reviewer_cannot_approve_a_contract_the_engine_blocked(
    api, uploaded_contract, admin_token, contractor_token
):
    """Approval is not an override switch for outstanding critical issues.

    An unsigned contract is no longer blocked (signing comes after review), so
    the block here is a genuine data problem: a required CDL field the parser
    did not find. The engine re-run must flip `can_sign` off and the reviewer
    must be refused.
    """
    _corrupt_data(api, uploaded_contract, "p1_cdl")
    rerun = api.post(f"/api/contracts/{uploaded_contract}/compare", headers=auth(contractor_token))
    assert rerun.status_code == 200, rerun.text
    comparison = rerun.json()
    assert comparison["can_sign"] is False
    assert "p1_cdl" in {m["field_id"] for m in comparison["missing_required"]}
    # The slots are still reported next to the real problem.
    assert comparison["signature_slots"]

    response = api.post(
        f"/api/review/{uploaded_contract}",
        headers=auth(admin_token),
        json={"decision": "approved", "notes": "looks fine to me"},
    )
    assert response.status_code == 409
    assert "cannot be approved" in response.json()["message"]


def test_a_reviewer_can_approve_when_only_signature_slots_are_pending(
    api, uploaded_contract, admin_token, contractor_token
):
    """Empty signature/date slots are signing work, not a data defect; a
    reviewer may approve a complete, consistent, unsigned contract."""
    comparison = api.get(
        f"/api/contracts/{uploaded_contract}/compare", headers=auth(contractor_token)
    ).json()
    assert comparison["can_sign"] is True, comparison
    assert comparison["signature_slots"]
    assert all(slot["severity"] == "info" for slot in comparison["signature_slots"])

    response = api.post(
        f"/api/review/{uploaded_contract}",
        headers=auth(admin_token),
        json={"decision": "approved", "notes": "data checks out"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["review_status"] == "approved"


def test_review_rejection_is_recorded(api, uploaded_contract, admin_token):
    response = api.post(
        f"/api/review/{uploaded_contract}",
        headers=auth(admin_token),
        json={"decision": "rejected", "notes": "CDL page is unreadable"},
    )
    assert response.status_code == 200
    assert response.json()["review_status"] == "rejected"


def test_finalize_requires_at_least_one_signature(api, contractor_token, uploaded_contract):
    _force_signable(api, uploaded_contract)
    response = api.post(
        f"/api/contracts/{uploaded_contract}/finalize",
        headers=auth(contractor_token),
        json={"contract_date": "2026-08-29"},
    )
    assert response.status_code == 409
    assert "No signatures" in response.json()["message"]


def test_a_corrupt_signature_image_is_rejected(api, contractor_token, uploaded_contract):
    _force_signable(api, uploaded_contract)
    response = api.post(
        f"/api/contracts/{uploaded_contract}/signatures",
        headers=auth(contractor_token),
        json={"page": 1, "bbox": {"x": 200, "y": 580, "width": 150, "height": 34},
              "image_data": "data:image/png;base64,bm90LWFuLWltYWdl"},
    )
    assert response.status_code == 422


def test_contract_signature_placement_shares_the_image_hardening(
    api, contractor_token, uploaded_contract
):
    """Both signature endpoints decode through `deps.decode_signature_image`,
    so the format and size gates proven in test_api_user_signatures apply
    here too. Spot-check the two most important ones on this endpoint."""
    _force_signable(api, uploaded_contract)
    placement = {"page": 1, "bbox": {"x": 200, "y": 580, "width": 150, "height": 34}}

    svg = base64.b64encode(
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    ).decode()
    response = api.post(
        f"/api/contracts/{uploaded_contract}/signatures",
        headers=auth(contractor_token),
        json={**placement, "image_data": "data:image/svg+xml;base64," + svg},
    )
    assert response.status_code == 422, response.text
    assert response.json()["message"] == "Unsupported image format"

    oversized = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024 + 1)).decode()
    response = api.post(
        f"/api/contracts/{uploaded_contract}/signatures",
        headers=auth(contractor_token),
        json={**placement, "image_data": oversized},
    )
    assert response.status_code == 413, response.text

    bomb = Image.new("1", (10000, 10000), 1)
    buffer = io.BytesIO()
    bomb.save(buffer, format="PNG")
    response = api.post(
        f"/api/contracts/{uploaded_contract}/signatures",
        headers=auth(contractor_token),
        json={**placement, "image_data": base64.b64encode(buffer.getvalue()).decode()},
    )
    assert response.status_code == 422, response.text


def test_the_happy_path_produces_a_hashed_downloadable_pdf(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """Paired-date fallback: the slot signature carries `paired_date_field_id`
    = p1_date, so `contract_date` + `apply_paired_dates` fills the one required
    date field without listing it explicitly in `dates`."""
    _force_signable(api, uploaded_contract)

    placed = api.post(
        f"/api/contracts/{uploaded_contract}/signatures",
        headers=auth(contractor_token),
        json={
            "page": 1,
            "bbox": {"x": 200, "y": 580, "width": 150, "height": 34},
            "field_id": "p1_contractor_signature",
            "image_data": signature_data_url,
        },
    )
    assert placed.status_code == 201, placed.text

    finalized = api.post(
        f"/api/contracts/{uploaded_contract}/finalize",
        headers=auth(contractor_token),
        json={"contract_date": "2026-08-29", "apply_paired_dates": True},
    )
    assert finalized.status_code == 200, finalized.text
    body = finalized.json()
    assert len(body["file_hash"]) == 64

    download = api.get(f"/api/download/{uploaded_contract}/final", headers=auth(contractor_token))
    assert download.status_code == 200
    assert download.content.startswith(b"%PDF-")
    assert download.headers["x-content-sha256"] == body["file_hash"]

    verified = api.get(f"/api/download/{uploaded_contract}/verify", headers=auth(contractor_token))
    assert verified.json()["intact"] is True


def test_the_finalised_contract_cannot_be_signed_again(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    api.post(
        f"/api/contracts/{uploaded_contract}/signatures",
        headers=auth(contractor_token),
        json={"page": 1, "bbox": {"x": 200, "y": 580, "width": 150, "height": 34},
              "field_id": "p1_contractor_signature", "image_data": signature_data_url},
    )
    finalized = api.post(
        f"/api/contracts/{uploaded_contract}/finalize",
        headers=auth(contractor_token),
        json={"contract_date": "2026-08-29"},
    )
    assert finalized.status_code == 200, finalized.text
    again = api.post(
        f"/api/contracts/{uploaded_contract}/signatures",
        headers=auth(contractor_token),
        json={"page": 1, "bbox": {"x": 200, "y": 600, "width": 150, "height": 34},
              "image_data": signature_data_url},
    )
    assert again.status_code == 409


def test_the_audit_trail_records_the_whole_lifecycle(
    api, contractor_token, uploaded_contract, admin_token, signature_data_url
):
    _force_signable(api, uploaded_contract)
    api.post(
        f"/api/contracts/{uploaded_contract}/signatures",
        headers=auth(contractor_token),
        json={"page": 1, "bbox": {"x": 200, "y": 580, "width": 150, "height": 34},
              "field_id": "p1_contractor_signature", "image_data": signature_data_url},
    )
    finalized = api.post(
        f"/api/contracts/{uploaded_contract}/finalize",
        headers=auth(contractor_token),
        json={"contract_date": "2026-08-29"},
    )
    assert finalized.status_code == 200, finalized.text
    api.get(f"/api/download/{uploaded_contract}/final", headers=auth(contractor_token))

    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    actions = {entry["action"] for entry in trail}
    assert {
        "contract_uploaded",
        "contract_parsed",
        "comparison_run",
        "signature_added",
        "contract_finalized",
        "final_downloaded",
    } <= actions


def test_download_is_refused_to_an_unrelated_contractor(
    api, contractor_token, uploaded_contract, signature_data_url
):
    from tests.conftest import _register

    _force_signable(api, uploaded_contract)
    api.post(
        f"/api/contracts/{uploaded_contract}/signatures",
        headers=auth(contractor_token),
        json={"page": 1, "bbox": {"x": 200, "y": 580, "width": 150, "height": 34},
              "field_id": "p1_contractor_signature", "image_data": signature_data_url},
    )
    finalized = api.post(
        f"/api/contracts/{uploaded_contract}/finalize",
        headers=auth(contractor_token),
        json={"contract_date": "2026-08-29"},
    )
    assert finalized.status_code == 200, finalized.text

    stranger = _register(api, "stranger@example.com")
    response = api.get(f"/api/download/{uploaded_contract}/final", headers=auth(stranger))
    assert response.status_code == 403


def test_signatures_are_stored_with_a_hash_and_a_path(
    api, contractor_token, uploaded_contract, signature_data_url
):
    from app.models.signature import Signature

    _force_signable(api, uploaded_contract)
    api.post(
        f"/api/contracts/{uploaded_contract}/signatures",
        headers=auth(contractor_token),
        json={"page": 1, "bbox": {"x": 200, "y": 580, "width": 150, "height": 34},
              "image_data": signature_data_url},
    )
    db = api.db_factory()
    try:
        row = db.scalar(select(Signature))
        assert len(row.signature_hash) == 64
        assert row.signature_image_path in api.objects
    finally:
        db.close()


# --------------------------------------------------------------------------
# Per-slot signatures and dates: finalize insists on every required slot, the
# template (never the client) decides where a slot signature or date lands.
# --------------------------------------------------------------------------


def _template_field(api, field_id: str) -> dict:
    from app.models.template import Template
    from app.services.comparison_engine import _index_schema

    db = api.db_factory()
    try:
        template = db.scalar(select(Template))
        return _index_schema(template.field_schema)[field_id]
    finally:
        db.close()


def _place(api, token, contract_id, image, **extra):
    payload = {"image_data": image, **extra}
    return api.post(f"/api/contracts/{contract_id}/signatures", headers=auth(token), json=payload)


def test_finalize_refuses_while_a_required_signature_slot_is_empty(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """A free-placed signature does not fill the contract's signature slot."""
    _force_signable(api, uploaded_contract)
    placed = _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        page=1, bbox={"x": 200, "y": 580, "width": 150, "height": 34},
    )
    assert placed.status_code == 201, placed.text

    response = api.post(
        f"/api/contracts/{uploaded_contract}/finalize",
        headers=auth(contractor_token),
        json={"dates": [{"field_id": "p1_date", "value": "2026-09-10"}], "apply_paired_dates": False},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"] == "conflict"
    missing = body["details"]["missing_signature_slots"]
    assert missing[0]["field_id"] == "p1_contractor_signature"
    assert {"field_id", "label", "page"} <= set(missing[0])
    assert missing[0]["page"] == 1
    assert body["details"]["missing_date_fields"] == []


def test_finalize_refuses_while_a_required_date_field_is_empty(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    placed = _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        page=1, bbox={"x": 200, "y": 580, "width": 150, "height": 34},
        field_id="p1_contractor_signature",
    )
    assert placed.status_code == 201, placed.text

    response = api.post(
        f"/api/contracts/{uploaded_contract}/finalize",
        headers=auth(contractor_token),
        json={"dates": [], "apply_paired_dates": False},
    )
    assert response.status_code == 409, response.text
    details = response.json()["details"]
    assert details["missing_signature_slots"] == []
    assert [d["field_id"] for d in details["missing_date_fields"]] == ["p1_date"]


def test_explicit_per_slot_dates_finalise_and_are_audited(
    api, contractor_token, uploaded_contract, admin_token, signature_data_url
):
    """The frontend's payload: one date per field id, no bbox, no fallback."""
    _force_signable(api, uploaded_contract)
    placed = _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        page=1, bbox={"x": 200, "y": 580, "width": 150, "height": 34},
        field_id="p1_contractor_signature",
    )
    assert placed.status_code == 201, placed.text

    response = api.post(
        f"/api/contracts/{uploaded_contract}/finalize",
        headers=auth(contractor_token),
        json={"dates": [{"field_id": "p1_date", "value": "2026-09-10"}], "apply_paired_dates": False},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["file_hash"]) == 64

    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    dated = [e for e in trail if e["action"] == "date_added"]
    assert [e["details"]["field_id"] for e in dated] == ["p1_date"]
    assert dated[0]["details"]["value"] == "09/10/2026"
    assert dated[0]["details"]["page"] == 1


def test_a_legacy_contract_date_key_is_tolerated_alongside_explicit_dates(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        field_id="p1_contractor_signature",
    )
    response = api.post(
        f"/api/contracts/{uploaded_contract}/finalize",
        headers=auth(contractor_token),
        json={
            "contract_date": "2026-08-29",
            "dates": [{"field_id": "p1_date", "value": "2026-09-10", "page": 7}],
            "apply_paired_dates": False,
        },
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("field_id", ["p1_full_name", "p1_date", "not_a_field"])
def test_a_signature_cannot_be_bound_to_a_non_signature_field(
    api, contractor_token, uploaded_contract, signature_data_url, field_id
):
    """Security: `field_id` must name a signature slot of *this* template. A
    text field, a date field or an invented id is refused, not silently stored
    and later counted as a signed slot."""
    _force_signable(api, uploaded_contract)
    response = _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        page=1, bbox={"x": 200, "y": 580, "width": 150, "height": 34}, field_id=field_id,
    )
    assert response.status_code == 422, response.text
    assert field_id in response.json()["message"]


def test_slot_coordinates_come_from_the_template_not_the_client(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """Security: with a field_id the client's page/bbox are ignored, so a
    signature cannot be steered onto a different clause."""
    from app.models.signature import Signature

    _force_signable(api, uploaded_contract)
    response = _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        page=1, bbox={"x": 0, "y": 0, "width": 1, "height": 1},
        field_id="p1_contractor_signature",
    )
    assert response.status_code == 201, response.text

    expected = _template_field(api, "p1_contractor_signature")
    db = api.db_factory()
    try:
        row = db.scalar(select(Signature))
        assert row.bbox == expected["bbox"]
        assert row.page_number == 1
        assert row.field_id == "p1_contractor_signature"
    finally:
        db.close()
    assert response.json()["bbox"] == expected["bbox"]


def test_bbox_and_page_are_optional_when_a_field_id_is_given(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    response = _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        field_id="p1_contractor_signature",
    )
    assert response.status_code == 201, response.text
    expected = _template_field(api, "p1_contractor_signature")
    assert response.json()["bbox"] == expected["bbox"]
    assert response.json()["page_number"] == 1


def test_free_placement_still_requires_page_and_bbox(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    assert _place(api, contractor_token, uploaded_contract, signature_data_url).status_code == 422
    assert _place(api, contractor_token, uploaded_contract, signature_data_url, page=1).status_code == 422
    assert _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        bbox={"x": 200, "y": 580, "width": 150, "height": 34},
    ).status_code == 422


@pytest.mark.parametrize("field_id", ["p1_full_name", "p1_contractor_signature", "not_a_field"])
def test_a_date_cannot_be_printed_into_a_non_date_field(
    api, contractor_token, uploaded_contract, signature_data_url, field_id
):
    """Security: dates land only in template date slots; the client supplies
    no bbox and cannot pick a text field or the signature box as a target."""
    _force_signable(api, uploaded_contract)
    _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        field_id="p1_contractor_signature",
    )
    response = api.post(
        f"/api/contracts/{uploaded_contract}/finalize",
        headers=auth(contractor_token),
        json={"dates": [{"field_id": field_id, "value": "2026-09-10"}], "apply_paired_dates": False},
    )
    assert response.status_code == 422, response.text
    assert field_id in response.json()["message"]


def test_a_date_placement_without_a_field_id_is_rejected(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        field_id="p1_contractor_signature",
    )
    response = api.post(
        f"/api/contracts/{uploaded_contract}/finalize",
        headers=auth(contractor_token),
        json={"dates": [{"page": 1, "bbox": {"x": 1, "y": 1, "width": 5, "height": 5},
                         "value": "2026-09-10"}]},
    )
    assert response.status_code == 422


def test_a_slot_takes_only_one_signature(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    first = _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        field_id="p1_contractor_signature",
    )
    assert first.status_code == 201, first.text
    second = _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        field_id="p1_contractor_signature",
    )
    assert second.status_code == 409, second.text
    assert "already has a signature" in second.json()["message"]

    # Removing the first frees the slot again.
    removed = api.delete(
        f"/api/contracts/{uploaded_contract}/signatures/{first.json()['id']}",
        headers=auth(contractor_token),
    )
    assert removed.status_code == 200
    third = _place(
        api, contractor_token, uploaded_contract, signature_data_url,
        field_id="p1_contractor_signature",
    )
    assert third.status_code == 201, third.text


# --------------------------------------------------------------------------
# Removing a signature: only the account that placed it, or an admin. Viewing
# the contract is not enough -- a reviewer must not be able to strip a
# signature someone else placed.
# --------------------------------------------------------------------------


def _reviewer_token(api) -> str:
    from tests.conftest import _promote, _register

    _register(api, "reviewer@example.com")
    _promote(api, "reviewer@example.com", "reviewer")
    response = api.post(
        "/api/auth/login", json={"email": "reviewer@example.com", "password": "password123"}
    )
    return response.json()["access_token"]


def _signature_row(api, signature_id: str):
    from uuid import UUID

    from app.models.signature import Signature

    db = api.db_factory()
    try:
        return db.get(Signature, UUID(signature_id))
    finally:
        db.close()


def _place_free(api, token, contract_id, image):
    placed = _place(
        api, token, contract_id, image,
        page=1, bbox={"x": 200, "y": 580, "width": 150, "height": 34},
    )
    assert placed.status_code == 201, placed.text
    return placed.json()


def test_placing_a_signature_records_who_placed_it(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    placed = _place_free(api, contractor_token, uploaded_contract, signature_data_url)

    me = api.get("/api/auth/me", headers=auth(contractor_token)).json()
    row = _signature_row(api, placed["id"])
    assert str(row.placed_by_user_id) == me["id"]


def test_the_placer_can_remove_their_own_signature(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    placed = _place_free(api, contractor_token, uploaded_contract, signature_data_url)

    removed = api.delete(
        f"/api/contracts/{uploaded_contract}/signatures/{placed['id']}",
        headers=auth(contractor_token),
    )
    assert removed.status_code == 200, removed.text
    assert _signature_row(api, placed["id"]) is None


def test_another_user_who_can_view_the_contract_cannot_remove_the_signature(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """A reviewer sees every contract, but the signature is not theirs to remove."""
    _force_signable(api, uploaded_contract)
    placed = _place_free(api, contractor_token, uploaded_contract, signature_data_url)
    reviewer = _reviewer_token(api)

    # The reviewer can view the contract and its signatures...
    listed = api.get(f"/api/contracts/{uploaded_contract}/signatures", headers=auth(reviewer))
    assert listed.status_code == 200
    assert [s["id"] for s in listed.json()] == [placed["id"]]

    # ...but must not be able to remove one placed by someone else.
    forbidden = api.delete(
        f"/api/contracts/{uploaded_contract}/signatures/{placed['id']}",
        headers=auth(reviewer),
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["error"] == "forbidden"

    row = _signature_row(api, placed["id"])
    assert row is not None
    assert row.signature_image_path in api.objects  # the image was not deleted either


def test_an_admin_can_remove_any_signature(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    placed = _place_free(api, contractor_token, uploaded_contract, signature_data_url)

    removed = api.delete(
        f"/api/contracts/{uploaded_contract}/signatures/{placed['id']}",
        headers=auth(admin_token),
    )
    assert removed.status_code == 200, removed.text
    assert _signature_row(api, placed["id"]) is None


def test_a_signature_with_no_recorded_placer_is_admin_only_to_remove(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url
):
    """Rows from before `placed_by_user_id` existed have an unknown placer.
    The safe default is that only an admin may remove them, even for the
    contract's own contractor."""
    from uuid import UUID

    from app.models.signature import Signature

    _force_signable(api, uploaded_contract)
    placed = _place_free(api, contractor_token, uploaded_contract, signature_data_url)

    db = api.db_factory()
    try:
        db.get(Signature, UUID(placed["id"])).placed_by_user_id = None
        db.commit()
    finally:
        db.close()

    forbidden = api.delete(
        f"/api/contracts/{uploaded_contract}/signatures/{placed['id']}",
        headers=auth(contractor_token),
    )
    assert forbidden.status_code == 403, forbidden.text
    assert _signature_row(api, placed["id"]) is not None

    removed = api.delete(
        f"/api/contracts/{uploaded_contract}/signatures/{placed['id']}",
        headers=auth(admin_token),
    )
    assert removed.status_code == 200, removed.text
    assert _signature_row(api, placed["id"]) is None
