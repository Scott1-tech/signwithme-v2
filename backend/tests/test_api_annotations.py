"""Contract v4: free annotations on `POST /api/contracts/{id}/finalize`.

Four kinds of element the user placed by hand rather than in a template slot --
`text`, `date`, `check`, `cross` -- each with its own point in the canonical
space (points, top-left origin) that `bbox` and `rule_y` already use.

What these tests hold onto, in order of how much damage the alternative does:

* Nothing is drawn other than exactly what was asked. A character the font
  cannot print is refused with its codepoint, not quietly replaced: reportlab's
  own fallback prints a ZapfDingbats block, so "Привет" would reach a signed
  contract as six black squares and nobody would be told.
  `test_a_character_the_font_cannot_print_is_refused_by_name` pins the refusal,
  `test_reportlab_would_silently_substitute_an_unprintable_character` the reason.
* A tick is stroked geometry, not a glyph, so its ink box is `size_pt` square
  to the rounding and it needs no font from the viewer. The measurements here
  are taken from a pdfium RENDER, not from the drawing constants.
* An annotation is drawn last, so it is on top of a signature it overlaps. The
  proof is a pixel: an opaque white signature image with a tick centred on it.
* No annotation text reaches the audit trail (PII), only counts per kind.
* Absent `annotations` produces the byte-for-byte document it did before this
  feature existed, /CreationDate aside.
"""
import base64
import io
import re
import time
from uuid import UUID

import pytest
from PIL import Image

from tests.conftest import _make_pdf, auth
from tests.test_api_signing import _force_signable

# The page the synthetic fixture PDF uses (reportlab `letter`).
PAGE_WIDTH, PAGE_HEIGHT = 612.0, 792.0
# Render resolution for ink measurements: 1/16 pt per pixel.
RENDER_SCALE = 16.0


