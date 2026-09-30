"""Contract v6: embedded fonts (Liberation, SIL OFL 1.1) and bold/italic.

What these tests hold onto:

* Schema: the three embedded families are accepted wherever a font is
  (`text`/`date` annotations, `layout.date_font`); `bold`/`italic` (and
  `layout.date_bold`/`date_italic`) are STRICT booleans; an unknown family, a
  face name used as a family, and any unknown key are 422.
* Byte for byte: a v5 body -- no `bold`/`italic`, or both false -- builds the
  v5 PDF. Proved against the v5 face-selection logic patched back in, and
  end to end through finalize.
* Embedded faces are checked against their own TrueType cmap: no glyph ->
  422 naming the character and its position; control/format characters,
  combining marks and right-to-left letters too. Standard-14 faces keep the
  WinAnsi rule and its message.
* Cyrillic, Uzbek `oʻ`/`gʻ` (U+02BB), `ş`, `ç`, `€` print in all 12 faces; a
  pdfium render (8 px/pt) shows no `.notdef` box in place of any of them.
* The font is really in the file: `/TrueType`, `/FontFile2`, `ABCDEF+` subset
  name, and a font program far smaller than the TTF.
* Metrics: Liberation Sans/Serif/Mono are within 1 % of Helvetica/Times/Courier
  (actually <= 0.02 %) on a real line, per face.
"""
import io
import re

import pytest
from PIL import Image, ImageChops
from pydantic import ValidationError as PydanticValidationError

from tests.conftest import auth
from tests.test_api_annotations import (  # noqa: F401 - fixtures
    _contract,
    _final_pdf,
    _finalize,
    _ready,
    _timeless,
    signature_data_url,
)
from tests.test_api_annotations_v5 import V4_BODY, _v4_source

SCALE = 8.0  # px per pt
DATE = "2026-09-24"
FAMILIES = ["Helvetica", "Times-Roman", "Courier", "LiberationSans", "LiberationSerif", "LiberationMono"]
EMBEDDED = ["LiberationSans", "LiberationSerif", "LiberationMono"]
STYLES = [(False, False), (True, False), (False, True), (True, True)]
FACES = {
    "Helvetica": ["Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"],
    "Times-Roman": ["Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"],
    "Courier": ["Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique"],
    "LiberationSans": ["LiberationSans", "LiberationSans-Bold", "LiberationSans-Italic",
                       "LiberationSans-BoldItalic"],
    "LiberationSerif": ["LiberationSerif", "LiberationSerif-Bold", "LiberationSerif-Italic",
                        "LiberationSerif-BoldItalic"],
    "LiberationMono": ["LiberationMono", "LiberationMono-Bold", "LiberationMono-Italic",
                       "LiberationMono-BoldItalic"],
}
# Cyrillic (Russian + the Uzbek Cyrillic letters), Uzbek Latin with the
# modifier letter turned comma U+02BB, Turkish, the euro.
WORLD_TEXT = "Привет, Oʻzbekiston! Gʻalaba. Ўў Ққ Ғғ Ҳҳ şç ĞİŞ €"
METRIC_LINE = "Independent Contractor Agreement 0123456789"


def _style_index(bold: bool, italic: bool) -> int:
    return (2 if italic else 0) + (1 if bold else 0)


def _text(**overrides) -> dict:
    note = {"id": "t1", "kind": "text", "page": 1, "x_pt": 72.0, "y_pt": 600.0,
            "text": "Independent Contractor Agreement"}
    note.update(overrides)
    return note


def _blank(width: float = 612.0, height: float = 792.0) -> bytes:
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _render(pdf_bytes: bytes) -> Image.Image:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    try:
        page = document[0]
        bitmap = page.render(scale=SCALE)
        try:
            image = bitmap.to_pil().convert("L")
        finally:
            bitmap.close()
        page.close()
    finally:
        document.close()
    return image


def _cell(image: Image.Image, x_pt: float, y_pt: float, w_pt: float, h_pt: float) -> Image.Image:
    return image.crop((int(x_pt * SCALE), int(y_pt * SCALE),
                       int((x_pt + w_pt) * SCALE), int((y_pt + h_pt) * SCALE)))


