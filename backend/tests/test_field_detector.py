from app.services.field_detector import FieldDetector
from app.services.geometry import BBox


def test_detects_the_labelled_text_fields(blank_template_path):
    schema = FieldDetector().build_schema(blank_template_path)
    ids = {f["field_id"] for page in schema["pages"] for f in page["fields"]}
    assert "p1_full_name" in ids
    assert "p1_cdl" in ids
    assert "p1_email" in ids


def test_bboxes_are_real_coordinates_not_placeholders(blank_template_path):
    """Every downstream feature -- highlighting and signature overlay alike --
    depends on these being derived from actual word positions."""
    schema = FieldDetector().build_schema(blank_template_path)
    fields = [f for page in schema["pages"] for f in page["fields"]]
    assert fields
    for field in fields:
        box = BBox.from_dict(field["bbox"])
        assert box.width > 0 and box.height > 0
        assert not (box.x == 0 and box.y == 0 and box.width == 100 and box.height == 20)

    name = next(f for f in fields if f["field_id"] == "p1_full_name")
    box = BBox.from_dict(name["bbox"])
    # The fill region follows the label, which is drawn at x=72.
    assert box.x > 72


def test_signature_and_date_are_typed_and_paired(blank_template_path):
    schema = FieldDetector().build_schema(blank_template_path)
    fields = {f["field_id"]: f for page in schema["pages"] for f in page["fields"]}
    signature = next(f for f in fields.values() if f["field_type"] == "signature")
    assert signature["paired_date_field_id"] is not None
    assert fields[signature["paired_date_field_id"]]["field_type"] == "date"


def test_vector_square_checkboxes_are_detected(blank_template_path):
    """Base-14 fonts carry no U+2610 glyph, so many templates draw checkboxes as
    rectangles. Matching only the ballot-box character would miss them, and a
    missed checkbox is a missed required field."""
    schema = FieldDetector().build_schema(blank_template_path)
    boxes = [
        f
        for page in schema["pages"]
        for f in page["fields"]
        if f["field_type"] == "checkbox" and f["detector"] == "checkbox_rect"
    ]
    assert len(boxes) >= 3
    labels = {f["label"] for f in boxes}
    assert "Individual" in labels and "LLC" in labels


def test_ballot_box_glyphs_form_a_checkbox_group():
    """The glyph path, exercised directly: templates that do emit U+2610 should
    produce one group with every option, not three loose checkboxes."""
    from app.services.field_detector import _Line, _Word
    from app.services.geometry import BBox

    texts = ["W-9", "Status:", "\u2610", "Individual", "\u2610", "LLC"]
    words = [
        _Word(text=text, bbox=BBox(x=72.0 + index * 40, y=560.0, width=30.0, height=10.0))
        for index, text in enumerate(texts)
    ]
    line = _Line(words=words)
    cursor = 0
    for index, word in enumerate(words):
        line.offsets.append((cursor, cursor + len(word.text), index))
        cursor += len(word.text) + 1
    line.text = " ".join(texts)

    fields = FieldDetector()._detect_checkboxes(line, page_number=1, counters={})
    assert len(fields) == 1
    group = fields[0]
    assert group.field_type == "checkbox_group"
    assert [option["label"] for option in group.options] == ["Individual", "LLC"]
    assert group.validation_rule == "at_least_one"


def test_repeated_labels_do_not_collapse_into_one_field():
    """'Date:' appears beside every signature. Suffixing keeps the contractor
    and company-rep dates distinct instead of deduplicating them away."""
    detector = FieldDetector()
    counters: dict[str, int] = {}
    first = detector._unique_id(1, "date", counters)
    second = detector._unique_id(1, "date", counters)
    assert first == "p1_date"
    assert second == "p1_date_2"


def test_pages_carry_their_dimensions(blank_template_path):
    schema = FieldDetector().build_schema(blank_template_path)
    page = schema["pages"][0]
    assert round(page["width"]) == 612 and round(page["height"]) == 792


# --------------------------------------------------------------------------
# Fill lines in a PRE-FILLED template. A production package uploaded as the
# form carries its own values printed over the underscore rules. pdfplumber
# then merges each '_' with the value glyph above it ("_U", "_t", "_ah") and
# parks those words on another baseline bucket, so the label's own line reads
# "State: _ ____" and the first `_{2,}` run is only the tail of the rule. The
# resulting box missed a contractor's value written at the start of the line.
# --------------------------------------------------------------------------

UNDERSCORE_W = 12 * 0.556  # Helvetica '_' advance at 12pt


