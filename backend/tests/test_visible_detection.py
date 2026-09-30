"""Detection decision 2026-09-17: "automatic skip + warning".

A signature or date the contract visibly shows is filled -- even when the
template file shows the same one -- and is therefore not pending signing work.
Leakage from the surrounding form is still kept out by geometry (guard A: the
rule band and the label filter; guard B: image placement) and, for dates, by
content (only a date-shaped value fills a date slot). A wrong upload is
surfaced as a warning instead: `prefilled_summary` on templates and
`template_match` on the comparison. Neither touches `can_sign`.
"""
from __future__ import annotations

import io
from uuid import UUID

import pytest
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.services.comparison_engine import ComparisonEngine
from app.services.field_detector import FieldDetector
from app.services.pdf_parser import BASELINE_KEY, PdfParser, _find_date
from tests.conftest import auth

PAGE_HEIGHT = letter[1]


def _png(color: str = "black", size=(40, 40)) -> ImageReader:
    picture = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return ImageReader(io.BytesIO(buffer.getvalue()))


def _write(path, draw) -> str:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.setFont("Helvetica", 11)
    draw(pdf)
    pdf.showPage()
    pdf.save()
    return str(path)


def _signature_form(pdf, *, date_text: str | None = None, rule_text: str | None = None):
    pdf.drawString(72, 720, "INDEPENDENT CONTRACTOR AGREEMENT")
    pdf.drawString(72, 212, "I certify that the information provided above is true and complete.")
    pdf.drawString(72, 200, "Contractor Signature:")
    pdf.drawString(200, 200, "_" * 25)
    pdf.drawString(400, 200, "Date:")
    pdf.drawString(440, 200, "__/__/____")
    if date_text:
        pdf.drawString(442, 202, date_text)
    if rule_text:
        pdf.drawString(205, 202, rule_text)


def _slots(schema: dict, extracted: dict) -> tuple[dict, dict, dict, dict]:
    fields = {f["field_id"]: f for page in schema["pages"] for f in page["fields"]}
    signature = next(f for f in fields.values() if f["field_type"] == "signature")
    date = fields[signature["paired_date_field_id"]]
    return signature, date, extracted[signature["field_id"]], extracted[date["field_id"]]


def _extract(template_path: str, contract_path: str) -> tuple[dict, dict, dict, dict, dict]:
    parser = PdfParser()
    schema = parser.with_template_baseline(template_path, FieldDetector().build_schema(template_path))
    extracted = parser.extract(contract_path, schema)
    return (schema, *_slots(schema, extracted))


# -- signatures: visible = filled, guards kept -----------------------------------


def test_signature_image_and_date_identical_to_the_template_are_filled(tmp_path):
    def signed(pdf):
        _signature_form(pdf, date_text="01/30/2026")
        pdf.drawImage(_png(size=(120, 40)), 210, 192, width=60, height=22)

    template = _write(tmp_path / "t.pdf", signed)
    contract = _write(tmp_path / "c.pdf", signed)
    schema, _, _, signature, date = _extract(template, contract)
    assert signature["has_signature"] is True
    assert date["value"] == "01/30/2026" and date["detected"] is True

    comparison = ComparisonEngine().compare(PdfParser().extract(contract, schema), schema).to_dict()
    assert comparison["signature_slots"] == []


def test_guard_b_an_image_hanging_down_from_the_row_above_is_not_a_signature(tmp_path):
    template = _write(tmp_path / "t.pdf", _signature_form)
    schema = FieldDetector().build_schema(template)
    signature_field = next(
        f for page in schema["pages"] for f in page["fields"] if f["field_type"] == "signature"
    )
    box = signature_field["bbox"]

    def hanging(pdf):
        _signature_form(pdf)
        # Bottom 8pt of a 30pt image inside the box's top edge: centre above
        # the box, 27% of the image inside -- a stamp belonging to the row above.
        bottom_top_coords = box["y"] + 8
        pdf.drawImage(
            _png(size=(120, 60)), box["x"] + 5, PAGE_HEIGHT - bottom_top_coords, width=60, height=30
        )

    contract = _write(tmp_path / "c.pdf", hanging)
    extracted = PdfParser().extract(contract, schema)
    assert extracted[signature_field["field_id"]]["has_signature"] is False


