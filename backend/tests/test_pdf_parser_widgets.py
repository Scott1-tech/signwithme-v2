"""Reparse reads filled AcroForm widget values (services.pdf_parser).

A B3 `field.value` (or an uploaded fillable PDF) stores the value in the
widget, not in the page's text, so the page reading alone never saw it. A
widget whose box overlaps a template field now fills that field -- only when
the page text left it empty (precedence rule in pdf_parser). A document with
no widgets reads exactly as before.
"""
from __future__ import annotations

import io
import tempfile
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import TypeAdapter
from pypdf import PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.schemas.document import ContentOp
from app.services import pdf_engine
from app.services import pdf_parser as parser_module
from app.services.field_detector import FieldDetector
from app.services.geometry import BBox, displayed_to_unrotated
from app.services.pdf_engine.model import PageRef
from app.services.pdf_parser import WIDGET_SOURCE, PdfParser
from tests.conftest import _make_pdf, auth
from tests.editor_v2_helpers import commit, first_page_uid, put_draft, session_id, start_draft

_OP = TypeAdapter(ContentOp)
U1 = "pg_000000000001"
W, H = letter


def R(box: dict) -> dict:
    return {"x_pt": box["x"], "y_pt": box["y"], "w_pt": box["width"], "h_pt": box["height"]}


def add(name, ftype, rect, tab, **extra):
    return {"id": f"a_{name}_{tab}", "kind": "field.add", "page_uid": U1,
            "field": {"name": name, "type": ftype, "rect": rect, "tab_index": tab, **extra}}


def value(name, val):
    return {"id": f"v_{name}", "kind": "field.value", "name": name, "value": val}


def apply(src: bytes, ops: list[dict]) -> bytes:
    refs = [PageRef(U1, 1)]
    return pdf_engine.apply_ops(src, refs, [pdf_engine.from_model(_OP.validate_python(op)) for op in ops]).pdf_bytes


def _path(tmp_path: Path, data: bytes, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


@pytest.fixture
def blank_schema(tmp_path) -> dict:
    return FieldDetector().build_schema(_path(tmp_path, _make_pdf(False), "template.pdf"))


def _boxes(schema: dict) -> dict[str, dict]:
    return {f["field_id"]: f["bbox"] for page in schema["pages"] for f in page["fields"]}


def _extract(tmp_path, data: bytes, schema: dict, name="doc.pdf") -> dict:
    return PdfParser().extract(_path(tmp_path, data, name), schema)


def test_filled_widgets_fill_the_fields_they_sit_on(tmp_path, blank_schema):
    boxes = _boxes(blank_schema)
    ops = [
        add("email", "text", R(boxes["p1_email"]), 0),
        add("expiry", "text", R(boxes["p1_cdl_expiration"]), 1),
        add("llc", "checkbox", R(boxes["p1_llc"]), 2),
        add("untouched", "text", R(boxes["p1_ssn"]), 3),  # empty widget
        value("email", "driver@example.com"),
        value("expiry", "01/30/2030"),
        value("llc", True),
    ]
    filled = apply(_make_pdf(False), ops)
    before = _extract(tmp_path, _make_pdf(False), blank_schema, "blank.pdf")
    after = _extract(tmp_path, filled, blank_schema)

    assert before["p1_email"]["detected"] is False
    assert after["p1_email"] == {"value": "driver@example.com", "confidence": 1.0, "detected": True,
                                 "extra": {"source": WIDGET_SOURCE}}
    assert after["p1_cdl_expiration"]["value"] == "01/30/2030"
    assert after["p1_cdl_expiration"]["extra"] == {"source": WIDGET_SOURCE}
    assert after["p1_llc"]["checked"] is True and after["p1_llc"]["extra"] == {"source": WIDGET_SOURCE}
    # An empty widget, and every field with no widget, read exactly as before.
    for field_id in ("p1_ssn", "p1_full_name", "p1_cdl", "p1_individual", "p1_sole",
                     "p1_contractor_signature", "p1_date"):
        assert after[field_id] == before[field_id], field_id


def test_a_date_widget_must_hold_a_date(tmp_path, blank_schema):
    boxes = _boxes(blank_schema)
    filled = apply(_make_pdf(False), [add("expiry", "text", R(boxes["p1_cdl_expiration"]), 0),
                                      value("expiry", "not yet")])
    assert _extract(tmp_path, filled, blank_schema)["p1_cdl_expiration"]["detected"] is False


def test_page_text_wins_over_a_widget(tmp_path, blank_schema):
    """The filled contract already shows CHARLES FRYE on the rule; a widget
    over it with another value does not replace what the page says."""
    boxes = _boxes(blank_schema)
    base = _make_pdf(True)
    filled = apply(base, [add("name", "text", R(boxes["p1_full_name"]), 0), value("name", "SOMEONE ELSE")])
    before = _extract(tmp_path, base, blank_schema, "base.pdf")
    after = _extract(tmp_path, filled, blank_schema)
    assert after["p1_full_name"] == before["p1_full_name"]
    assert "SOMEONE" not in str(after["p1_full_name"]["value"])


def test_a_widget_only_fills_a_field_it_overlaps(tmp_path, blank_schema):
    boxes = _boxes(blank_schema)
    email = boxes["p1_email"]
    # Just touching the email box from below (1 pt of a 14 pt box) -> no.
    grazing = {"x": email["x"], "y": email["y"] + email["height"] - 1.0, "width": email["width"], "height": 14.0}
    filled = apply(_make_pdf(False), [add("near", "text", R(grazing), 0), value("near", "x@example.com")])
    result = _extract(tmp_path, filled, blank_schema)
    assert result["p1_email"]["detected"] is False


def test_a_signature_slot_never_takes_a_widget_value(tmp_path, blank_schema):
    boxes = _boxes(blank_schema)
    filled = apply(_make_pdf(False), [add("typed", "text", R(boxes["p1_contractor_signature"]), 0),
                                      value("typed", "Charles Frye")])
    assert _extract(tmp_path, filled, blank_schema)["p1_contractor_signature"]["has_signature"] is False


def test_a_document_without_widgets_reads_exactly_as_before(tmp_path, blank_schema, monkeypatch):
    data = _make_pdf(True)
    with_reader = _extract(tmp_path, data, blank_schema, "a.pdf")
    assert parser_module._form_widgets(str(tmp_path / "a.pdf")) == {}
    monkeypatch.setattr(parser_module, "_form_widgets", lambda path: {})
    assert _extract(tmp_path, data, blank_schema, "b.pdf") == with_reader


def test_hidden_and_signature_widgets_are_ignored(tmp_path, blank_schema):
    """Docusign packages carry a hidden /Sig field: not a value source."""
    boxes = _boxes(blank_schema)
    filled = apply(_make_pdf(False), [add("sig", "signature", R(boxes["p1_email"]), 0)])
    path = _path(tmp_path, filled, "sig.pdf")
    assert parser_module._form_widgets(path) == {}


# -- rotated page: the widget's /Rect is mapped to the displayed space -------------

MATRIX = {90: (0, 1, -1, 0, W, 0), 270: (0, -1, 1, 0, 0, H)}


def _rotated_form(rotation: int) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(W, H))
    pdf.transform(*MATRIX[rotation])
    displayed_height = W
    pdf.setFont("Helvetica", 11)
    pdf.drawString(72, displayed_height - 150, "Email:")
    pdf.drawString(200, displayed_height - 150, "_" * 30)
    pdf.showPage()
    pdf.save()
    writer = PdfWriter(clone_from=io.BytesIO(buffer.getvalue()))
    writer.pages[0].rotate(rotation)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.mark.parametrize("rotation", [90, 270])
