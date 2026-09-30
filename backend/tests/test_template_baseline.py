"""A slot is filled by what the contract visibly shows there -- and never by
prose, labels or placeholders leaking in from the surrounding form.

Production packages turned out to be signed documents uploaded as templates
(the same signature image baked into 33 of 40 slots), with paragraphs sitting
12pt above the signature rule and "__/__/____" date placeholders. The leaks are
kept out by geometry and content (rule band, label filter, image placement,
date shape). Subtracting whatever the template file shows was tried and
withdrawn (2026-09-17): a contract that visibly carries a signature or date is
filled even when the template file carries the same one; the template baseline
now only drives the "prefilled template" warning.
"""
from __future__ import annotations

import io

from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.services import validators as v
from app.services.field_detector import FieldDetector
from app.services.pdf_parser import BASELINE_KEY, PdfParser

SIGNATURE_X, SIGNATURE_Y = 200, 200  # reportlab baseline of the signature row


def _png(color: str, size=(120, 40)) -> ImageReader:
    picture = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return ImageReader(io.BytesIO(buffer.getvalue()))


def _pdf(path, *, image_color: str | None = None, typed_name: str | None = None,
         date_value: str | None = None, prose_above: bool = True) -> str:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.setFont("Helvetica", 11)
    pdf.drawString(72, 720, "INDEPENDENT CONTRACTOR AGREEMENT")
    pdf.drawString(72, 700, "Full Name: ______________________")
    if prose_above:
        pdf.drawString(72, SIGNATURE_Y + 12, "I certify that the information provided above is true and complete.")
    pdf.drawString(72, SIGNATURE_Y, "Contractor Signature:")
    pdf.drawString(SIGNATURE_X, SIGNATURE_Y, "_" * 25)
    pdf.drawString(400, SIGNATURE_Y, "Date:")
    pdf.drawString(440, SIGNATURE_Y, "__/__/____")
    if image_color:
        # A signature stamp straddling the rule, as e-sign tools place it.
        pdf.drawImage(_png(image_color), SIGNATURE_X + 10, SIGNATURE_Y - 8, width=60, height=22)
    if typed_name:
        pdf.drawString(SIGNATURE_X + 5, SIGNATURE_Y + 2, typed_name)
    if date_value:
        pdf.drawString(442, SIGNATURE_Y + 2, date_value)
    pdf.showPage()
    pdf.save()
    return str(path)


def _signature_and_date(schema, extracted):
    fields = {f["field_id"]: f for page in schema["pages"] for f in page["fields"]}
    signature = next(f for f in fields.values() if f["field_type"] == "signature")
    date = fields[signature["paired_date_field_id"]]
    return extracted[signature["field_id"]], extracted[date["field_id"]]


def _schema_with_baseline(template_path):
    parser = PdfParser()
    schema = FieldDetector().build_schema(template_path)
    assert parser.needs_template_baseline(schema)
    schema = parser.with_template_baseline(template_path, schema)
    assert not parser.needs_template_baseline(schema)
    return schema


# -- prose above the rule (no baseline needed) --------------------------------


def test_prose_12pt_above_an_unsigned_rule_is_not_a_signature(tmp_path):
    template = _pdf(tmp_path / "t.pdf")
    schema = FieldDetector().build_schema(template)
    signature, date = _signature_and_date(schema, PdfParser().extract(template, schema))
    assert signature["has_signature"] is False
    assert date["value"] is None and date["detected"] is False


def test_a_typed_name_on_the_rule_still_counts(tmp_path):
    template = _pdf(tmp_path / "t.pdf")
    contract = _pdf(tmp_path / "c.pdf", typed_name="Charles Frye", date_value="01/30/2026")
    schema = _schema_with_baseline(template)
    signature, date = _signature_and_date(schema, PdfParser().extract(contract, schema))
    assert signature["has_signature"] is True
    assert date["value"] == "01/30/2026" and date["detected"] is True


# -- template baseline ----------------------------------------------------------


def test_baseline_is_additive_and_flags_a_prefilled_template(tmp_path):
    signed_as_template = _pdf(tmp_path / "t.pdf", image_color="black", date_value="01/30/2026")
    plain = FieldDetector().build_schema(signed_as_template)
    schema = PdfParser().with_template_baseline(signed_as_template, plain)

    plain_fields = {f["field_id"]: f for p in plain["pages"] for f in p["fields"]}
    for page in schema["pages"]:
        for field in page["fields"]:
            original = plain_fields[field["field_id"]]
            # Every pre-existing key survives untouched; only the baseline is new.
            assert {k: field[k] for k in original} == original
            if field["field_type"] in ("signature", "date"):
                assert BASELINE_KEY in field
            else:
                assert BASELINE_KEY not in field
    assert PdfParser.baseline_summary(schema) == {"prefilled_signatures": 1, "prefilled_dates": 1}
    assert PdfParser.baseline_summary(plain) == {"prefilled_signatures": 0, "prefilled_dates": 0}