def test_guard_a_a_label_on_the_rule_is_not_a_typed_name(tmp_path):
    template = _write(tmp_path / "t.pdf", _signature_form)
    contract = _write(tmp_path / "c.pdf", lambda pdf: _signature_form(pdf, rule_text="Signature:"))
    _, _, _, signature, _ = _extract(template, contract)
    assert signature["has_signature"] is False


def test_an_empty_rule_is_unsigned_and_a_placeholder_date_is_empty(tmp_path):
    template = _write(tmp_path / "t.pdf", _signature_form)
    _, _, _, signature, date = _extract(template, template)
    assert signature["has_signature"] is False
    assert date["value"] is None and date["detected"] is False


# -- dates: only a date-shaped value fills the slot ------------------------------


@pytest.mark.parametrize("leak", ["sideration.", "Date:", "PART", "er (MRO), and other"])
def test_leaked_prose_or_a_label_in_a_date_slot_is_empty(tmp_path, leak):
    template = _write(tmp_path / "t.pdf", _signature_form)
    contract = _write(tmp_path / "c.pdf", lambda pdf: _signature_form(pdf, date_text=leak))
    _, _, _, _, date = _extract(template, contract)
    assert date["value"] is None and date["detected"] is False
    assert date["confidence"] == 0.0


@pytest.mark.parametrize(
    "written, expected",
    [("01/30/2026", "01/30/2026"), ("1-5-26", "1-5-26"), ("Aug 22, 2026", "Aug 22, 2026")],
)
def test_real_dates_in_a_date_slot_are_filled(tmp_path, written, expected):
    template = _write(tmp_path / "t.pdf", _signature_form)
    contract = _write(tmp_path / "c.pdf", lambda pdf: _signature_form(pdf, date_text=written))
    _, _, _, _, date = _extract(template, contract)
    assert date["value"] == expected and date["detected"] is True


def test_date_shape_boundaries():
    for blank in ("//", "/ /", "sideration.", "Date:", "Aug 2026", "May", "9", "99", "D12345678",
                  "13/2029", "12 345 2026", "Mayor 12, 2026", None, ""):
        assert _find_date(blank) is None, blank
    assert _find_date("01//30//2026") == "01/30/2026"
    assert _find_date("Date:01/30/2026") == "01/30/2026"
    assert _find_date("Exp 1/5/2030") == "1/5/2030"
    assert _find_date("9 / 9 / 2026") == "9/9/2026"
    assert _find_date("2026-01-30") == "2026-01-30"
    assert _find_date("22nd August, 2026") == "22nd August, 2026"
    assert _find_date("01 30 2026 //") == "01/30/2026"
    assert _find_date("0 1 / 3 0 / 2 0 2 6") == "01/30/2026"
    assert _find_date("08/2029") == "08/2029"
    assert _find_date("01302026") == "01302026"  # counted, returned as written
    assert _find_date("99999999") is None  # no calendar reading


def _narrow_date_schema(x: float, width: float) -> dict:
    return {
        "pages": [{
            "page_number": 1, "width": 612, "height": 792,
            "fields": [{
                "field_id": "p1_date", "label": "Date", "field_type": "date", "page": 1,
                "bbox": {"x": x, "y": 478.0, "width": width, "height": 14.0},
            }],
        }],
        "field_count": 1,
    }


def _date_running_past_the_box(pdf):
    pdf.drawString(72, 300, "Date:")
    pdf.drawString(110, 300, "_" * 20)
    pdf.drawString(130, 302, "01/30/2026")  # glyphs x 130..~185, top 481.3