def _ink(image: Image.Image) -> int:
    return image.point(lambda v: 255 if v < 128 else 0).histogram()[255]


def _placements(notes):
    from app.services.pdf_builder import AnnotationPlacement

    return [AnnotationPlacement(**{k: v for k, v in n.items() if k != "text" or v is not None})
            for n in notes]


def _page_fonts(pdf_bytes: bytes) -> dict[str, dict]:
    """BaseFont -> the font dictionary, for every font on page 1."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    fonts = reader.pages[0]["/Resources"]["/Font"]
    return {str(font.get_object()["/BaseFont"]): font.get_object() for font in fonts.values()}


def _detail_messages(response) -> str:
    return " | ".join(entry["msg"] for entry in response.json()["detail"])


# -- schema ------------------------------------------------------------------------------------


@pytest.mark.parametrize("font", FAMILIES)
@pytest.mark.parametrize("bold,italic", STYLES)
def test_every_family_and_style_is_accepted(font, bold, italic):
    from app.schemas.contract import AnnotationIn, FinalizeLayout

    note = AnnotationIn.model_validate(_text(font=font, bold=bold, italic=italic))
    assert (note.font, note.bold, note.italic) == (font, bold, italic)
    layout = FinalizeLayout.model_validate({"date_font": font, "date_bold": bold, "date_italic": italic})
    assert (layout.date_font, layout.date_bold, layout.date_italic) == (font, bold, italic)


def test_bold_and_italic_default_to_false():
    from app.schemas.contract import AnnotationIn, FinalizeLayout

    note = AnnotationIn.model_validate(_text())
    assert (note.font, note.bold, note.italic) == ("Helvetica", False, False)
    layout = FinalizeLayout()
    assert (layout.date_bold, layout.date_italic) == (False, False)


@pytest.mark.parametrize("font", [
    "Arial", "liberationsans", "Liberation Sans", "LiberationSans-Bold", "Helvetica-Bold",
    "Times", "", None, 1,
])
def test_an_unknown_family_is_refused(font):
    from app.schemas.contract import AnnotationIn, FinalizeLayout

    with pytest.raises(PydanticValidationError):
        AnnotationIn.model_validate(_text(font=font))
    with pytest.raises(PydanticValidationError):
        FinalizeLayout.model_validate({"date_font": font})


@pytest.mark.parametrize("key", ["bold", "italic"])
@pytest.mark.parametrize("value", ["true", "false", 1, 0, None, "yes", [], {}])
def test_bold_and_italic_are_strict_booleans(key, value):
    from app.schemas.contract import AnnotationIn, FinalizeLayout

    with pytest.raises(PydanticValidationError):
        AnnotationIn.model_validate(_text(**{key: value}))
    with pytest.raises(PydanticValidationError):
        FinalizeLayout.model_validate({f"date_{key}": value})


@pytest.mark.parametrize("extra", [{"weight": "bold"}, {"font_style": "italic"},
                                   {"underline": True}, {"font_family": "LiberationSans"}])
def test_an_unknown_style_key_is_refused(extra):
    from app.schemas.contract import AnnotationIn, FinalizeLayout

    with pytest.raises(PydanticValidationError, match="Extra inputs are not permitted"):
        AnnotationIn.model_validate(_text(**extra))
    with pytest.raises(PydanticValidationError, match="Extra inputs are not permitted"):
        FinalizeLayout.model_validate({f"date_{next(iter(extra))}": True})


@pytest.mark.parametrize("kind", ["check", "cross"])
@pytest.mark.parametrize("style", [{"bold": True}, {"italic": True}])
def test_a_mark_takes_no_bold_or_italic(kind, style):
    from app.schemas.contract import AnnotationIn

    with pytest.raises(PydanticValidationError, match="not set in a font"):
        AnnotationIn.model_validate({"id": "m", "kind": kind, "page": 1, "x_pt": 100.0,
                                     "y_pt": 100.0, "size_pt": 12.0, **style})
    # Explicit false is not input that gets thrown away: it is the default.
    AnnotationIn.model_validate({"id": "m", "kind": kind, "page": 1, "x_pt": 100.0,
                                 "y_pt": 100.0, "bold": False, "italic": False})


@pytest.mark.parametrize("body", [
    {"annotations": [_text(font="LiberationSans", bold="true")]},
    {"annotations": [_text(font="Arial")]},
    {"annotations": [_text(font="LiberationSans", weight="bold")]},
    {"layout": {"date_font": "LiberationSerif", "date_bold": 1}},
    {"layout": {"date_font": "Comic Sans"}},
    {"layout": {"date_weight": "bold"}},
])
def test_a_bad_font_body_is_422_and_nothing_is_finalised(
    api, contractor_token, uploaded_contract, signature_data_url, body
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract, contract_date=DATE, **body)
    assert response.status_code == 422, response.text
    contract = _contract(api, uploaded_contract)
    assert contract.status != "completed"
    assert contract.final_file_path is None


# -- glyph coverage: the cmap, not WinAnsi ------------------------------------------------------


@pytest.mark.parametrize("font", EMBEDDED)
@pytest.mark.parametrize("bold,italic", STYLES)
def test_every_embedded_face_has_the_world_text(font, bold, italic):
    from app.services.pdf_builder import undrawable_character

    assert undrawable_character(WORLD_TEXT, font, bold, italic) is None
    # ...which the standard 14 do not: the premise of the whole feature.
    base = {"LiberationSans": "Helvetica", "LiberationSerif": "Times-Roman",
            "LiberationMono": "Courier"}[font]
    assert undrawable_character(WORLD_TEXT, base, bold, italic) == (0, "П")


@pytest.mark.parametrize("text,index,char,reason", [
    ("ok \U0001F600", 3, "\U0001F600", "the font has no glyph for it"),
    ("你好", 0, "你", "the font has no glyph for it"),
    ("tick ✓", 5, "✓", "the font has no glyph for it"),
    ("abc‮def", 3, "‮", "control, format and private-use characters are not printed"),
    ("a​b", 1, "​", "control, format and private-use characters are not printed"),
    ("a\tb", 1, "\t", "control, format and private-use characters are not printed"),
    ("é", 1, "́", "combining marks are not supported"),
    ("shalom ש", 7, "ש", "right-to-left text is not supported"),
])
def test_an_embedded_face_refuses_what_it_cannot_print(text, index, char, reason):
    from app.services.pdf_builder import undrawable_character, undrawable_reason

    for bold, italic in STYLES:
        assert undrawable_character(text, "LiberationSans", bold, italic) == (index, char)
        assert undrawable_reason(char, "LiberationSans", bold, italic).startswith(reason)


def test_the_cmap_422_names_the_face_the_character_and_its_position(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract, contract_date=DATE,
                         annotations=[_text(font="LiberationSerif", bold=True,
                                            text="Oʻzbek \U0001F600")])
    assert response.status_code == 422, response.text
    message = _detail_messages(response)
    assert "LiberationSerif-Bold cannot print" in message
    assert "U+1F600" in message and "at position 7" in message
    assert "the font has no glyph for it" in message
    assert _contract(api, uploaded_contract).status != "completed"


def test_a_standard_face_in_bold_keeps_the_winansi_rule_and_message():
    from app.schemas.contract import AnnotationIn

    with pytest.raises(PydanticValidationError) as caught:
        AnnotationIn.model_validate(_text(font="Helvetica", bold=True, italic=True, text="Привет"))
    message = str(caught.value)
    assert "Helvetica cannot print 'П' (U+041F) at position 0" in message
    assert "the standard PDF fonts cover Western European text only" in message
    # And WinAnsi text is fine in every standard face.
    for font in ("Helvetica", "Times-Roman", "Courier"):
        for bold, italic in STYLES:
            AnnotationIn.model_validate(_text(font=font, bold=bold, italic=italic,
                                              text="Ünïcodé € “quoted” – dash"))


# -- byte for byte -----------------------------------------------------------------------------


def _build(notes, layout=None, dates=(), source=None) -> bytes:
    from app.services.pdf_builder import PdfBuilder

    return _timeless(PdfBuilder().build_final_contract(
        source or _v4_source(), [], list(dates), layout=layout,
        annotations=_placements(notes)).pdf_bytes)


def _slot_date():
    from app.services.geometry import BBox
    from app.services.pdf_builder import DatePlacement

    return DatePlacement(page=1, bbox=BBox(400, 480, 100, 14), value="09/24/2026",
                         field_id="p1_date", rule_y=500.0)


def test_a_v5_body_builds_the_v5_bytes(monkeypatch):
    """`bold`/`italic` absent, or explicitly false: identical bytes. And the v6
    face resolution, replaced by the v5 selection it superseded
    (`font if font in the three else Helvetica`), changes nothing either."""
    from app.services import pdf_builder
    from app.services.fonts import register_embedded_fonts
    from app.services.pdf_builder import Layout

    body = [dict(note) for note in V4_BODY] + [
        _text(id="t2", font="Times-Roman", y_pt=660.0, color="blue"),
    ]
    explicit = [{**note, "bold": False, "italic": False} if note["kind"] in ("text", "date") else note
                for note in body]
    layouts = [Layout(date_font="Courier", date_font_size=13.0),
               Layout(date_font="Courier", date_font_size=13.0, date_bold=False, date_italic=False)]
    plain = _build(body, layouts[0], [_slot_date()])
    assert _build(explicit, layouts[1], [_slot_date()]) == plain

    # Registering the embedded faces does not touch a standard-14 document.
    register_embedded_fonts()
    assert _build(body, layouts[0], [_slot_date()]) == plain

    # Premise: the style really reaches the bytes (the equality is not vacuous).
    bold = _build([{**body[0], "bold": True}], layouts[0], [_slot_date()])
    assert bold != _build([body[0]], layouts[0], [_slot_date()])
    assert b"/Helvetica-Bold" in bold

    v5 = ("Helvetica", "Times-Roman", "Courier")
    monkeypatch.setattr(pdf_builder.AnnotationPlacement, "face",
                        property(lambda self: self.font if self.font in v5 else "Helvetica"))
    monkeypatch.setattr(pdf_builder.Layout, "date_face",
                        property(lambda self: self.date_font if self.date_font in v5 else "Helvetica"))
    assert _build(body, layouts[0], [_slot_date()]) == plain


def test_a_v5_body_with_explicit_false_through_the_api_matches_the_direct_build(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url
):
    from app.models.signature import Signature
    from app.models.template import Template
    from app.services.comparison_engine import _index_schema
    from app.services.geometry import BBox
    from app.services.pdf_builder import DatePlacement, Layout, PdfBuilder, SignaturePlacement

    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    notes = [{**n, "bold": False, "italic": False} if n["kind"] in ("text", "date") else n
             for n in V4_BODY]
    layout = {"date_font": "Times-Roman", "date_bold": False, "date_italic": False}
    response = _finalize(api, contractor_token, uploaded_contract, contract_date=DATE,
                         annotations=notes, layout=layout)
    assert response.status_code == 200, response.text
    through_api = _final_pdf(api, contractor_token, uploaded_contract)

    db = api.db_factory()
    try:
        contract = _contract(api, uploaded_contract)
        index = _index_schema(db.get(Template, contract.template_id).field_schema)
        rows = list(db.query(Signature).filter(Signature.contract_id == contract.id))
        placements = [SignaturePlacement(page=r.page_number, bbox=BBox.from_dict(r.bbox),
                                         image_bytes=api.objects[r.signature_image_path],
                                         field_id=r.field_id, rule_y=index[r.field_id]["rule_y"])
                      for r in rows]
        paired = index["p1_contractor_signature"]["paired_date_field_id"]
        dates = [DatePlacement(page=int(index[paired]["page"]),
                               bbox=BBox.from_dict(index[paired]["bbox"]), value="09/24/2026",
                               field_id=paired, rule_y=index[paired]["rule_y"])]
    finally:
        db.close()
    direct = PdfBuilder().build_final_contract(
        api.objects[contract.file_path], placements, dates,
        title=f"Signed Contract - {contract.contractor_name or contract.id}",
        layout=Layout(date_font="Times-Roman"),
        annotations=_placements(V4_BODY),
    ).pdf_bytes
    assert _timeless(direct) == _timeless(through_api)

    # The audit record of a v5 layout is the v5 record: no date_bold/date_italic.
    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    recorded = next(e for e in trail if e["action"] == "contract_finalized")["details"]["layout"]
    assert "date_bold" not in recorded and "date_italic" not in recorded
    assert recorded["date_font"] == "Times-Roman"


# -- drawing: the right face, really embedded --------------------------------------------------


@pytest.mark.parametrize("font", FAMILIES)
def test_each_family_draws_its_four_faces(font):
    """pdfplumber reads the face back from the content stream; a bold line
    carries more ink than the regular one, an italic line is not the regular
    line (slanted)."""
    import pdfplumber

    notes = [_text(id=f"s{i}", font=font, bold=b, italic=it, y_pt=100.0 + 40.0 * i, size_pt=16.0)
             for i, (b, it) in enumerate(STYLES)]
    pdf = _build(notes, source=_blank())
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        chars = document.pages[0].chars
    for i, face in enumerate(FACES[font]):
        baseline = 100.0 + 40.0 * i
        row = [c for c in chars if baseline - 20.0 < float(c["top"]) < baseline]
        assert "".join(c["text"] for c in row) == "Independent Contractor Agreement"
        names = {re.sub(r"^[A-Z]{6}\+", "", c["fontname"]) for c in row}
        assert names == {face}, (face, names)

    image = _render(pdf)
    cells = [_cell(image, 70.0, 100.0 + 40.0 * i - 18.0, 330.0, 24.0) for i in range(4)]
    regular, bold, italic, _ = cells
    assert _ink(bold) > _ink(regular) * 1.15, "bold is heavier"
    difference = ImageChops.difference(regular, italic).point(lambda v: 255 if v > 64 else 0)
    assert difference.histogram()[255] > _ink(regular) * 0.3, "italic is not the regular glyphs"


@pytest.mark.parametrize("font", EMBEDDED)
def test_an_embedded_face_is_a_subset_font_program_in_the_file(font):
    from app.services.fonts import EMBEDDED_FONT_FILES

    pdf = _build([_text(id=f"s{i}", font=font, bold=b, italic=it, y_pt=100.0 + 30.0 * i,
                        text="Привет Oʻzbekiston €")
                  for i, (b, it) in enumerate(STYLES)])
    fonts = _page_fonts(pdf)
    for face in FACES[font]:
        name = next((n for n in fonts if re.fullmatch(rf"/[A-Z]{{6}}\+{re.escape(face)}", n)), None)
        assert name is not None, (face, sorted(fonts))
        font_dict = fonts[name]
        assert font_dict["/Subtype"] == "/TrueType"
        assert "/ToUnicode" in font_dict, "copy/paste and search get the real text"
        program = font_dict["/FontDescriptor"].get_object()["/FontFile2"].get_object()
        size = len(program.get_data())
        # A subset: a fraction of the 280-420 KB file it was cut from.
        assert size < EMBEDDED_FONT_FILES[face].stat().st_size / 4, (face, size)


def test_the_world_text_is_extracted_back_verbatim():
    from pypdf import PdfReader

    pdf = _build([_text(font="LiberationSans", text=WORLD_TEXT)])
    assert WORLD_TEXT in PdfReader(io.BytesIO(pdf)).pages[0].extract_text()


def test_no_character_of_the_world_text_is_a_notdef_box():
    """One small page, one row per face. Every row starts with a cell drawn
    with a character the font does NOT have (handed straight to the builder,
    past the schema): that is the face's .notdef glyph, the box a missing
    glyph turns into. Every other cell holds one character of WORLD_TEXT, and
    has to have ink AND differ from that .notdef cell. Cells sit on whole
    points, i.e. whole pixels at 8 px/pt, so identical glyphs rasterise
    identically.

    Liberation's .notdef is a box in Sans Regular/Bold and all of Mono, and
    EMPTY (no contours) in Sans Italic/BoldItalic and all of Serif: there a
    missing glyph would not even show as a box, it would silently vanish --
    which is why the cmap check refuses it before anything is drawn."""
    from app.services.pdf_builder import AnnotationPlacement, PdfBuilder

    chars = sorted({c for c in WORLD_TEXT if not c.isspace()})
    cell, size = 24.0, 16.0
    faces = [(font, b, it) for font in EMBEDDED for b, it in STYLES]
    width, height = cell * (len(chars) + 2), cell * (len(faces) + 1)
    notes = []
    for row, (font, b, it) in enumerate(faces):
        baseline = cell * (row + 1)
        for col, char in enumerate(["\U0001F600"] + chars):
            notes.append(AnnotationPlacement(
                id=f"r{row}c{col}", kind="text", page=1, x_pt=cell * (col + 0.5), y_pt=baseline,
                text=char, font=font, bold=b, italic=it, size_pt=size))
    pdf = PdfBuilder().build_final_contract(_blank(width, height), [], [], annotations=notes).pdf_bytes
    image = _render(pdf)

    boxed = {("LiberationSans", False, False), ("LiberationSans", True, False),
             *[("LiberationMono", b, it) for b, it in STYLES]}
    for row, face in enumerate(faces):
        top = cell * (row + 1) - size
        notdef = _cell(image, cell * 0.5 - 2, top, cell, size + 5)
        # The reference really is .notdef: a box, or nothing at all.
        assert (_ink(notdef) > 0) == (face in boxed), face
        for col, char in enumerate(chars, start=1):
            glyph = _cell(image, cell * (col + 0.5) - 2, top, cell, size + 5)
            assert _ink(glyph) > 0, (face, char, "no ink")
            changed = ImageChops.difference(glyph, notdef).point(lambda v: 255 if v > 64 else 0)
            assert changed.histogram()[255] > 20, (face, char, "drew the .notdef box")


# -- metrics --------------------------------------------------------------------------------------

METRIC_PAIRS = [(FACES[base][i], FACES[lib][i])
                for base, lib in (("Helvetica", "LiberationSans"), ("Times-Roman", "LiberationSerif"),
                                  ("Courier", "LiberationMono"))
                for i in range(4)]


@pytest.mark.parametrize("standard,embedded", METRIC_PAIRS)
def test_liberation_is_metric_compatible_with_the_standard_face(standard, embedded):
    from reportlab.pdfbase.pdfmetrics import stringWidth

    from app.services.fonts import ensure_font

    ensure_font(embedded)
    ours, theirs = stringWidth(METRIC_LINE, embedded, 11.0), stringWidth(METRIC_LINE, standard, 11.0)
    assert abs(ours - theirs) / theirs <= 0.01, (standard, embedded, ours, theirs)
    # Every printable ASCII character, one by one: within 1/1000 em.
    for code in range(32, 127):
        char = chr(code)
        assert abs(stringWidth(char, embedded, 1000.0) - stringWidth(char, standard, 1000.0)) <= 1.0, char


# -- slot dates -----------------------------------------------------------------------------------


@pytest.mark.parametrize("font", FAMILIES)
@pytest.mark.parametrize("bold,italic", STYLES)
def test_the_slot_date_is_set_in_the_layout_face(font, bold, italic):
    import pdfplumber

    from app.services.pdf_builder import Layout

    pdf = _build([], Layout(date_font=font, date_bold=bold, date_italic=italic), [_slot_date()],
                 source=_blank())
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        chars = [c for c in document.pages[0].chars
                 if float(c["x0"]) >= 399.0 and c["text"] in "0123456789/"]
    assert "".join(c["text"] for c in sorted(chars, key=lambda c: float(c["x0"]))) == "09/24/2026"
    names = {re.sub(r"^[A-Z]{6}\+", "", c["fontname"]) for c in chars}
    assert names == {FACES[font][_style_index(bold, italic)]}


def test_a_slot_date_face_through_the_api_is_drawn_and_audited(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url
):
    import pdfplumber

    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    layout = {"date_font": "LiberationMono", "date_italic": True, "date_font_size": 12.0}
    response = _finalize(api, contractor_token, uploaded_contract, contract_date=DATE, layout=layout,
                         annotations=[_text(font="LiberationSerif", bold=True, text=WORLD_TEXT)])
    assert response.status_code == 200, response.text
    pdf = _final_pdf(api, contractor_token, uploaded_contract)
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        names = {re.sub(r"^[A-Z]{6}\+", "", c["fontname"]) for c in document.pages[0].chars}
    assert {"LiberationMono-Italic", "LiberationSerif-Bold"} <= names

    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    details = next(e for e in trail if e["action"] == "contract_finalized")["details"]
    assert details["layout"]["date_font"] == "LiberationMono"
    assert details["layout"]["date_italic"] is True
    assert "date_bold" not in details["layout"], "false is not recorded (v5 record shape)"
    # The audit rule is unchanged: counts only, never the text.
    assert details["annotations"] == {"total": 1, "by_kind": {"text": 1}}
    assert "Oʻzbekiston" not in str(trail) and "Привет" not in str(trail)


# -- editor v2 B2: the font registry, new families, GET /api/fonts ------------------------------

B2_FAMILIES = ["Arimo", "Carlito", "Caladea", "DejaVuSans", "DejaVuSerif", "Amiri"]


def test_get_fonts_lists_the_registry_in_order(api, contractor_token):
    response = api.get("/api/fonts", headers=auth(contractor_token))
    assert response.status_code == 200
    fonts = response.json()
    assert [f["family"] for f in fonts] == FAMILIES + B2_FAMILIES
    by_family = {f["family"]: f for f in fonts}
    assert set(by_family["Carlito"]) == {"family", "label", "styles", "license", "substitutes_for", "scripts",
                                         "embedded", "category"}
    assert by_family["Carlito"]["substitutes_for"] == ["Calibri"]
    assert by_family["Caladea"]["substitutes_for"] == ["Cambria"]
    assert "Arial" in by_family["Arimo"]["substitutes_for"]
    assert by_family["Helvetica"]["embedded"] is False and by_family["Arimo"]["embedded"] is True
    assert by_family["DejaVuSans"]["license"] == "Bitstream-Vera" and by_family["Amiri"]["license"] == "OFL-1.1"
    assert by_family["Caladea"]["scripts"] == ["latin"]
    signature = api.get("/api/fonts?category=signature", headers=auth(contractor_token)).json()
    assert [f["family"] for f in signature] == ["GreatVibes", "Allura", "Sacramento"]
    assert api.get("/api/fonts").status_code == 401
    assert api.get("/api/fonts?category=emoji", headers=auth(contractor_token)).status_code == 422


def test_every_font_file_matches_sources_md():
    """SOURCES.md is the provenance record: every file it lists exists with
    exactly that size and SHA-256, and every shipped file is listed."""
    import hashlib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "assets" / "fonts"
    text = (root / "SOURCES.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| ([^|]+?) \| (\d+) \| `([0-9a-f]{64})` \|", text, flags=re.M)
    listed = {}
    for name, size, digest in rows:
        path = root / name if "/" in name else root / "liberation" / name.split(" ")[0]
        listed[path.resolve()] = (int(size), digest)
    assert len(listed) >= 13 + 35
    for path, (size, digest) in listed.items():
        data = path.read_bytes()
        assert (len(data), hashlib.sha256(data).hexdigest()) == (size, digest), path.name
    shipped = {p.resolve() for p in root.rglob("*") if p.is_file() and p.suffix in (".ttf", ".txt", "") and
               p.name not in ("AUTHORS",)}
    assert shipped <= set(listed), sorted(p.name for p in shipped - set(listed))


@pytest.mark.parametrize("font", B2_FAMILIES)
@pytest.mark.parametrize("bold,italic", STYLES)
def test_every_new_face_is_accepted_and_embedded(font, bold, italic):
    from app.services.fonts import EMBEDDED_FAMILIES, resolve_font

    face = resolve_font(font, bold, italic)
    assert face == EMBEDDED_FAMILIES[font][_style_index(bold, italic)]
    text = "Independent Contractor 0123"
    pdf = _build([_text(font=font, bold=bold, italic=italic, text=text)], source=_blank())
    fonts = _page_fonts(pdf)
    # BaseFont is the file's PostScript name (`AAAAAA+Carlito-Regular`), a subset.
    assert any(re.match(r"^/[A-Z]{6}\+" + font, name) for name in fonts), fonts.keys()
    assert text.replace(" ", "") in compact_text(pdf)


def compact_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader

    return "".join((PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text() or "").split())


@pytest.mark.parametrize(
    "font, text, ok",
    [
        ("Carlito", "Привет Oʻzbekiston", True),
        ("Arimo", "Привет Oʻzbekiston €", True),
        ("DejaVuSans", "Привет ✓ Oʻzbek", True),
        ("Caladea", "Привет", False),
        ("Amiri", "Latin text only", True),
        ("Amiri", "سلام", False),
    ],
)
def test_new_families_print_what_their_cmap_has(font, text, ok):
    from app.schemas.contract import AnnotationIn

    body = {"id": "t", "kind": "text", "page": 1, "x_pt": 72, "y_pt": 600, "text": text, "font": font}
    if ok:
        AnnotationIn.model_validate(body)
    else:
        with pytest.raises(PydanticValidationError):
            AnnotationIn.model_validate(body)


def test_the_substitution_map():
    from app.services.pdf_engine.fonts import suggest_family

    assert suggest_family("ABCDEF+Calibri-Bold") == "Carlito"
    assert suggest_family("Cambria") == "Caladea"
    assert suggest_family("ArialMT") == "Arimo"
    assert suggest_family("Arial-BoldItalicMT") == "Arimo"
    assert suggest_family("Helvetica") == "LiberationSans"
    assert suggest_family("TimesNewRomanPSMT") == "LiberationSerif"
    assert suggest_family("CourierNewPSMT") == "LiberationMono"
    assert suggest_family("Verdana") == "DejaVuSans"
    assert suggest_family("SomethingSerif") == "LiberationSerif"
    assert suggest_family("Unknown") == "LiberationSans"


def test_text_replace_in_a_new_family():
    """The editor can set replacement text in Carlito; the file embeds it."""
    from app.services import pdf_engine
    from app.services.pdf_engine import PageRef, from_stored
    from tests.editor_v2_helpers import find_line, replace_op, reportlab_pdf

    def draw(pdf, _index):
        pdf.setFont("Helvetica", 12)
        pdf.drawString(72, 700, "Carrier: ABC Transportation")

    source = reportlab_pdf(draw)
    uid = "pg_000000000001"
    model = pdf_engine.text_model_dict(pdf_engine.page_text_model(source, 0))
    op = replace_op(model, "Carrier: ABC Transportation", "Carrier: Oʻzbek Yuk", page_uid=uid, size=12.0)
    op["runs"][0]["font"] = "Carlito"
    op["box"] = {**find_line(model, "Carrier: ABC Transportation")[1]["bbox"], "w_pt": 400}
    result = pdf_engine.apply_ops(source, [PageRef(uid, 1)], from_stored([op]))
    assert "Oʻzbek" in (__import__("tests.editor_v2_helpers", fromlist=["plumber_text"]).plumber_text(result.pdf_bytes))
    assert any("Carlito" in name for name in _page_fonts(result.pdf_bytes))


def test_font_coverage_generator_covers_every_embedded_face(tmp_path):
    """`gen_font_coverage.py` (staging frontend) emits every embedded face."""
    import importlib.util
    from pathlib import Path

    from app.services.fonts import EMBEDDED_FONT_FILES

    script = Path(__file__).resolve().parents[2] / "frontend" / "scripts" / "gen_font_coverage.py"
    if not script.is_file():
        pytest.skip("no frontend copy next to this backend")
    spec = importlib.util.spec_from_file_location("gen_font_coverage", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.OUT = str(tmp_path / "fontCoverage.ts")
    module.main()
    generated = (tmp_path / "fontCoverage.ts").read_text(encoding="utf-8")
    for face in EMBEDDED_FONT_FILES:
        assert f"'{face}':" in generated


def test_text_model_keeps_the_b1_family_and_adds_the_substitute():
    """A B1 client keeps getting a Liberation family; B2 adds `substitute_family`."""
    from app.services import pdf_engine
    from app.services.pdf_engine.model import Char, Line, PageText, Span
    from app.services.pdf_engine.model import Block

    span = Span(font="Calibri-Bold", size=11, flags=16, color=0, ascender=0.9, descender=-0.2,
                chars=[Char("A", (0, 0, 5, 10), (0, 9))], bbox=(0, 0, 5, 10))
    page = PageText(width=612, height=792, rotation=0, has_text=True, scanned=False,
                    blocks=[Block(id="b0", bbox=(0, 0, 5, 10), lines=[Line("b0l0", (0, 0, 5, 10), (1, 0), [span])])])
    out = pdf_engine.text_model_dict(page)["blocks"][0]["lines"][0]["spans"][0]
    assert (out["family"], out["substitute_family"]) == ("LiberationSans", "Carlito")