def test_a_signature_image_the_template_also_shows_is_still_signed(tmp_path):
    """Same bytes at the same slot in template and contract: the contract
    visibly carries a signature, so the slot is filled (decision 2026-09-17;
    previously this asserted False by subtracting the template's hashes)."""
    template = _pdf(tmp_path / "t.pdf", image_color="black")
    contract = _pdf(tmp_path / "c.pdf", image_color="black")
    schema = _schema_with_baseline(template)

    without_baseline = FieldDetector().build_schema(template)
    naive, _ = _signature_and_date(without_baseline, PdfParser().extract(contract, without_baseline))
    assert naive["has_signature"] is True

    # The baseline no longer changes the reading.
    signature, _ = _signature_and_date(schema, PdfParser().extract(contract, schema))
    assert signature["has_signature"] is True


def test_a_different_image_on_the_same_slot_is_a_signature(tmp_path):
    template = _pdf(tmp_path / "t.pdf", image_color="black")
    contract = _pdf(tmp_path / "c.pdf", image_color="navy")
    schema = _schema_with_baseline(template)
    signature, _ = _signature_and_date(schema, PdfParser().extract(contract, schema))
    assert signature["has_signature"] is True


def test_a_date_the_template_also_shows_is_still_filled(tmp_path):
    """Previously asserted None (template tokens were subtracted). A visible
    date is filled whatever the template file shows (decision 2026-09-17)."""
    template = _pdf(tmp_path / "t.pdf", date_value="01/30/2026")
    schema = _schema_with_baseline(template)

    inherited = _pdf(tmp_path / "same.pdf", date_value="01/30/2026")
    _, date = _signature_and_date(schema, PdfParser().extract(inherited, schema))
    assert date["value"] == "01/30/2026" and date["detected"] is True

    changed = _pdf(tmp_path / "other.pdf", date_value="02/14/2026")
    _, date = _signature_and_date(schema, PdfParser().extract(changed, schema))
    assert date["value"] == "02/14/2026"


# -- placeholders ---------------------------------------------------------------


def test_stripped_placeholders_are_blank_but_values_are_not():
    for placeholder in ("//", "/ /", "-", "...", "__", " / / "):
        assert v.is_blank(placeholder)
    for value in ("0", "01/30/2026", "N/A x", "a", "1-2"):
        assert not v.is_blank(value)


# -- through the API harness ----------------------------------------------------


def test_template_analysis_stores_the_baseline(api, admin_token, ready_template):
    from uuid import UUID

    from app.models.template import Template

    db = api.db_factory()
    try:
        template = db.get(Template, UUID(ready_template))
        assert not PdfParser.needs_template_baseline(template.field_schema)
    finally:
        db.close()


def test_a_legacy_schema_gets_its_baseline_when_a_contract_is_processed(
    api, admin_token, ready_template, uploaded_contract
):
    """Templates analysed before this change carry no baseline; the contract
    task derives it from the stored form and keeps it."""
    from uuid import UUID

    from app.models.contract import Contract
    from app.models.template import Template
    from app.workers import tasks as task_module

    db = api.db_factory()
    try:
        template = db.get(Template, UUID(ready_template))
        legacy = {
            "pages": [
                {**page, "fields": [{k: v for k, v in f.items() if k != BASELINE_KEY} for f in page["fields"]]}
                for page in template.field_schema["pages"]
            ],
            "field_count": template.field_schema["field_count"],
        }
        assert PdfParser.needs_template_baseline(legacy)
        template.field_schema = legacy
        db.commit()
    finally:
        db.close()

    # __wrapped__ already carries the bound task instance (see conftest).
    task_module.process_contract_task.__wrapped__(uploaded_contract)

    db = api.db_factory()
    try:
        template = db.get(Template, UUID(ready_template))
        assert not PdfParser.needs_template_baseline(template.field_schema)
        contract = db.get(Contract, UUID(uploaded_contract))
        assert contract.status == "analyzed"
        # The fixture contract is unsigned: the prose 12pt above the rule sits
        # outside the rule band (guard A), so it is not read as a typed name
        # and the slot is still reported as pending signing work.
        assert contract.extracted_data["p1_contractor_signature"].get("has_signature") is not True
        pending = {s["field_id"] for s in contract.comparison_result["signature_slots"]}
        assert "p1_contractor_signature" in pending
    finally:
        db.close()