def test_a_date_starting_in_a_narrow_box_is_read_from_the_rest_of_its_row(tmp_path):
    contract = _write(tmp_path / "c.pdf", _date_running_past_the_box)
    extracted = PdfParser().extract(contract, _narrow_date_schema(110.0, 24.0))
    assert extracted["p1_date"]["value"] == "01/30/2026"


def test_a_date_starting_right_of_the_box_does_not_fill_it(tmp_path):
    contract = _write(tmp_path / "c.pdf", _date_running_past_the_box)
    extracted = PdfParser().extract(contract, _narrow_date_schema(110.0, 16.0))
    assert extracted["p1_date"]["value"] is None


# -- checkboxes ticked with an image ----------------------------------------------


def _checkbox_schema() -> dict:
    options = [
        {"id": "individual", "label": "Individual",
         "bbox": {"x": 149.0, "y": 222.0, "width": 12.0, "height": 12.0}},
        {"id": "llc", "label": "LLC",
         "bbox": {"x": 169.0, "y": 222.0, "width": 12.0, "height": 12.0}},
    ]
    return {
        "pages": [{
            "page_number": 1, "width": 612, "height": 792,
            "fields": [
                {"field_id": "p1_w_9_status", "label": "W-9", "field_type": "checkbox_group", "page": 1,
                 "bbox": {"x": 149.0, "y": 222.0, "width": 32.0, "height": 12.0}, "options": options},
                {"field_id": "p1_single", "label": "Class 4", "field_type": "checkbox", "page": 1,
                 "bbox": {"x": 249.0, "y": 222.0, "width": 12.0, "height": 12.0}},
            ],
        }],
        "field_count": 2,
    }


def _boxes(pdf, tick_x: float | None = None, image_size=(8, 8), single_tick: bool = False):
    # Option boxes at x 150 and 170 (10pt, top 224 in page coordinates).
    for x in (150, 170, 250):
        pdf.rect(x, 558, 10, 10, stroke=1, fill=0)
    if tick_x is not None:
        width, height = image_size
        pdf.drawImage(_png(), tick_x, 563 - height / 2, width=width, height=height)
    if single_tick:
        pdf.drawImage(_png(), 251, 559, width=8, height=8)


def test_an_image_tick_checks_only_the_option_it_is_centred_on(tmp_path):
    parser = PdfParser()
    first = _write(tmp_path / "a.pdf", lambda pdf: _boxes(pdf, tick_x=151))
    result = parser.extract(first, _checkbox_schema())
    assert result["p1_w_9_status"]["selected"] == ["individual"]

    second = _write(tmp_path / "b.pdf", lambda pdf: _boxes(pdf, tick_x=171))
    result = parser.extract(second, _checkbox_schema())
    assert result["p1_w_9_status"]["selected"] == ["llc"]

    single = _write(tmp_path / "c.pdf", lambda pdf: _boxes(pdf, single_tick=True))
    result = parser.extract(single, _checkbox_schema())
    assert result["p1_single"]["checked"] is True
    assert result["p1_w_9_status"]["selected"] == []


def test_an_image_much_larger_than_the_box_is_not_a_tick(tmp_path):
    # A 40x30 stamp centred over the first box: > 1.5x the box on both axes.
    big = _write(tmp_path / "big.pdf", lambda pdf: _boxes(pdf, tick_x=135, image_size=(40, 30)))
    result = PdfParser().extract(big, _checkbox_schema())
    assert result["p1_w_9_status"]["selected"] == []


def test_unticked_image_free_boxes_stay_unchecked(tmp_path):
    empty = _write(tmp_path / "e.pdf", _boxes)
    result = PdfParser().extract(empty, _checkbox_schema())
    assert result["p1_w_9_status"]["selected"] == [] and result["p1_single"]["checked"] is False


# -- template warning: prefilled_summary -------------------------------------------