@pytest.fixture
def signature_data_url() -> str:
    image = Image.new("RGBA", (240, 60), (0, 0, 0, 0))
    for x in range(10, 230):
        for y in range(28, 34):
            image.putpixel((x, y), (0, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def opaque_white_signature() -> str:
    """A signature image that is solid, opaque white.

    Only useful for the z-order test: whatever is drawn before it disappears
    under it, so a tick that survives on top can only have been drawn after.
    """
    image = Image.new("RGB", (200, 60), (255, 255, 255))
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


def _finalize(api, token, contract_id, **body):
    return api.post(f"/api/contracts/{contract_id}/finalize", headers=auth(token), json=body)


def _signed(api, token, contract_id, image, field_id="p1_contractor_signature"):
    response = api.post(
        f"/api/contracts/{contract_id}/signatures",
        headers=auth(token),
        json={"field_id": field_id, "image_data": image},
    )
    assert response.status_code == 201, response.text


def _free_signature(api, token, contract_id, image, bbox, page=1):
    response = api.post(
        f"/api/contracts/{contract_id}/signatures",
        headers=auth(token),
        json={"page": page, "bbox": bbox, "image_data": image},
    )
    assert response.status_code == 201, response.text


def _final_pdf(api, token, contract_id) -> bytes:
    return api.get(f"/api/download/{contract_id}/final", headers=auth(token)).content


def _ready(api, token, contract_id, image):
    """A contract that is one `finalize` call away from being signed."""
    _force_signable(api, contract_id)
    _signed(api, token, contract_id, image)


def _annotation(**overrides) -> dict:
    note = {"id": "a1", "kind": "text", "page": 1, "x_pt": 120.5, "y_pt": 640.0, "text": "N/A"}
    note.update(overrides)
    return note


# -- measurement helpers ------------------------------------------------------


def _chars(pdf_bytes: bytes, page_index: int = 0) -> list[dict]:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return list(pdf.pages[page_index].chars)


def _baseline(char: dict) -> float:
    """The canonical (top-left origin) y of a character's BASELINE.

    Taken from the text matrix, not from `char["bottom"]`: the latter is the
    glyph box, which sits a descender below the baseline and so would make the
    assertion depend on the font's metrics rather than on where we drew.
    """
    return PAGE_HEIGHT - char["matrix"][5]


def _word(pdf_bytes: bytes, text: str, page_index: int = 0) -> list[dict]:
    """Every character of the first occurrence of `text`, in order."""
    chars = _chars(pdf_bytes, page_index)
    rendered = "".join(c["text"] for c in chars)
    start = rendered.index(text)
    return chars[start:start + len(text)]


def _ink_box(pdf_bytes: bytes, page_index: int = 0, window: tuple | None = None):
    """(left, top, right, bottom) in points of the dark ink on a rendered page.

    Measured from a pypdfium2 render -- the engine Chrome and Edge use -- so the
    numbers are what a reader sees, not what the drawing code intended.
    """
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    try:
        page = document[page_index]
        bitmap = page.render(scale=RENDER_SCALE)
        try:
            image = bitmap.to_pil().convert("L")
        finally:
            bitmap.close()
        page.close()
    finally:
        document.close()
    if window is not None:
        left, top, right, bottom = (v * RENDER_SCALE for v in window)
        image = image.crop((int(left), int(top), int(right), int(bottom)))
        origin = (window[0], window[1])
    else:
        origin = (0.0, 0.0)
    box = image.point(lambda v: 255 if v < 128 else 0).getbbox()
    if box is None:
        return None
    return (
        origin[0] + box[0] / RENDER_SCALE,
        origin[1] + box[1] / RENDER_SCALE,
        origin[0] + box[2] / RENDER_SCALE,
        origin[1] + box[3] / RENDER_SCALE,
    )


# -- validation ---------------------------------------------------------------


@pytest.mark.parametrize(
    "annotations",
    [
        # kind
        pytest.param([_annotation(kind="circle")], id="unknown-kind"),
        pytest.param([_annotation(kind="Text")], id="kind-is-case-sensitive"),
        pytest.param([{**_annotation(), "kind": None}], id="kind-null"),
        # text
        pytest.param([_annotation(text="")], id="empty-text"),
        pytest.param([_annotation(text="   ")], id="whitespace-only-text"),
        pytest.param([_annotation(text=None)], id="null-text"),
        pytest.param([{k: v for k, v in _annotation().items() if k != "text"}], id="missing-text"),
        pytest.param([_annotation(kind="date", text="")], id="empty-date-text"),
        pytest.param([_annotation(text="x" * 201)], id="text-too-long"),
        pytest.param(
            [_annotation(id="c", kind="check", text="x", y_pt=300.0)], id="text-on-a-check"
        ),
        # size_pt
        pytest.param([_annotation(size_pt=5.9)], id="text-size-too-small"),
        pytest.param([_annotation(size_pt=48.1)], id="text-size-too-big"),
        pytest.param([_annotation(kind="check", text=None, size_pt=5.9)], id="mark-size-too-small"),
        pytest.param([_annotation(kind="cross", text=None, size_pt=72.1)], id="mark-size-too-big"),
        pytest.param([_annotation(size_pt=0)], id="zero-size"),
        pytest.param([_annotation(size_pt=-11)], id="negative-size"),
        pytest.param([_annotation(size_pt="NaN")], id="size-nan"),
        pytest.param([_annotation(size_pt="Infinity")], id="size-inf"),
        pytest.param([_annotation(size_pt="big")], id="size-not-a-number"),
        # font
        pytest.param([_annotation(font="Arial")], id="unknown-font"),
        pytest.param([_annotation(font="helvetica")], id="font-is-case-sensitive"),
        pytest.param([_annotation(font="")], id="empty-font"),
        # id
        pytest.param([_annotation(id="")], id="empty-id"),
        pytest.param([_annotation(id="a" * 65)], id="id-too-long"),
        pytest.param([_annotation(id="bad id")], id="id-with-a-space"),
        pytest.param([_annotation(id="bad.id")], id="id-with-a-dot"),
        pytest.param([_annotation(id="../../etc")], id="id-with-a-path"),
        pytest.param([_annotation(id="a\u00e9")], id="id-not-ascii"),
        pytest.param([_annotation(id=1)], id="id-not-a-string"),
        pytest.param([_annotation(), _annotation(y_pt=600.0)], id="duplicate-id"),
        # page
        pytest.param([_annotation(page=0)], id="page-zero"),
        pytest.param([_annotation(page=-1)], id="page-negative"),
        pytest.param([_annotation(page=2)], id="page-past-the-end"),
        pytest.param([_annotation(page=1.5)], id="page-not-an-integer"),
        # coordinates
        pytest.param([_annotation(x_pt=-0.5)], id="x-left-of-the-page"),
        pytest.param([_annotation(x_pt=612.5)], id="x-right-of-the-page"),
        pytest.param([_annotation(y_pt=-0.5)], id="y-above-the-page"),
        pytest.param([_annotation(y_pt=792.5)], id="y-below-the-page"),
        pytest.param([_annotation(x_pt="NaN")], id="x-nan"),
        pytest.param([_annotation(y_pt="Infinity")], id="y-inf"),
        pytest.param([_annotation(y_pt="-Infinity")], id="y-negative-inf"),
        pytest.param([_annotation(x_pt=None)], id="x-null"),
        # shape
        pytest.param([_annotation(colour="red")], id="extra-key"),
        pytest.param([_annotation(x=1.0)], id="misspelled-key"),
        pytest.param(["a1"], id="not-an-object"),
        pytest.param([[]], id="list-instead-of-an-object"),
        pytest.param({"a1": _annotation()}, id="not-a-list"),
    ],
)
def test_a_bad_annotation_is_422_and_nothing_is_finalised(
    api, contractor_token, uploaded_contract, signature_data_url, annotations
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract,
                         contract_date="2026-09-24", annotations=annotations)
    assert response.status_code == 422, response.text
    contract = _contract(api, uploaded_contract)
    assert contract.status != "completed"
    assert contract.final_file_path is None


def test_more_than_five_hundred_annotations_is_422(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """The cap was 200 in v4 and is 500 from contract v5."""
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    notes = [_annotation(id=f"a{i}", y_pt=100.0 + i * 0.5) for i in range(501)]
    response = _finalize(api, contractor_token, uploaded_contract,
                         contract_date="2026-09-24", annotations=notes)
    assert response.status_code == 422, response.text
    assert _contract(api, uploaded_contract).status != "completed"


@pytest.mark.parametrize(
    "annotations",
    [
        pytest.param([_annotation(size_pt=6.0), _annotation(id="a2", size_pt=48.0, y_pt=600.0)],
                     id="text-size-bounds"),
        pytest.param([_annotation(id="m1", kind="check", text=None, size_pt=6.0, y_pt=300.0),
                      _annotation(id="m2", kind="cross", text=None, size_pt=72.0, y_pt=400.0)],
                     id="mark-size-bounds"),
        pytest.param([_annotation(x_pt=0.0, y_pt=0.0), _annotation(id="a2", x_pt=612.0, y_pt=792.0)],
                     id="page-corners"),
        pytest.param([_annotation(id="A-b_9")], id="id-charset"),
        pytest.param([_annotation(id="a" * 64)], id="id-at-the-length-limit"),
        pytest.param([_annotation(text="x" * 200)], id="text-at-the-length-limit"),
        pytest.param([_annotation(font="Times-Roman"), _annotation(id="a2", font="Courier", y_pt=600.0)],
                     id="fonts"),
        pytest.param([_annotation(kind="date", text="09/24/2026")], id="date-kind"),
        pytest.param([], id="empty-list"),
    ],
)
def test_annotations_on_the_boundary_are_accepted(
    api, contractor_token, uploaded_contract, signature_data_url, annotations
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract,
                         contract_date="2026-09-24", annotations=annotations)
    assert response.status_code == 200, response.text
    assert _contract(api, uploaded_contract).status == "completed"


def test_an_annotation_past_the_last_page_names_itself_and_the_page_count(
    api, admin_token, contractor_token, signature_data_url
):
    """A 2-page upload against a 3-page template: p3 exists in the template but
    not in the document the user is signing."""
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
    contract_id = contract.json()["contract_id"]
    _force_signable(api, contract_id)
    bulk = api.post(
        f"/api/contracts/{contract_id}/signatures/bulk",
        headers=auth(contractor_token),
        json={"image_data": signature_data_url,
              "field_ids": ["p1_contractor_signature", "p2_contractor_signature"]},
    )
    assert bulk.status_code == 200, bulk.text

    response = _finalize(api, contractor_token, contract_id, contract_date="2026-09-24",
                         annotations=[_annotation(id="ok"), _annotation(id="stray", page=3)])
    assert response.status_code == 422, response.text
    details = response.json()["details"]
    assert details["reason"] == "page_out_of_range"
    assert details["page_count"] == 2
    assert details["annotations"] == [{"id": "stray", "kind": "text", "page": 3}]
    assert _contract(api, contract_id).status != "completed"


def test_an_annotation_off_the_page_names_itself_and_the_page_size(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(
        api, contractor_token, uploaded_contract, contract_date="2026-09-24",
        annotations=[_annotation(id="ok"), _annotation(id="stray", kind="check", text=None, x_pt=700.0)],
    )
    assert response.status_code == 422, response.text
    details = response.json()["details"]
    assert details["reason"] == "annotation_outside_page"
    assert details["annotations"] == [
        {"id": "stray", "kind": "check", "page": 1,
         "page_width_pt": PAGE_WIDTH, "page_height_pt": PAGE_HEIGHT}
    ]
    assert _contract(api, uploaded_contract).status != "completed"


def test_one_bad_annotation_discards_the_whole_request(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """All or nothing: the good elements of a rejected batch are not drawn, and
    the contract stays signable so the client can fix the batch and retry."""
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    notes = [_annotation(id=f"ok{i}", y_pt=200.0 + i) for i in range(5)]
    notes.append(_annotation(id="bad", y_pt=5000.0))
    assert _finalize(api, contractor_token, uploaded_contract,
                     contract_date="2026-09-24", annotations=notes).status_code == 422

    contract = _contract(api, uploaded_contract)
    assert contract.status == "signing"
    assert contract.final_file_path is None
    # ... and the same request without the bad element goes through.
    assert _finalize(api, contractor_token, uploaded_contract,
                     contract_date="2026-09-24", annotations=notes[:-1]).status_code == 200


# -- text encoding ------------------------------------------------------------


@pytest.mark.parametrize(
    "text, label",
    [
        ("\u041f\u0440\u0438\u0432\u0435\u0442", "cyrillic"),
        ("Name \u4f60\u597d", "cjk"),
        ("ok \U0001F600", "emoji"),
        ("\u05e9\u05dc\u05d5\u05dd", "hebrew"),
        ("a\nb", "newline"),
        ("a\tb", "tab"),
        ("a\rb", "carriage-return"),
        ("a\x00b", "nul"),
        ("a\x0cb", "form-feed"),
        ("a\x1bb", "escape"),
        ("a\u0081b", "undefined-in-winansi"),
        ("\u2713", "the-check-character-itself"),
    ],
)
def test_a_character_the_font_cannot_print_is_refused_by_name(
    api, contractor_token, uploaded_contract, signature_data_url, text, label
):
    """reportlab would draw a ZapfDingbats dot here and say nothing (see
    `test_reportlab_would_silently_substitute_an_unprintable_character`). On a
    signed contract that is a forged value, so it is a 422 instead -- and the
    message names the exact character, because the user has to be able to find
    it in a 200-character line."""
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-24",
                         annotations=[_annotation(text=text)])
    assert response.status_code == 422, response.text
    offender = next(c for c in text if c not in "abNameok ")
    body = response.text
    assert f"U+{ord(offender):04X}" in body, body
    assert _contract(api, uploaded_contract).status != "completed"


@pytest.mark.parametrize(
    "text, label",
    [
        ("Charles Frye", "ascii"),
        ("N/A", "the common case"),
        ("\u00dcn\u00efcod\u00e9 \u00e4\u00f6\u00fc \u00df", "latin-1 letters"),
        ("\u201cquoted\u201d \u2013 dash", "curly quotes and an en dash"),
        ("\u20ac 1,250.00", "euro sign"),
        ("caf\u00e9 \u00e7a va", "accents"),
        ("100% \u00b1 2 \u00b0C", "symbols winansi has"),
        ("a(b)c\\d", "pdf string delimiters"),
        ("\u00a0leading nbsp", "non-breaking space"),
    ],
)
def test_every_character_winansi_has_is_drawn(
    api, contractor_token, uploaded_contract, signature_data_url, text, label
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    assert _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-24",
                     annotations=[_annotation(text=text)]).status_code == 200

    pdf_bytes = _final_pdf(api, contractor_token, uploaded_contract)
    drawn = "".join(c["text"] for c in _chars(pdf_bytes))
    # Round-tripped through the PDF: a substituted character would come back as
    # something else (reportlab's stand-in decodes as 'n').
    assert text.replace("\u00a0", " ") in drawn, drawn


def test_pdf_string_delimiters_do_not_corrupt_the_document(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """`(`, `)` and `\\` end or escape a PDF string literal. reportlab escapes
    them; if it ever stopped, the file would not parse at all -- so this asserts
    the document still opens and the characters survive."""
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    text = r"a(b)c\d)))"
    assert _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-24",
                     annotations=[_annotation(text=text)]).status_code == 200
    pdf_bytes = _final_pdf(api, contractor_token, uploaded_contract)
    assert "".join(c["text"] for c in _chars(pdf_bytes)).count(text) == 1
    assert api.get(f"/api/download/{uploaded_contract}/verify",
                   headers=auth(contractor_token)).json()["intact"] is True


def test_a_line_longer_than_the_page_is_drawn_from_the_anchor_and_not_clipped_away(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """The anchor is on the page, the tail is not. Consistent with an oversized
    signature, which also runs off the mediabox: the mark starts exactly where
    it was asked to, and the viewer clips what does not fit."""
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    text = "W" * 200
    assert _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-24",
                     annotations=[_annotation(text=text, x_pt=500.0, y_pt=400.0)]).status_code == 200
    chars = _word(_final_pdf(api, contractor_token, uploaded_contract), text)
    assert len(chars) == 200
    assert abs(chars[0]["x0"] - 500.0) <= 0.5
    assert chars[-1]["x1"] > PAGE_WIDTH, "premise: it really does run off the page"


def test_reportlab_would_silently_substitute_an_unprintable_character(api):
    """Why the 422 above exists, pinned against the library rather than argued.

    `unicode2T1` is the function `drawString` goes through. Given Cyrillic and a
    standard font it returns a run in a DIFFERENT font -- ZapfDingbats byte `n`,
    which is glyph `a73`, a filled black square -- with no exception and no
    warning. A signed contract would carry blocks where a name should be.
    """
    from reportlab.pdfbase import pdfmetrics

    from app.services.pdf_builder import undrawable_character

    face = pdfmetrics.getFont("Helvetica")
    chunks = pdfmetrics.unicode2T1("\u041f\u0440\u0438\u0432\u0435\u0442", [face])
    assert [f.fontName for f, _ in chunks] == ["ZapfDingbats"]
    assert chunks[0][1] == b"nnnnnn", "six blocks, not six letters"
    assert pdfmetrics.getFont("ZapfDingbats").encoding.vector[0x6E] == "a73"

    assert undrawable_character("\u041f\u0440\u0438\u0432\u0435\u0442", "Helvetica") == (0, "\u041f")
    assert undrawable_character("ok \u4f60", "Helvetica") == (3, "\u4f60")
    assert undrawable_character("Charles Frye", "Helvetica") is None
    for font in ("Helvetica", "Times-Roman", "Courier"):
        assert undrawable_character("\u00dcn\u00efcod\u00e9 \u20ac", font) is None
        assert undrawable_character("\u041f", font) is not None


# -- drawing: text and date ----------------------------------------------------


@pytest.mark.parametrize("font", ["Helvetica", "Times-Roman", "Courier"])
@pytest.mark.parametrize("size_pt", [6.0, 11.0, 27.5, 48.0])
def test_text_lands_on_the_requested_baseline_in_the_requested_font(
    api, contractor_token, uploaded_contract, signature_data_url, font, size_pt
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    x_pt, y_pt = 123.25, 455.75
    assert _finalize(
        api, contractor_token, uploaded_contract, contract_date="2026-09-24",
        annotations=[_annotation(text="Zq", x_pt=x_pt, y_pt=y_pt, font=font, size_pt=size_pt)],
    ).status_code == 200

    chars = _word(_final_pdf(api, contractor_token, uploaded_contract), "Zq")
    assert abs(chars[0]["x0"] - x_pt) <= 0.5, chars[0]
    for char in chars:
        assert abs(_baseline(char) - y_pt) <= 0.5, char
        assert char["fontname"].endswith(font), char["fontname"]
        assert abs(char["size"] - size_pt) <= 0.01, char


def test_a_date_annotation_is_printed_verbatim(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """Unlike a date SLOT, a free date is not re-parsed and re-formatted: the
    editor already shows the string the user will get, and silently rewriting
    it would move the text under their cursor."""
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    assert _finalize(
        api, contractor_token, uploaded_contract, contract_date="2026-09-24",
        annotations=[_annotation(kind="date", text="09/24/2026", x_pt=440.0, y_pt=592.4)],
    ).status_code == 200

    chars = _word(_final_pdf(api, contractor_token, uploaded_contract), "09/24/2026")
    assert abs(chars[0]["x0"] - 440.0) <= 0.5
    assert abs(_baseline(chars[0]) - 592.4) <= 0.5


def test_an_annotation_reaches_a_page_that_carries_no_signature(
    api, admin_token, contractor_token, signature_data_url
):
    """The overlay is built per page and used to be skipped when a page had no
    signature and no date. A page with nothing but an annotation must still get
    one.

    A one-page template against a two-page upload, so page 2 carries no slot at
    all and finalize cannot demand a signature on it.
    """
    template = api.post(
        "/api/templates", headers=auth(admin_token),
        files={"file": ("t.pdf", _make_pdf(filled=False, pages=1), "application/pdf")},
        data={"name": "One-page template"},
    )
    contract = api.post(
        "/api/contracts", headers=auth(contractor_token),
        files={"file": ("c.pdf", _make_pdf(filled=True, pages=2), "application/pdf")},
        data={"template_id": template.json()["template_id"], "contractor_name": "Charles Frye"},
    )
    contract_id = contract.json()["contract_id"]
    _ready(api, contractor_token, contract_id, signature_data_url)  # page 1 only

    assert _finalize(
        api, contractor_token, contract_id, contract_date="2026-09-24",
        annotations=[_annotation(id="p2", page=2, text="SECOND", x_pt=90.0, y_pt=400.0)],
    ).status_code == 200

    pdf_bytes = _final_pdf(api, contractor_token, contract_id)
    chars = _word(pdf_bytes, "SECOND", page_index=1)
    assert abs(chars[0]["x0"] - 90.0) <= 0.5
    assert abs(_baseline(chars[0]) - 400.0) <= 0.5
    assert "SECOND" not in "".join(c["text"] for c in _chars(pdf_bytes, 0))


# -- drawing: check and cross --------------------------------------------------


@pytest.mark.parametrize("kind", ["check", "cross"])
@pytest.mark.parametrize("size_pt", [6.0, 12.0, 24.0, 72.0])
def test_a_mark_is_centred_on_its_point_and_as_tall_as_it_was_asked_to_be(
    api, contractor_token, uploaded_contract, signature_data_url, kind, size_pt
):
    """Measured from a render, in a window of blank page, so this is the ink a
    reader sees -- including the round caps -- not the path the code drew."""
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    x_pt, y_pt = 300.0, 420.0
    assert _finalize(
        api, contractor_token, uploaded_contract, contract_date="2026-09-24",
        annotations=[_annotation(id="m", kind=kind, text=None, x_pt=x_pt, y_pt=y_pt, size_pt=size_pt)],
    ).status_code == 200

    pdf_bytes = _final_pdf(api, contractor_token, uploaded_contract)
    window = (x_pt - 60, y_pt - 60, x_pt + 60, y_pt + 60)
    box = _ink_box(pdf_bytes, window=window)
    assert box is not None, "the mark was not drawn at all"
    left, top, right, bottom = box
    assert abs((left + right) / 2 - x_pt) <= 1.0, box
    assert abs((top + bottom) / 2 - y_pt) <= 1.0, box
    assert abs((bottom - top) - size_pt) <= size_pt * 0.2, box
    assert abs((right - left) - size_pt) <= size_pt * 0.2, box


def test_a_mark_needs_no_font_from_the_viewer(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """Why a tick is stroked and not a ZapfDingbats glyph.

    The same renderer that draws our tick draws ZapfDingbats 0x34 (`a20`, heavy
    check) as .notdef -- a solid black box. A viewer without the font would
    therefore have printed a filled square onto a signed contract. The page we
    produce carries no font resource for the mark at all.
    """
    import pypdfium2 as pdfium
    from reportlab.pdfgen import canvas as rl_canvas

    # What the glyph route would have produced, in the engine Chrome uses.
    buffer = io.BytesIO()
    glyph_pdf = rl_canvas.Canvas(buffer, pagesize=(120, 120))
    glyph_pdf.setFont("ZapfDingbats", 48)
    glyph_pdf.drawString(30, 40, chr(0x34))
    glyph_pdf.save()
    document = pdfium.PdfDocument(io.BytesIO(buffer.getvalue()))
    try:
        page = document[0]
        bitmap = page.render(scale=RENDER_SCALE)
        try:
            glyph = bitmap.to_pil().convert("L")
        finally:
            bitmap.close()
        page.close()
    finally:
        document.close()
    dark = glyph.point(lambda v: 255 if v < 128 else 0)
    box = dark.getbbox()
    filled = dark.crop(box).tobytes().count(255)
    area = (box[2] - box[0]) * (box[3] - box[1])
    assert filled / area > 0.98, "premise: ZapfDingbats renders as a solid .notdef box here"

    # Ours, by contrast, is a stroke: mostly white inside its own bounding box.
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    assert _finalize(
        api, contractor_token, uploaded_contract, contract_date="2026-09-24",
        annotations=[_annotation(id="m", kind="check", text=None, x_pt=300.0, y_pt=420.0, size_pt=48.0)],
    ).status_code == 200
    pdf_bytes = _final_pdf(api, contractor_token, uploaded_contract)
    ink = _ink_box(pdf_bytes, window=(240, 360, 360, 480))
    assert ink is not None
    assert abs((ink[2] - ink[0]) - 48.0) <= 1.0, ink

    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page_one = pdf.pages[0]
        fonts = {c["fontname"] for c in page_one.chars}
        strokes = [
            obj
            for obj in list(page_one.lines) + list(page_one.curves)
            if 240 <= obj["x0"] and obj["x1"] <= 360
        ]
    assert not any("ZapfDingbats" in f for f in fonts), fonts
    assert strokes, "the tick is vector geometry, not text"


# -- z-order -------------------------------------------------------------------


def test_an_annotation_is_drawn_on_top_of_a_signature(
    api, contractor_token, uploaded_contract, signature_data_url, opaque_white_signature
):
    """Proved with a pixel, not with the source order.

    The free signature image is solid opaque white, so it paints over everything
    beneath it. A tick centred on that rectangle can only be visible if it was
    drawn AFTER the image.
    """
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    bbox = {"x": 260.0, "y": 380.0, "width": 200.0, "height": 60.0}
    _free_signature(api, contractor_token, uploaded_contract, opaque_white_signature, bbox)

    centre = (bbox["x"] + bbox["width"] / 2, bbox["y"] + bbox["height"] / 2)
    notes = [_annotation(id="m", kind="check", text=None,
                         x_pt=centre[0], y_pt=centre[1], size_pt=24.0)]
    assert _finalize(api, contractor_token, uploaded_contract,
                     contract_date="2026-09-24", annotations=notes).status_code == 200

    pdf_bytes = _final_pdf(api, contractor_token, uploaded_contract)
    # Look only well inside the white rectangle.
    window = (bbox["x"] + 10, bbox["y"] + 5, bbox["x"] + bbox["width"] - 10,
              bbox["y"] + bbox["height"] - 5)
    ink = _ink_box(pdf_bytes, window=window)
    assert ink is not None, "the tick was painted over by the signature image"
    assert abs((ink[0] + ink[2]) / 2 - centre[0]) <= 1.0, ink
    assert abs((ink[1] + ink[3]) / 2 - centre[1]) <= 1.0, ink
    assert abs((ink[3] - ink[1]) - 24.0) <= 24.0 * 0.2, ink


def test_the_overlay_emits_annotations_after_the_slot_marks(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """The structural half of the z-order guarantee: one overlay stream, with
    the annotation operators last, so nothing depends on how a viewer orders
    separate overlays."""
    from pypdf import PdfReader

    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    assert _finalize(
        api, contractor_token, uploaded_contract, contract_date="2026-09-24",
        annotations=[_annotation(text="ZORDER", x_pt=90.0, y_pt=300.0)],
    ).status_code == 200

    pdf_bytes = _final_pdf(api, contractor_token, uploaded_contract)
    stream = PdfReader(io.BytesIO(pdf_bytes)).pages[0].get_contents().get_data()
    image_op = stream.rindex(b"/FormXob") if b"/FormXob" in stream else stream.rindex(b" Do")
    assert stream.index(b"(ZORDER) Tj") > image_op, "the annotation is emitted after the signature"


# -- absent annotations --------------------------------------------------------


# pypdf (generic/_base.py, TextStringObject.write_to_stream) octal-escapes every
# non-alphanumeric byte of a text string, so the ':' of the date is written as
# `\072`: `/CreationDate (D\07220260929194034Z)`. The plain `(D:...)` spelling is
# accepted too. The old pattern only knew the plain one, matched NOTHING, and
# every "modulo /CreationDate" comparison was really a raw one -- it failed
# whenever the two builds straddled a wall-clock second (a slow, full run).
_CREATION_DATE = re.compile(rb"/CreationDate \(D(:|\\072)\d{14}Z\)")


def _timeless(pdf_bytes: bytes) -> bytes:
    """The document with only its /CreationDate neutralised -- same length, so
    every byte offset in the xref table is unchanged. Exactly one date must be
    found: a pattern that silently matches nothing makes the comparison
    clock-dependent again."""
    out, count = _CREATION_DATE.subn(
        lambda m: b"/CreationDate (D" + m.group(1) + b"00000000000000Z)", pdf_bytes
    )
    assert count == 1, f"expected exactly one /CreationDate, found {count}"
    return out


@pytest.mark.parametrize("annotations", [None, []])
def test_without_annotations_the_document_is_byte_for_byte_what_it_was(
    api, contractor_token, uploaded_contract, signature_data_url, annotations
):
    """Both spellings of "no annotations" must leave the artefact identical to
    the pre-v4 one. The only difference allowed is /CreationDate, which is the
    wall clock and already differed between any two runs."""
    from app.services.pdf_builder import PdfBuilder

    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    body = {"contract_date": "2026-09-24"}
    if annotations is not None:
        body["annotations"] = annotations
    assert _finalize(api, contractor_token, uploaded_contract, **body).status_code == 200
    through_the_api = _final_pdf(api, contractor_token, uploaded_contract)

    # The same build with the argument the pre-v4 signature did not have.
    from app.models.contract import Contract
    from app.models.signature import Signature
    from app.models.template import Template
    from app.services.comparison_engine import _index_schema
    from app.services.geometry import BBox
    from app.services.pdf_builder import DatePlacement, SignaturePlacement

    db = api.db_factory()
    try:
        contract = db.get(Contract, UUID(uploaded_contract))
        template = db.get(Template, contract.template_id)
        index = _index_schema(template.field_schema)
        rows = list(db.query(Signature).filter(Signature.contract_id == contract.id))
        placements = [
            SignaturePlacement(
                page=row.page_number,
                bbox=BBox.from_dict(row.bbox),
                image_bytes=api.objects[row.signature_image_path],
                field_id=row.field_id,
                rule_y=index[row.field_id]["rule_y"],
            )
            for row in rows
        ]
        paired = index["p1_contractor_signature"]["paired_date_field_id"]
        dates = [
            DatePlacement(
                page=int(index[paired]["page"]),
                bbox=BBox.from_dict(index[paired]["bbox"]),
                value="09/24/2026",
                field_id=paired,
                rule_y=index[paired]["rule_y"],
            )
        ]
        title = f"Signed Contract - {contract.contractor_name or contract.id}"
    finally:
        db.close()

    baseline = PdfBuilder().build_final_contract(
        api.objects[_contract(api, uploaded_contract).file_path],
        placements,
        dates,
        title=title,
    ).pdf_bytes
    assert _timeless(baseline) == _timeless(through_the_api)


# -- audit ---------------------------------------------------------------------


def _finalized_entry(api, admin_token, contract_id) -> dict:
    trail = api.get(f"/api/review/{contract_id}/audit", headers=auth(admin_token)).json()
    return next(e for e in trail if e["action"] == "contract_finalized")


def test_the_audit_records_counts_per_kind(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    notes = [
        _annotation(id="t1", text="N/A"),
        _annotation(id="t2", text="see addendum", y_pt=600.0),
        _annotation(id="d1", kind="date", text="09/24/2026", y_pt=560.0),
        _annotation(id="c1", kind="check", text=None, y_pt=520.0),
        _annotation(id="c2", kind="check", text=None, y_pt=500.0),
        _annotation(id="x1", kind="cross", text=None, y_pt=480.0),
    ]
    assert _finalize(api, contractor_token, uploaded_contract,
                     contract_date="2026-09-24", annotations=notes).status_code == 200

    details = _finalized_entry(api, admin_token, uploaded_contract)["details"]
    assert details["annotations"] == {
        "total": 6,
        "by_kind": {"check": 2, "cross": 1, "date": 1, "text": 2},
    }


@pytest.mark.parametrize("annotations", [None, []])
def test_the_audit_reports_zero_when_nothing_was_annotated(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url, annotations
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    body = {"contract_date": "2026-09-24"}
    if annotations is not None:
        body["annotations"] = annotations
    assert _finalize(api, contractor_token, uploaded_contract, **body).status_code == 200
    details = _finalized_entry(api, admin_token, uploaded_contract)["details"]
    assert details["annotations"] == {"total": 0, "by_kind": {}}
    assert details["layout"]["date_font"] == "Helvetica", "the layout record is untouched"


def test_annotation_text_never_reaches_the_audit_trail(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url
):
    """A free annotation is whatever the user typed -- here an SSN and a name.
    The audit table is read by every admin and kept for years, so it gets counts
    and nothing else. The content lives only in the access-controlled PDF.
    """
    import json

    secret = "123-45-6789"
    name = "Charles Frye SSN"
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    assert _finalize(
        api, contractor_token, uploaded_contract, contract_date="2026-09-24",
        annotations=[_annotation(id="pii", text=f"{name} {secret}")],
    ).status_code == 200

    # The whole trail, not just the finalize entry.
    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    dumped = json.dumps(trail)
    assert secret not in dumped, dumped
    assert name not in dumped, dumped
    # Nor do the coordinates or the client's element ids leak in.
    assert "pii" not in json.dumps(_finalized_entry(api, admin_token, uploaded_contract)["details"])
    # ... while the text really is in the signed document.
    assert secret in "".join(c["text"] for c in _chars(_final_pdf(api, contractor_token, uploaded_contract)))


def test_annotation_text_is_not_logged(
    api, contractor_token, uploaded_contract, signature_data_url, caplog
):
    secret = "987-65-4321"
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    with caplog.at_level("DEBUG"):
        assert _finalize(
            api, contractor_token, uploaded_contract, contract_date="2026-09-24",
            annotations=[_annotation(text=f"note {secret}")],
        ).status_code == 200
    assert secret not in caplog.text


# -- the whole feature, at size ------------------------------------------------


def test_two_hundred_annotations_are_drawn_and_the_document_verifies(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url
):
    """The documented ceiling, end to end: every element lands, the artefact
    hashes to what was recorded, and the audit counts agree."""
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    notes = []
    for i in range(200):
        column, row = i % 10, i // 10
        x_pt, y_pt = 40.0 + column * 55.0, 60.0 + row * 36.0
        kind = ("text", "date", "check", "cross")[i % 4]
        note = {"id": f"a{i:03d}", "kind": kind, "page": 1, "x_pt": x_pt, "y_pt": y_pt,
                "size_pt": 9.0}
        if kind in ("text", "date"):
            note["text"] = f"v{i:03d}"
        notes.append(note)

    started = time.perf_counter()
    response = _finalize(api, contractor_token, uploaded_contract,
                         contract_date="2026-09-24", annotations=notes)
    elapsed = time.perf_counter() - started
    assert response.status_code == 200, response.text
    # Not a performance gate, a sanity bound: 200 marks must not turn one
    # finalize into a request a proxy would time out. Measured here at ~0.18s
    # for the whole call (the drawing itself is ~0.11s of it), so the limit is
    # two orders of magnitude clear and will not go off on a slow CI box.
    print(f"\nTIMING 200 annotations: {elapsed:.3f}s")
    assert elapsed < 20.0, f"200 annotations took {elapsed:.2f}s"

    pdf_bytes = _final_pdf(api, contractor_token, uploaded_contract)
    drawn = "".join(c["text"] for c in _chars(pdf_bytes))
    for i in range(0, 200, 4):
        assert f"v{i:03d}" in drawn, i

    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page_one = pdf.pages[0]
        strokes = len(page_one.lines) + len(page_one.curves)
    # 50 ticks (one polyline each) + 50 crosses (two segments each), plus the
    # template's own rules, so this is a floor rather than an equality.
    assert strokes >= 150, strokes

    verify = api.get(f"/api/download/{uploaded_contract}/verify", headers=auth(contractor_token))
    assert verify.json()["intact"] is True, verify.text
    details = _finalized_entry(api, admin_token, uploaded_contract)["details"]
    assert details["annotations"]["total"] == 200
    assert sum(details["annotations"]["by_kind"].values()) == 200
    assert details["final_file_hash"] == verify.json()["current_hash"]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_rotated_page_bounds_annotations_by_its_displayed_size(rotation):
    """/Rotate 90 or 270 swaps the page's dimensions as a reader sees it, and
    canonical coordinates are read from pdfplumber, which reports that rotated
    geometry. So the limit for `x_pt` is 792 on a rotated letter page, not 612 --
    otherwise half the page would be unreachable and the other half refused."""
    from pypdf import PdfWriter
    from reportlab.pdfgen import canvas as rl_canvas

    from app.services.pdf_builder import (
        AnnotationPlacement,
        AnnotationPlacementError,
        PdfBuilder,
    )

    buffer = io.BytesIO()
    page = rl_canvas.Canvas(buffer, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    page.drawString(72, 700, "x")
    page.showPage()
    page.save()
    writer = PdfWriter(clone_from=io.BytesIO(buffer.getvalue()))
    if rotation:
        writer.pages[0].rotate(rotation)
    rotated = io.BytesIO()
    writer.write(rotated)
    source = rotated.getvalue()

    width, height = (
        (PAGE_HEIGHT, PAGE_WIDTH) if rotation in (90, 270) else (PAGE_WIDTH, PAGE_HEIGHT)
    )
    accepted = [
        AnnotationPlacement(id="t", kind="text", page=1, x_pt=50.0, y_pt=100.0, text="ROT"),
        AnnotationPlacement(id="m", kind="check", page=1, x_pt=width, y_pt=height, size_pt=12.0),
    ]
    assert PdfBuilder().build_final_contract(source, [], [], annotations=accepted).pdf_bytes

    with pytest.raises(AnnotationPlacementError) as refused:
        PdfBuilder().build_final_contract(
            source, [], [],
            annotations=[AnnotationPlacement(id="bad", kind="check", page=1,
                                             x_pt=width + 0.5, y_pt=10.0)],
        )
    assert refused.value.reason == "annotation_outside_page"
    assert refused.value.entries[0]["page_width_pt"] == width
    assert refused.value.entries[0]["page_height_pt"] == height


def test_annotations_do_not_move_the_signature_or_the_date_slots(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """The v1-v3 placement maths is untouched: the same contract finalised with
    and without annotations puts the signature image and the slot date in
    exactly the same place."""
    import pdfplumber

    def _marks(contract_id):
        pdf_bytes = _final_pdf(api, contractor_token, contract_id)
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page_one = pdf.pages[0]
            images = [tuple(round(float(im[k]), 3) for k in ("x0", "top", "x1", "bottom"))
                      for im in page_one.images]
        date_chars = _word(pdf_bytes, "09/24/2026")
        return images, (round(date_chars[0]["x0"], 3), round(_baseline(date_chars[0]), 3))

    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    assert _finalize(api, contractor_token, uploaded_contract,
                     contract_date="2026-09-24").status_code == 200
    plain = _marks(uploaded_contract)

    # A second contract, same inputs, finalised with annotations on top.
    other = api.post(
        "/api/contracts", headers=auth(contractor_token),
        files={"file": ("c.pdf", _make_pdf(filled=True), "application/pdf")},
        data={"template_id": str(_contract(api, uploaded_contract).template_id),
              "contractor_name": "Charles Frye"},
    ).json()["contract_id"]
    _ready(api, contractor_token, other, signature_data_url)
    assert _finalize(
        api, contractor_token, other, contract_date="2026-09-24",
        annotations=[_annotation(id="n", text="ANNOTATED", x_pt=90.0, y_pt=300.0),
                     _annotation(id="m", kind="check", text=None, x_pt=90.0, y_pt=340.0)],
    ).status_code == 200
    assert _marks(other) == plain