def test_a_widget_on_a_rotated_page_fills_its_field(tmp_path, rotation):
    source = _rotated_form(rotation)
    schema = FieldDetector().build_schema(_path(tmp_path, source, "t.pdf"))
    email = _boxes(schema)["p1_email"]  # displayed (canonical) coordinates
    # The engine takes UNROTATED page coordinates (pdf_engine.adapter).
    corners = [displayed_to_unrotated(x, y, W, H, rotation)
               for x, y in ((email["x"], email["y"]),
                            (email["x"] + email["width"], email["y"] + email["height"]))]
    (ax, ay), (bx, by) = corners
    rect = {"x_pt": min(ax, bx), "y_pt": min(ay, by), "w_pt": abs(bx - ax), "h_pt": abs(by - ay)}
    filled = apply(source, [add("email", "text", rect, 0), value("email", "rot@example.com")])

    widgets = parser_module._form_widgets(_path(tmp_path, filled, "f.pdf"))
    box = widgets[1][0].box
    assert abs(box.x - email["x"]) < 0.01 and abs(box.y - email["y"]) < 0.01
    assert abs(box.width - email["width"]) < 0.01 and abs(box.height - email["height"]) < 0.01
    assert _extract(tmp_path, filled, schema)["p1_email"]["value"] == "rot@example.com"


# -- through the API: field.value -> commit -> reparse -> comparison ------------------


def test_reparse_after_field_value_puts_the_value_in_the_comparison(api, admin_token, contractor_token,
                                                                   ready_template, blank_template_bytes):
    from app.models.contract import Contract
    from app.models.template import Template

    uploaded = api.post(
        "/api/contracts",
        headers=auth(contractor_token),
        files={"file": ("contract.pdf", blank_template_bytes, "application/pdf")},
        data={"template_id": ready_template, "contractor_name": "Charles Frye"},
    )
    assert uploaded.status_code == 202, uploaded.text
    contract_id = uploaded.json()["contract_id"]

    db = api.db_factory()
    try:
        schema = db.get(Template, UUID(ready_template)).field_schema
        before = db.get(Contract, UUID(contract_id))
        assert before.extracted_data["p1_email"]["value"] is None
        missing_before = {m.get("field_id") for m in before.comparison_result["missing_required"]}
        filled_before = before.comparison_result["filled_count"]
    finally:
        db.close()

    uid = first_page_uid(api, contractor_token, contract_id)
    email = _boxes(schema)["p1_email"]
    ops = [
        {"id": "a_email", "kind": "field.add", "page_uid": uid,
         "field": {"name": "email", "type": "text", "rect": R(email), "tab_index": 0}},
        {"id": "v_email", "kind": "field.value", "name": "email", "value": "driver@example.com"},
    ]
    session = session_id()
    started = start_draft(api, contractor_token, contract_id, session)
    assert started.status_code in (200, 201), started.text
    saved = put_draft(api, contractor_token, contract_id, session, started.json()["revision"], ops)
    assert saved.status_code == 200, saved.text
    done = commit(api, contractor_token, contract_id, saved.json()["revision"])
    assert done.status_code == 201, done.text
    assert done.json()["content_changed"] is True

    db = api.db_factory()
    try:
        after = db.get(Contract, UUID(contract_id))
        assert after.extracted_data["p1_email"]["value"] == "driver@example.com"
        assert after.comparison_result["filled_count"] == filled_before + 1
        missing_after = {m.get("field_id") for m in after.comparison_result["missing_required"]}
        assert "p1_email" not in missing_after
        assert missing_after == missing_before - {"p1_email"}
    finally:
        db.close()