def _baseline_schema(signatures: list[bool], dates: list[bool]) -> dict:
    fields = []
    for index, prefilled in enumerate(signatures):
        fields.append({"field_id": f"s{index}", "field_type": "signature",
                       BASELINE_KEY: {"image_hashes": [], "strokes": 0, "text_tokens": [], "prefilled": prefilled}})
    for index, prefilled in enumerate(dates):
        fields.append({"field_id": f"d{index}", "field_type": "date",
                       BASELINE_KEY: {"image_hashes": [], "strokes": 0, "text_tokens": [], "prefilled": prefilled}})
    fields.append({"field_id": "t0", "field_type": "text_line"})
    return {"pages": [{"page_number": 1, "fields": fields}], "field_count": len(fields)}


def test_prefilled_summary_counts_and_warning_threshold():
    half = PdfParser.prefilled_summary(_baseline_schema([True, False], [True, False]))
    assert half == {"signatures": {"filled": 1, "total": 2}, "dates": {"filled": 1, "total": 2}, "warning": False}

    over = PdfParser.prefilled_summary(_baseline_schema([True, True, False], [False]))
    assert over["signatures"] == {"filled": 2, "total": 3} and over["warning"] is True

    dates_only = PdfParser.prefilled_summary(_baseline_schema([False], [True, True, False]))
    assert dates_only["warning"] is True

    none_filled = PdfParser.prefilled_summary(_baseline_schema([], []))
    assert none_filled == {"signatures": {"filled": 0, "total": 0}, "dates": {"filled": 0, "total": 0}, "warning": False}


def test_prefilled_summary_is_none_without_a_baseline():
    legacy = _baseline_schema([True], [True])
    for field in legacy["pages"][0]["fields"]:
        field.pop(BASELINE_KEY, None)
    assert PdfParser.prefilled_summary(legacy) is None
    assert PdfParser.prefilled_summary({}) is None
    assert PdfParser.prefilled_summary(None) is None


def test_templates_api_returns_the_prefilled_summary(api, admin_token, ready_template):
    # The fixture form: one signature slot; two date slots (Exp: and Date:).
    blank = {"signatures": {"filled": 0, "total": 1}, "dates": {"filled": 0, "total": 2}, "warning": False}
    detail = api.get(f"/api/templates/{ready_template}", headers=auth(admin_token)).json()
    assert detail["prefilled_summary"] == blank
    listing = api.get("/api/templates", headers=auth(admin_token)).json()
    assert next(t for t in listing if t["id"] == ready_template)["prefilled_summary"] == blank

    from app.models.template import Template

    db = api.db_factory()
    try:
        template = db.get(Template, UUID(ready_template))
        schema = {**template.field_schema, "pages": [
            {**page, "fields": [
                {**f, BASELINE_KEY: {**f[BASELINE_KEY], "prefilled": True}} if BASELINE_KEY in f else f
                for f in page["fields"]
            ]}
            for page in template.field_schema["pages"]
        ]}
        template.field_schema = schema
        db.commit()
    finally:
        db.close()
    detail = api.get(f"/api/templates/{ready_template}", headers=auth(admin_token)).json()
    assert detail["prefilled_summary"]["warning"] is True
    assert detail["prefilled_summary"]["signatures"] == {"filled": 1, "total": 1}


# -- contract warning: template_match ------------------------------------------------


def test_template_match_on_files(blank_template_path, filled_contract_path, tmp_path):
    parser = PdfParser()
    schema = FieldDetector().build_schema(blank_template_path)
    same = parser.template_match(blank_template_path, blank_template_path, schema)
    assert same == {"identical": True, "page_similarity": 1.0, "image_match_ratio": 1.0}

    filled = parser.template_match(blank_template_path, filled_contract_path, schema)
    assert filled["identical"] is False and filled["page_similarity"] < 0.995

    from tests.conftest import _make_pdf

    longer = tmp_path / "longer.pdf"
    longer.write_bytes(_make_pdf(filled=False, pages=2))
    extra_page = parser.template_match(blank_template_path, str(longer), schema)
    assert extra_page["identical"] is False and extra_page["page_similarity"] == 0.0