def _write_form_pdf(path, *, overlay_value: bool) -> str:
    """One page, three rows. Row 1 is the case under test; rows 2-3 are the
    controls that must not move."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.setFont("Helvetica", 12)
    # Row 1: "State: ________ Exp: __/__/____"
    pdf.drawString(72, 700, "State:")
    pdf.drawString(110, 700, "_" * 8)
    pdf.drawString(170, 700, "Exp:")
    pdf.drawString(200, 700, "__/__/____")
    # Row 2: three fields on one line.
    # (Each rule starts >3pt after its label, or pdfplumber glues "Phone:_____"
    # into one word -- a separate, pre-existing word-granularity effect.)
    pdf.drawString(72, 660, "Email:")
    pdf.drawString(110, 660, "_" * 12)
    pdf.drawString(200, 660, "Phone:")
    pdf.drawString(246, 660, "_" * 5)
    pdf.drawString(290, 660, "SSN:")
    pdf.drawString(322, 660, "_" * 9)
    # Row 3: a plain blank rule.
    pdf.drawString(72, 620, "Phone:")
    pdf.drawString(120, 620, "_" * 20)
    if overlay_value:
        # The template's own value, 9pt, raised ~5.6pt so its top sits 3.2pt
        # above the rule's -- the geometry measured in the production package.
        pdf.setFont("Helvetica", 9)
        pdf.drawString(112, 705.6, "Utah")
        # A 10pt marker on the same row (a form number in the margin) bridges
        # pdfplumber's line clustering, exactly as the package's mixed sizes do,
        # so the merged "_U _ta _h" words land in a different 3pt bucket from
        # "State:". Without it reportlab output never reproduces the split.
        pdf.setFont("Helvetica", 10)
        pdf.drawString(520, 703, "Form 1-A")
    pdf.showPage()
    pdf.save()
    return str(path)


def _boxes(schema) -> dict[str, BBox]:
    return {f["field_id"]: BBox.from_dict(f["bbox"]) for page in schema["pages"] for f in page["fields"]}


def test_a_rule_split_by_a_printed_value_is_measured_end_to_end(tmp_path):
    from app.services.field_detector import _Line

    path = _write_form_pdf(tmp_path / "prefilled.pdf", overlay_value=True)
    # Precondition: the overlay really did split the label's line text, so the
    # regex path alone sees only the tail of the rule (the bug being fixed).
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        lines: list[_Line] = FieldDetector()._build_lines(pdf.pages[0])
    state_line = next(line.text for line in lines if line.text.startswith("State:"))
    assert "________" not in state_line, state_line

    box = _boxes(FieldDetector().build_schema(path))["p1_state"]
    assert box.x <= 110.0 + 0.5
    assert box.x1 >= 110.0 + 8 * UNDERSCORE_W - 0.5
    # ...and it stops at the next label: "Exp:" starts at x=170.
    assert box.x1 < 170.0


def test_three_fields_on_one_line_stay_three_boxes(tmp_path):
    """Widening walks over abutting '_' glyphs only; the space before the next
    label is wider than that, so neighbouring fills never merge."""
    path = _write_form_pdf(tmp_path / "prefilled.pdf", overlay_value=True)
    boxes = _boxes(FieldDetector().build_schema(path))
    expected = {"p1_email": (110.0, 12), "p1_phone": (246.0, 5), "p1_ssn": (322.0, 9)}
    for field_id, (x, count) in expected.items():
        box = boxes[field_id]
        assert abs(box.x - x) < 0.5, field_id
        assert abs(box.x1 - (x + count * UNDERSCORE_W)) < 0.5, field_id
    # One detector-level line, three boxes with no overlap between them.
    assert boxes["p1_email"].x1 < boxes["p1_phone"].x < boxes["p1_phone"].x1 < boxes["p1_ssn"].x


def test_a_blank_form_rule_keeps_its_box(tmp_path):
    """Same page without the overlay: every box is byte-identical to what a
    detector without glyph widening produces -- widening only ever acts on a
    rule a value has split."""
    path = _write_form_pdf(tmp_path / "blank.pdf", overlay_value=False)
    detector = FieldDetector()
    with_glyphs = _boxes(detector.build_schema(path))

    # Disable widening and detect again: identical output on a blank form.
    detector._underscore_glyphs = lambda page: []  # type: ignore[method-assign]
    without_glyphs = _boxes(detector.build_schema(path))

    assert set(with_glyphs) == set(without_glyphs) >= {"p1_state", "p1_email", "p1_phone", "p1_ssn", "p1_cdl_expiration"}
    for field_id, box in with_glyphs.items():
        assert box == without_glyphs[field_id], field_id
    phone = with_glyphs["p1_phone_2"]
    assert abs(phone.x - 120.0) < 0.5 and abs(phone.x1 - (120.0 + 20 * UNDERSCORE_W)) < 0.5
