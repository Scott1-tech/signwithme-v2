"""Contract v1 over HTTP: `rule_y` on slots, the finalize `layout`, the page
guard for uploads shorter than their template, and the signature image route.

The page-guard fixture reproduces the production defect: a 34-page template
with 31-page uploads, whose p32/p34 slots were demanded by finalize and then
silently dropped by the builder. Here: 3-page template, 2-page contract.
"""
import base64
import copy
import io
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy import select

from tests.conftest import _make_pdf, _register, auth
from tests.test_api_signing import _force_signable


@pytest.fixture
def signature_data_url() -> str:
    image = Image.new("RGBA", (240, 60), (0, 0, 0, 0))
    for x in range(10, 230):
        for y in range(28, 34):
            image.putpixel((x, y), (0, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _short_contract(api, admin_token, contractor_token) -> str:
    template = api.post(
        "/api/templates",
        headers=auth(admin_token),
        files={"file": ("t.pdf", _make_pdf(filled=False, pages=3), "application/pdf")},
        data={"name": "Three-page template"},
    )
    assert template.status_code == 202, template.text
    contract = api.post(
        "/api/contracts",
        headers=auth(contractor_token),
        files={"file": ("c.pdf", _make_pdf(filled=True, pages=2), "application/pdf")},
        data={"template_id": template.json()["template_id"], "contractor_name": "Charles Frye"},
    )
    assert contract.status_code == 202, contract.text
    contract_id = contract.json()["contract_id"]
    _force_signable(api, contract_id)
    return contract_id


@pytest.fixture
def short_contract(api, admin_token, contractor_token) -> str:
    return _short_contract(api, admin_token, contractor_token)


def _contract(api, contract_id):
    from app.models.contract import Contract

    db = api.db_factory()
    try:
        return db.get(Contract, UUID(contract_id))
    finally:
        db.close()


def _template_of(api, contract_id):
    from app.models.template import Template

    db = api.db_factory()
    try:
        return db.get(Template, _contract(api, contract_id).template_id)
    finally:
        db.close()


def _finalize(api, token, contract_id, **body):
    return api.post(f"/api/contracts/{contract_id}/finalize", headers=auth(token), json=body)


# -- GET /compare: read-time filtering and enrichment ----------------------------


def test_compare_drops_slots_past_the_last_page_at_read_time(api, contractor_token, short_contract):
    stored = _contract(api, short_contract).comparison_result
    assert {s["page"] for s in stored["signature_slots"]} == {1, 2, 3}, "premise: stored has p3"

    body = api.get(f"/api/contracts/{short_contract}/compare", headers=auth(contractor_token)).json()
    assert {s["page"] for s in body["signature_slots"]} == {1, 2}
    assert body["can_sign"] == stored["can_sign"], "can_sign is not affected"
    # Read-time only: the stored comparison is not rewritten.
    assert {s["page"] for s in _contract(api, short_contract).comparison_result["signature_slots"]} == {1, 2, 3}


def test_compare_slots_carry_rule_y_from_the_template_schema(api, contractor_token, uploaded_contract):
    from app.services.comparison_engine import _index_schema

    body = api.get(f"/api/contracts/{uploaded_contract}/compare", headers=auth(contractor_token)).json()
    index = _index_schema(_template_of(api, uploaded_contract).field_schema)
    assert body["signature_slots"]
    for slot in body["signature_slots"]:
        assert "rule_y" in slot
        assert slot["rule_y"] == index[slot["field_id"]]["rule_y"]
    signature = next(s for s in body["signature_slots"] if s["field_id"] == "p1_contractor_signature")
    assert signature["rule_y"] is not None
    # The synthetic rule is a '_' run at reportlab baseline 200 (x 200..353).
    # Expected ink top comes from an independent full-page render, not metrics.
    from tests.test_rule_locator import rendered_ink_top

    baseline = 792 - 200
    expected = rendered_ink_top(_make_pdf(filled=False), 205, 330, baseline - 3, baseline + 8)
    assert abs(signature["rule_y"] - expected) <= 0.3, (signature["rule_y"], expected)
    assert signature["rule_y"] > baseline, "ink top, not the text baseline"


def test_an_existing_contract_gets_rule_y_without_a_reparse(api, contractor_token, uploaded_contract):
    """Template analysed and comparison stored before `rule_y` existed."""
    from app.models.contract import Contract
    from app.models.template import Template

    db = api.db_factory()
    try:
        contract = db.get(Contract, UUID(uploaded_contract))
        template = db.get(Template, contract.template_id)
        schema = copy.deepcopy(template.field_schema)
        for page in schema["pages"]:
            for field in page["fields"]:
                field.pop("rule_y", None)
        template.field_schema = schema
        result = copy.deepcopy(contract.comparison_result)
        for slot in result["signature_slots"]:
            slot.pop("rule_y", None)
        contract.comparison_result = result
        db.commit()
    finally:
        db.close()

    body = api.get(f"/api/contracts/{uploaded_contract}/compare", headers=auth(contractor_token)).json()
    signature = next(s for s in body["signature_slots"] if s["field_id"] == "p1_contractor_signature")
    assert signature["rule_y"] is not None
    # Derived once and kept on the template (additive key).
    from app.services.comparison_engine import _index_schema

    assert "rule_y" in _index_schema(_template_of(api, uploaded_contract).field_schema)["p1_contractor_signature"]


def test_template_analysis_stores_rule_y_additively(api, contractor_token, uploaded_contract):
    from app.services.comparison_engine import _index_schema

    index = _index_schema(_template_of(api, uploaded_contract).field_schema)
    for field_id in ("p1_contractor_signature", "p1_date"):
        assert "rule_y" in index[field_id]
        assert "template_baseline" in index[field_id], "existing keys stay"
    assert "rule_y" not in index["p1_full_name"]


# -- page guard -------------------------------------------------------------------


def test_placing_on_a_missing_page_is_422(api, contractor_token, short_contract, signature_data_url):
    slot = api.post(
        f"/api/contracts/{short_contract}/signatures",
        headers=auth(contractor_token),
        json={"field_id": "p3_contractor_signature", "image_data": signature_data_url},
    )
    assert slot.status_code == 422, slot.text
    assert slot.json()["details"]["reason"] == "page_out_of_range"
    assert slot.json()["details"]["page_count"] == 2

    free = api.post(
        f"/api/contracts/{short_contract}/signatures",
        headers=auth(contractor_token),
        json={"page": 3, "bbox": {"x": 200, "y": 580, "width": 150, "height": 34},
              "image_data": signature_data_url},
    )
    assert free.status_code == 422, free.text
    assert free.json()["details"]["reason"] == "page_out_of_range"


def test_bulk_reports_a_missing_page_per_slot(api, contractor_token, short_contract, signature_data_url):
    response = api.post(
        f"/api/contracts/{short_contract}/signatures/bulk",
        headers=auth(contractor_token),
        json={"image_data": signature_data_url,
              "field_ids": ["p1_contractor_signature", "p3_contractor_signature"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [p["field_id"] for p in body["placed"]] == ["p1_contractor_signature"]
    assert body["failed"] == [{"field_id": "p3_contractor_signature", "reason": "page_out_of_range"}]


def test_finalize_does_not_demand_slots_the_document_cannot_carry(
    api, contractor_token, short_contract, signature_data_url
):
    placed = api.post(
        f"/api/contracts/{short_contract}/signatures/bulk",
        headers=auth(contractor_token),
        json={"image_data": signature_data_url,
              "field_ids": ["p1_contractor_signature", "p2_contractor_signature"]},
    )
    assert len(placed.json()["placed"]) == 2, placed.text
    response = _finalize(api, contractor_token, short_contract,
                         contract_date="2026-09-14", apply_paired_dates=True)
    assert response.status_code == 200, response.text


def test_finalize_refuses_a_placement_on_a_missing_page(
    api, contractor_token, short_contract, signature_data_url
):
    from app.models.signature import Signature

    placed = api.post(
        f"/api/contracts/{short_contract}/signatures/bulk",
        headers=auth(contractor_token),
        json={"image_data": signature_data_url,
              "field_ids": ["p1_contractor_signature", "p2_contractor_signature"]},
    ).json()["placed"]
    # A row that predates the guard: placed on page 3 of a 2-page upload.
    db = api.db_factory()
    try:
        row = db.get(Signature, UUID(placed[1]["signature_id"]))
        row.page_number = 3
        db.commit()
    finally:
        db.close()

    response = _finalize(api, contractor_token, short_contract,
                         contract_date="2026-09-14", apply_paired_dates=False,
                         dates=[{"field_id": "p1_date", "value": "2026-09-14"},
                                {"field_id": "p2_date", "value": "2026-09-14"}])
    assert response.status_code == 409, response.text
    details = response.json()["details"]
    assert details["reason"] == "page_out_of_range"
    assert [s["page"] for s in details["signatures"]] == [3]
    assert _contract(api, short_contract).status != "completed"


# -- layout validation ------------------------------------------------------------


def _signed(api, token, contract_id, image):
    response = api.post(
        f"/api/contracts/{contract_id}/signatures",
        headers=auth(token),
        json={"field_id": "p1_contractor_signature", "image_data": image},
    )
    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    "layout",
    [
        {"signature_offset_pt": 20.01},
        {"signature_offset_pt": -20.01},
        {"date_offset_pt": 21},
        {"date_offset_pt": -20.5},
        {"signature_scale": 0.59},
        {"signature_scale": 1.51},
        {"date_font": "Arial"},
        {"date_font": "helvetica"},
        {"date_font_size": 7.9},
        {"date_font_size": 16.1},
        {"slot_offsets_pt": {"p1_contractor_signature": 200.01}},
        {"slot_offsets_pt": {"p1_contractor_signature": -200.5}},
        {"slot_offsets_pt": {"p1_contractor_signature": "NaN"}},
        {"slot_offsets_pt": {"p1_contractor_signature": "Infinity"}},
        {"slot_offsets_x_pt": {"p1_contractor_signature": 200.01}},
        {"slot_offsets_x_pt": {"p1_contractor_signature": -200.01}},
        {"slot_offsets_x_pt": {"p1_date": "NaN"}},
        {"slot_offsets_x_pt": {"p1_date": "-Infinity"}},
        {"slot_offsets_x_pt": {"p1_date": "wide"}},
        {"slot_offsets_x_pt": [1.0]},
        # contract v3: per-slot scale, range [0.3, 3.0].
        {"slot_scales": {"p1_contractor_signature": 0.29}},
        {"slot_scales": {"p1_contractor_signature": 3.01}},
        {"slot_scales": {"p1_contractor_signature": 0}},
        {"slot_scales": {"p1_contractor_signature": -1.0}},
        {"slot_scales": {"p1_contractor_signature": "NaN"}},
        {"slot_scales": {"p1_contractor_signature": "Infinity"}},
        {"slot_scales": {"p1_contractor_signature": "-Infinity"}},
        {"slot_scales": {"p1_contractor_signature": "big"}},
        {"slot_scales": [1.0]},
        {"slot_scale": {"p1_contractor_signature": 1.0}},
        {"unexpected": 1},
        {"signature_offset_pt": "NaN"},
        {"signature_offset_pt": "Infinity"},
    ],
)
def test_an_out_of_range_or_unknown_layout_value_is_422(
    api, contractor_token, uploaded_contract, signature_data_url, layout
):
    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract,
                         contract_date="2026-09-14", layout=layout)
    assert response.status_code == 422, response.text
    assert _contract(api, uploaded_contract).status != "completed"


@pytest.mark.parametrize("key", ["slot_offsets_pt", "slot_offsets_x_pt"])
def test_too_many_slot_offsets_is_422(api, contractor_token, uploaded_contract, signature_data_url, key):
    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    offsets = {f"p1_slot_{i}": 0.0 for i in range(501)}
    response = _finalize(api, contractor_token, uploaded_contract,
                         contract_date="2026-09-14", layout={key: offsets})
    assert response.status_code == 422, response.text
    assert _contract(api, uploaded_contract).status != "completed"


def test_too_many_slot_scales_is_422(api, contractor_token, uploaded_contract, signature_data_url):
    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    scales = {f"p1_slot_{i}": 1.0 for i in range(501)}
    response = _finalize(api, contractor_token, uploaded_contract,
                         contract_date="2026-09-14", layout={"slot_scales": scales})
    assert response.status_code == 422, response.text
    assert _contract(api, uploaded_contract).status != "completed"


@pytest.mark.parametrize("key", ["slot_offsets_pt", "slot_offsets_x_pt"])
@pytest.mark.parametrize("field_id", ["p1_full_name", "not_a_field"])
def test_a_slot_offset_for_a_non_slot_is_422(
    api, contractor_token, uploaded_contract, signature_data_url, field_id, key
):
    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-14",
                         layout={key: {field_id: 1.0}})
    assert response.status_code == 422, response.text
    body = response.json()
    assert {"error", "message", "details"} <= set(body)
    assert body["details"]["unknown_slot_offsets"] == [field_id]


def test_unknown_keys_of_both_offset_maps_are_reported_in_one_list(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(
        api, contractor_token, uploaded_contract, contract_date="2026-09-14",
        layout={
            "slot_offsets_pt": {"p1_contractor_signature": 1.0, "zz_vertical": 1.0, "shared_bad": 2.0},
            "slot_offsets_x_pt": {"p1_date": 1.0, "aa_horizontal": 1.0, "shared_bad": 2.0},
        },
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert {"error", "message", "details"} <= set(body)
    assert body["details"]["unknown_slot_offsets"] == ["aa_horizontal", "shared_bad", "zz_vertical"]
    assert _contract(api, uploaded_contract).status != "completed"


@pytest.mark.parametrize("key", ["slot_offsets_pt", "slot_offsets_x_pt", "slot_scales"])
def test_a_slot_offset_for_a_slot_on_a_missing_page_is_422(
    api, contractor_token, short_contract, signature_data_url, key
):
    api.post(
        f"/api/contracts/{short_contract}/signatures/bulk",
        headers=auth(contractor_token),
        json={"image_data": signature_data_url,
              "field_ids": ["p1_contractor_signature", "p2_contractor_signature"]},
    )
    response = _finalize(api, contractor_token, short_contract, contract_date="2026-09-14",
                         layout={key: {"p3_contractor_signature": 1.0}})
    assert response.status_code == 422, response.text
    assert response.json()["details"]["unknown_slot_offsets"] == ["p3_contractor_signature"]


# -- contract v3: slot_scales key rules -------------------------------------------


@pytest.mark.parametrize("field_id", ["p1_full_name", "not_a_field"])
def test_a_slot_scale_for_a_non_slot_is_422(
    api, contractor_token, uploaded_contract, signature_data_url, field_id
):
    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-14",
                         layout={"slot_scales": {field_id: 1.0}})
    assert response.status_code == 422, response.text
    assert response.json()["details"]["unknown_slot_offsets"] == [field_id]


def test_a_slot_scale_for_a_date_slot_is_422(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """A date field is a legitimate key for the OFFSET maps but not for
    `slot_scales`: date text is sized by `date_font_size`, so a date key there
    means the client is asking for something the server cannot do -- refused,
    never silently ignored."""
    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-14",
                         layout={"slot_scales": {"p1_date": 1.0}})
    assert response.status_code == 422, response.text
    assert response.json()["details"]["unknown_slot_offsets"] == ["p1_date"]
    assert _contract(api, uploaded_contract).status != "completed"

    # The very same key is fine in an offset map: the refusal is about scaling
    # a date, not about the field id.
    accepted = _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-14",
                         layout={"slot_offsets_pt": {"p1_date": 1.0}})
    assert accepted.status_code == 200, accepted.text


def test_bad_scale_keys_join_the_one_unknown_slot_offsets_list(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(
        api, contractor_token, uploaded_contract, contract_date="2026-09-14",
        layout={
            "slot_offsets_pt": {"p1_contractor_signature": 1.0, "zz_vertical": 1.0},
            "slot_scales": {"p1_contractor_signature": 1.0, "p1_date": 1.0, "aa_scale": 1.0},
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["details"]["unknown_slot_offsets"] == [
        "aa_scale", "p1_date", "zz_vertical"
    ]
    assert _contract(api, uploaded_contract).status != "completed"


def test_boundary_layout_is_accepted_and_audited(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    layout = {
        "signature_offset_pt": 20.0,
        "date_offset_pt": -20.0,
        "slot_offsets_pt": {"p1_contractor_signature": -200.0, "p1_date": 200.0},
        "slot_offsets_x_pt": {"p1_contractor_signature": 200.0, "p1_date": -200.0},
        "signature_scale": 0.6,
        "slot_scales": {"p1_contractor_signature": 3.0},
        "date_font": "Courier",
        "date_font_size": 16.0,
    }
    response = _finalize(api, contractor_token, uploaded_contract,
                         contract_date="2026-09-14", layout=layout)
    assert response.status_code == 200, response.text

    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    finalized = [e for e in trail if e["action"] == "contract_finalized"]
    assert len(finalized) == 1
    assert finalized[0]["details"]["layout"] == layout


def test_finalize_without_layout_records_the_defaults(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    assert _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-14").status_code == 200
    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    details = next(e for e in trail if e["action"] == "contract_finalized")["details"]
    assert details["layout"] == {
        "signature_offset_pt": 0.0, "date_offset_pt": 0.0, "slot_offsets_pt": {},
        "slot_offsets_x_pt": {},
        "signature_scale": 1.0, "slot_scales": {},
        "date_font": "Helvetica", "date_font_size": 11.0,
    }


@pytest.mark.parametrize("value", [0.0, 150.0, -200.0, 200.0])
def test_the_widened_vertical_slot_offset_range_is_accepted(
    api, contractor_token, uploaded_contract, signature_data_url, value
):
    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-14",
                         layout={"slot_offsets_pt": {"p1_contractor_signature": value}})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("value", [0.3, 0.5, 1.0, 1.51, 3.0])
def test_the_wider_per_slot_scale_range_is_accepted(
    api, contractor_token, uploaded_contract, signature_data_url, value
):
    """Deliberately wider than the global `signature_scale` [0.6, 1.5]: a corner
    drag must not hit the end of the range after a few pixels. 1.51 -- refused
    as a global value -- is accepted per slot."""
    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-14",
                         layout={"slot_scales": {"p1_contractor_signature": value}})
    assert response.status_code == 200, response.text


def test_the_global_scale_keeps_its_narrow_range(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-14",
                         layout={"signature_scale": 3.0})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("layout", [{"signature_offset_pt": 21.0}, {"date_offset_pt": -150.0}])
def test_global_offsets_keep_the_narrow_range(
    api, contractor_token, uploaded_contract, signature_data_url, layout
):
    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract,
                         contract_date="2026-09-14", layout=layout)
    assert response.status_code == 422, response.text


def test_a_free_signature_ignores_the_rule_and_slot_offsets_in_the_final_pdf(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """Contract v1.1/v2. The free box ends 5pt ABOVE a real fill line, inside the
    locator's search window: a live snap would move it onto the line, a leaked
    slot offset would move it 7pt further, and a leaked horizontal slot offset
    40pt right. None may happen; the slot mark itself takes both offsets."""
    import pdfplumber

    from app.models.signature import Signature
    from app.services.comparison_engine import _index_schema
    from app.services.pdf_builder import _ink_metrics

    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    rule_y = _index_schema(_template_of(api, uploaded_contract).field_schema)[
        "p1_contractor_signature"]["rule_y"]
    free_bottom = rule_y - 5.0
    free = api.post(
        f"/api/contracts/{uploaded_contract}/signatures",
        headers=auth(contractor_token),
        json={"page": 1, "bbox": {"x": 200, "y": free_bottom - 34, "width": 150, "height": 34},
              "image_data": signature_data_url},
    )
    assert free.status_code == 201, free.text

    slot_x = _index_schema(_template_of(api, uploaded_contract).field_schema)[
        "p1_contractor_signature"]["bbox"]["x"]
    layout = {"signature_offset_pt": 3.0, "slot_offsets_pt": {"p1_contractor_signature": 7.0},
              "slot_offsets_x_pt": {"p1_contractor_signature": 40.0}}
    response = _finalize(api, contractor_token, uploaded_contract,
                         contract_date="2026-09-14", layout=layout)
    assert response.status_code == 200, response.text
    pdf_bytes = api.get(f"/api/download/{uploaded_contract}/final",
                        headers=auth(contractor_token)).content

    db = api.db_factory()
    try:
        png = api.objects[db.scalar(select(Signature)).signature_image_path]
    finally:
        db.close()
    pixel_width, pixel_height, _, _, _, ink_bottom_px = _ink_metrics(png)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        rects = pdf.pages[0].images
    marks = sorted(
        (float(r["top"]) + ink_bottom_px * (float(r["bottom"]) - float(r["top"])) / pixel_height,
         float(r["x0"]))
        for r in rects
    )
    expected = sorted([
        (free_bottom - 1.0 - 3.0, 200.0),              # free: bbox bottom/x, global only, no dx
        (rule_y - 1.0 - (3.0 + 7.0), slot_x + 40.0),   # slot: rule, global + dy, bbox.x + dx
    ])
    assert len(marks) == 2
    for (got_y, got_x), (want_y, want_x) in zip(marks, expected):
        assert abs(got_y - want_y) <= 0.3, (marks, expected)
        assert abs(got_x - want_x) <= 0.3, (marks, expected)


def _final_image_rect(api, token, contract_id) -> dict:
    import pdfplumber

    pdf_bytes = api.get(f"/api/download/{contract_id}/final", headers=auth(token)).content
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        images = pdf.pages[0].images
    assert len(images) == 1, images
    return {k: float(images[0][k]) for k in ("x0", "top", "x1", "bottom")}


@pytest.mark.parametrize("slot_scale", [0.3, 1.0, 3.0])
def test_a_per_slot_scale_replaces_the_global_one_in_the_final_pdf(
    api, contractor_token, uploaded_contract, signature_data_url, slot_scale
):
    """End to end: `slot_scales` decides this slot's size on its own, and the
    bottom-left anchor (left edge, ink bottom) does not move with it."""
    from app.services.comparison_engine import _index_schema
    from app.services.pdf_builder import _ink_metrics

    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    slot = _index_schema(_template_of(api, uploaded_contract).field_schema)[
        "p1_contractor_signature"]
    bbox, rule_y = slot["bbox"], slot["rule_y"]

    response = _finalize(
        api, contractor_token, uploaded_contract, contract_date="2026-09-14",
        # A global the product would otherwise apply; the slot entry replaces it.
        layout={"signature_scale": 0.6, "slot_scales": {"p1_contractor_signature": slot_scale}},
    )
    assert response.status_code == 200, response.text

    # Measure the STORED (normalised) PNG: that is what the builder fits.
    from app.models.signature import Signature

    db = api.db_factory()
    try:
        png = api.objects[db.scalar(select(Signature)).signature_image_path]
    finally:
        db.close()
    pixel_width, pixel_height, _, _, _, ink_bottom_px = _ink_metrics(png)
    contain = min(bbox["width"] * slot_scale / pixel_width,
                  bbox["height"] * slot_scale / pixel_height)

    rect = _final_image_rect(api, contractor_token, uploaded_contract)
    assert abs((rect["x1"] - rect["x0"]) - pixel_width * contain) <= 0.3, rect
    # Product of the two scales (0.6 * slot_scale) would be a different width.
    if slot_scale != 1.0:
        assert abs((rect["x1"] - rect["x0"]) - pixel_width * contain * 0.6) > 1.0, rect
    # Bottom-left anchor: left edge on bbox.x, INK bottom one gap above the rule.
    assert abs(rect["x0"] - bbox["x"]) <= 0.3, rect
    ink_bottom = rect["top"] + ink_bottom_px * (rect["bottom"] - rect["top"]) / pixel_height
    assert abs(ink_bottom - (rule_y - 1.0)) <= 0.3, rect


def test_finalize_audit_records_the_per_slot_scales(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    _signed(api, contractor_token, uploaded_contract, signature_data_url)
    layout = {"signature_scale": 1.5, "slot_scales": {"p1_contractor_signature": 2.25}}
    assert _finalize(api, contractor_token, uploaded_contract,
                     contract_date="2026-09-14", layout=layout).status_code == 200

    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    details = next(e for e in trail if e["action"] == "contract_finalized")["details"]
    assert details["layout"]["slot_scales"] == {"p1_contractor_signature": 2.25}
    assert details["layout"]["signature_scale"] == 1.5


# -- GET .../signatures/{id}/image ----------------------------------------------------


def test_signature_image_is_served_to_those_who_can_view_the_contract(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url
):
    from app.models.signature import Signature

    _force_signable(api, uploaded_contract)
    placed = api.post(
        f"/api/contracts/{uploaded_contract}/signatures",
        headers=auth(contractor_token),
        json={"field_id": "p1_contractor_signature", "image_data": signature_data_url},
    ).json()
    url = f"/api/contracts/{uploaded_contract}/signatures/{placed['id']}/image"

    db = api.db_factory()
    try:
        stored = api.objects[db.get(Signature, UUID(placed["id"])).signature_image_path]
    finally:
        db.close()

    for token in (contractor_token, admin_token):
        response = api.get(url, headers=auth(token))
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "no-store"
        assert response.content == stored

    stranger = _register(api, "stranger@example.com")
    assert api.get(url, headers=auth(stranger)).status_code == 403
    assert api.get(url).status_code in (401, 403)


def test_signature_image_is_404_for_an_unknown_or_foreign_signature(
    api, admin_token, contractor_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    other = _short_contract(api, admin_token, contractor_token)
    foreign = api.post(
        f"/api/contracts/{other}/signatures",
        headers=auth(contractor_token),
        json={"field_id": "p1_contractor_signature", "image_data": signature_data_url},
    ).json()

    base = f"/api/contracts/{uploaded_contract}/signatures"
    assert api.get(f"{base}/{uuid4()}/image", headers=auth(contractor_token)).status_code == 404
    # A real signature id, but of another contract: never a cross-contract read.
    assert api.get(f"{base}/{foreign['id']}/image", headers=auth(contractor_token)).status_code == 404
    assert api.get(
        f"/api/contracts/{uuid4()}/signatures/{foreign['id']}/image", headers=auth(contractor_token)
    ).status_code == 404