def test_template_match_needs_the_same_signature_images(tmp_path):
    def signed(color):
        def draw(pdf):
            _signature_form(pdf)
            pdf.drawImage(_png(color, size=(120, 40)), 210, 192, width=60, height=22)
        return draw

    template = _write(tmp_path / "t.pdf", signed("black"))
    other = _write(tmp_path / "o.pdf", signed("navy"))
    schema = FieldDetector().build_schema(template)
    parser = PdfParser()
    assert parser.template_match(template, template, schema)["identical"] is True
    result = parser.template_match(template, other, schema)
    assert result["page_similarity"] == 1.0
    assert result["image_match_ratio"] == 0.0 and result["identical"] is False


def _upload_contract(api, token: str, template_id: str, data: bytes, name: str) -> str:
    response = api.post(
        "/api/contracts",
        headers=auth(token),
        files={"file": (f"{name}.pdf", data, "application/pdf")},
        data={"template_id": template_id, "contractor_name": name},
    )
    assert response.status_code == 202, response.text
    return response.json()["contract_id"]


def test_compare_reports_template_match_and_can_sign_ignores_it(
    api, admin_token, contractor_token, ready_template, blank_template_bytes, filled_contract_bytes
):
    from app.models.contract import Contract
    from app.models.template import Template

    same = _upload_contract(api, contractor_token, ready_template, blank_template_bytes, "Same File")
    other = _upload_contract(api, contractor_token, ready_template, filled_contract_bytes, "Charles Frye")

    same_view = api.get(f"/api/contracts/{same}/compare", headers=auth(admin_token)).json()
    other_view = api.get(f"/api/contracts/{other}/compare", headers=auth(admin_token)).json()
    assert same_view["template_match"]["identical"] is True
    assert same_view["template_match"]["page_similarity"] == 1.0
    assert other_view["template_match"]["identical"] is False

    db = api.db_factory()
    try:
        template = db.get(Template, UUID(ready_template))
        for contract_id, view in ((same, same_view), (other, other_view)):
            contract = db.get(Contract, UUID(contract_id))
            # can_sign is exactly the engine's own verdict: template_match is
            # recorded next to it and never feeds into it.
            engine = ComparisonEngine().compare(
                contract.extracted_data, template.field_schema, contract_id=contract_id
            ).to_dict()
            assert view["can_sign"] == engine["can_sign"] == contract.comparison_result["can_sign"]
            assert "template_match" not in engine
    finally:
        db.close()


def test_rerunning_compare_keeps_the_stored_template_match(
    api, admin_token, contractor_token, ready_template, blank_template_bytes
):
    same = _upload_contract(api, contractor_token, ready_template, blank_template_bytes, "Same File")
    before = api.get(f"/api/contracts/{same}/compare", headers=auth(admin_token)).json()
    assert before["template_match"]["identical"] is True

    rerun = api.post(f"/api/contracts/{same}/compare", headers=auth(admin_token))
    assert rerun.status_code == 200, rerun.text
    assert rerun.json()["template_match"] == before["template_match"]
    after = api.get(f"/api/contracts/{same}/compare", headers=auth(admin_token)).json()
    assert after["template_match"] == before["template_match"]


def test_a_comparison_stored_before_template_match_returns_null(
    api, admin_token, uploaded_contract
):
    from app.models.contract import Contract

    db = api.db_factory()
    try:
        contract = db.get(Contract, UUID(uploaded_contract))
        legacy = dict(contract.comparison_result)
        legacy.pop("template_match", None)
        contract.comparison_result = legacy
        db.commit()
    finally:
        db.close()
    view = api.get(f"/api/contracts/{uploaded_contract}/compare", headers=auth(admin_token)).json()
    assert view["template_match"] is None
