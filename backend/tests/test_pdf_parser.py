from app.services.field_detector import FieldDetector
from app.services.pdf_parser import PdfParser, sha256_bytes


def test_reads_values_out_of_a_filled_contract(blank_template_path, filled_contract_path):
    schema = FieldDetector().build_schema(blank_template_path)
    extracted = PdfParser().extract(filled_contract_path, schema)

    assert "CHARLES FRYE" in (extracted["p1_full_name"]["value"] or "")
    assert "Rt305248" in (extracted["p1_cdl"]["value"] or "")
    assert extracted["p1_full_name"]["detected"] is True


def test_blank_contract_reports_nothing_filled(blank_template_path):
    schema = FieldDetector().build_schema(blank_template_path)
    extracted = PdfParser().extract(blank_template_path, schema)

    assert extracted["p1_full_name"]["value"] is None
    assert extracted["p1_full_name"]["detected"] is False


def test_ruled_underscores_are_not_mistaken_for_content(blank_template_path):
    """The fill line itself extracts as underscores; treating that as a value
    would mark every empty field as complete."""
    schema = FieldDetector().build_schema(blank_template_path)
    extracted = PdfParser().extract(blank_template_path, schema)
    values = [entry["value"] for entry in extracted.values() if entry.get("value")]
    assert not any(set(str(value)) == {"_"} for value in values)


def test_unsigned_signature_region_is_not_reported_as_signed(blank_template_path):
    schema = FieldDetector().build_schema(blank_template_path)
    extracted = PdfParser().extract(blank_template_path, schema)
    signature_ids = [
        field["field_id"]
        for page in schema["pages"]
        for field in page["fields"]
        if field["field_type"] == "signature"
    ]
    for field_id in signature_ids:
        assert extracted[field_id]["has_signature"] is False


def test_hash_is_stable():
    assert sha256_bytes(b"abc") == sha256_bytes(b"abc")
    assert sha256_bytes(b"abc") != sha256_bytes(b"abd")


def test_a_digital_pdf_is_not_flagged_for_ocr(filled_contract_path):
    report = PdfParser().analyze_text_layer(filled_contract_path)
    assert report.needs_ocr is False
    assert report.pages_without_text == []


def test_a_scanned_pdf_is_identified_rather_than_reported_as_empty(tmp_path):
    """A scan extracts no text. Without this check every field reads as missing,
    which looks like a broken comparison instead of a file that needs OCR."""
    import io

    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    picture = Image.new("RGB", (1200, 1600), "white")
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")

    path = tmp_path / "scan.pdf"
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.drawImage(ImageReader(io.BytesIO(buffer.getvalue())), 0, 0, width=612, height=792)
    pdf.showPage()
    pdf.save()

    report = PdfParser().analyze_text_layer(str(path))
    assert report.needs_ocr is True
    assert "OCR" in report.message
