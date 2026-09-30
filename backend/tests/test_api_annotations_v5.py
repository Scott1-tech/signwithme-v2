"""Contract v5: Sejda-style annotation tools on `POST /api/contracts/{id}/finalize`,
and `POST/GET /api/contracts/{id}/annotation-assets`.

What these tests hold onto:

* Every new kind has its own strict schema (discriminated on `kind`): unknown
  keys, an out-of-palette colour, `white`, an opaque fill, out-of-range counts
  and sizes are all 422, and nothing is finalised.
* Geometry off the page is 422 `annotation_outside_page`, for every kind.
* Where and how each kind lands is measured on a pdfium render (the engine of
  Chrome/Edge) at 8 px/pt, not read back from the drawing constants.
* Z-order: original -> whiteouts -> signatures/dates -> the rest in request
  order. Proved with pixels.
* A whiteout is visual only: the covered words are still extracted.
* v4 bodies (no `color`) produce the v4 bytes.
* Image assets: same hardening as a signature (size, magic bytes, bomb, always
  re-encoded) minus background removal; per-contract (IDOR -> 422/404);
  authorised like signing; deleted with the contract.
* Audit: counts per kind + whiteout boxes, never text.
"""
import base64
import io
import json
import time
from uuid import UUID

import pytest
from PIL import Image

from tests.conftest import _make_pdf, auth
from tests.test_api_annotations import (  # noqa: F401 - fixtures
    _chars,
    _contract,
    _final_pdf,
    _finalize,
    _free_signature,
    _ready,
    _timeless,
    signature_data_url,
)
from tests.test_api_signing import _force_signable

PAGE_WIDTH, PAGE_HEIGHT = 612.0, 792.0
SCALE = 8.0  # px per pt, as the contract asks
DATE = "2026-09-24"
# The heading of the fixture PDF: "INDEPENDENT CONTRACTOR AGREEMENT", Helvetica
# 11, baseline at canonical y = 72, x from 72.
HEADING = "INDEPENDENT CONTRACTOR AGREEMENT"
HEADING_BOX = (70.0, 60.0, 240.0, 16.0)  # generous box around its ink

PALETTE = {
    "black": (0, 0, 0),
    "blue": (0x1D, 0x4E, 0xD8),
    "red": (0xDC, 0x26, 0x26),
    "green": (0x16, 0xA3, 0x4A),
    "yellow": (0xFA, 0xCC, 0x15),
    "orange": (0xEA, 0x58, 0x0C),
    "purple": (0x7E, 0x22, 0xCE),
}


# -- helpers --------------------------------------------------------------------


def _render(pdf_bytes: bytes, page_index: int = 0, **kwargs) -> Image.Image:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    try:
        page = document[page_index]
        bitmap = page.render(scale=SCALE, **kwargs)
        try:
            image = bitmap.to_pil().convert("RGB")
        finally:
            bitmap.close()
        page.close()
    finally:
        document.close()
    return image


def _px(image: Image.Image, x_pt: float, y_pt: float) -> tuple[int, int, int]:
    return image.getpixel((int(x_pt * SCALE), int(y_pt * SCALE)))


def _close(actual, expected, tolerance=12) -> bool:
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


def _box(image: Image.Image, window, predicate=None):
    """(left, top, right, bottom) in points of the pixels in `window` (points)
    that satisfy `predicate` (default: not near-white)."""
    left, top, right, bottom = window
    crop = image.crop(
        (int(left * SCALE), int(top * SCALE), int(right * SCALE), int(bottom * SCALE))
    )
    if predicate is None:
        mask = crop.convert("L").point(lambda v: 255 if v < 200 else 0)
    else:
        mask = Image.new("L", crop.size, 0)
        mask.putdata([255 if predicate(p) else 0 for p in crop.getdata()])
    box = mask.getbbox()
    if box is None:
        return None
    return (
        left + box[0] / SCALE,
        top + box[1] / SCALE,
        left + box[2] / SCALE,
        top + box[3] / SCALE,
    )


def _assert_box(actual, expected, tolerance=1.0):
    assert actual is not None, "nothing was drawn"
    for a, e in zip(actual, expected):
        assert abs(a - e) <= tolerance, (actual, expected)


def _dark_count(image: Image.Image, window) -> int:
    left, top, right, bottom = window
    crop = image.crop(
        (int(left * SCALE), int(top * SCALE), int(right * SCALE), int(bottom * SCALE))
    ).convert("L")
    return sum(1 for v in crop.getdata() if v < 100)


def _png(size=(40, 20), colour=(255, 0, 0, 255), mode="RGBA", fmt="PNG", **save) -> bytes:
    image = Image.new(mode, size, colour if mode != "RGB" else colour[:3])
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **save)
    return buffer.getvalue()


def _data_url(data: bytes, mime="image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def _upload(api, token, contract_id, data: bytes | None = None, *, multipart=False, raw=None):
    url = f"/api/contracts/{contract_id}/annotation-assets"
    if multipart:
        return api.post(url, headers=auth(token), files={"file": ("x.png", data, "image/png")})
    body = raw if raw is not None else {"image_data": _data_url(data)}
    return api.post(url, headers=auth(token), json=body)


def _finalize_ok(api, token, contract_id, annotations) -> bytes:
    response = _finalize(api, token, contract_id, contract_date=DATE, annotations=annotations)
    assert response.status_code == 200, response.text
    return _final_pdf(api, token, contract_id)


def _audit_entry(api, admin_token, contract_id, action="contract_finalized") -> dict:
    trail = api.get(f"/api/review/{contract_id}/audit", headers=auth(admin_token)).json()
    return next(e for e in trail if e["action"] == action)


def _hl(**kw):
    note = {"id": "h1", "kind": "highlight", "page": 1,
            "rects": [{"x_pt": 100.0, "y_pt": 300.0, "w_pt": 200.0, "h_pt": 20.0}]}
    note.update(kw)
    return note


def _ink(**kw):
    note = {"id": "i1", "kind": "ink", "page": 1, "stroke_pt": 4.0, "color": "green",
            "paths": [[{"x_pt": 100.0, "y_pt": 300.0}, {"x_pt": 200.0, "y_pt": 300.0},
                       {"x_pt": 200.0, "y_pt": 400.0}]]}
    note.update(kw)
    return note


def _shape(kind="rect", **kw):
    note = {"id": "s1", "kind": kind, "page": 1, "x_pt": 100.0, "y_pt": 300.0,
            "w_pt": 150.0, "h_pt": 80.0, "stroke_pt": 4.0, "color": "blue"}
    note.update(kw)
    return note


def _line(kind="line", **kw):
    note = {"id": "l1", "kind": kind, "page": 1, "x1_pt": 100.0, "y1_pt": 300.0,
            "x2_pt": 300.0, "y2_pt": 300.0, "stroke_pt": 4.0, "color": "red"}
    note.update(kw)
    return note


def _note(**kw):
    note = {"id": "n1", "kind": "note", "page": 1, "x_pt": 300.0, "y_pt": 400.0,
            "text": "Please initial here"}
    note.update(kw)
    return note


def _whiteout(**kw):
    note = {"id": "w1", "kind": "whiteout", "page": 1, "x_pt": HEADING_BOX[0],
            "y_pt": HEADING_BOX[1], "w_pt": HEADING_BOX[2], "h_pt": HEADING_BOX[3]}
    note.update(kw)
    return note


def _image(asset_id, **kw):
    note = {"id": "g1", "kind": "image", "page": 1, "asset_id": asset_id,
            "x_pt": 100.0, "y_pt": 300.0, "w_pt": 80.0, "h_pt": 40.0}
    note.update(kw)
    return note


ASSET = "0" * 32


# -- schema: accept / reject --------------------------------------------------------


def _pts(n, x=100.0, y=300.0):
    return [{"x_pt": x + (i % 100) * 0.5, "y_pt": y + (i // 100) * 0.5} for i in range(n)]


@pytest.mark.parametrize(
    "annotations",
    [
        # colour palette
        pytest.param([_hl(color="white")], id="white-is-not-in-the-palette"),
        pytest.param([_shape(color="#ffffff")], id="hex-colour"),
        pytest.param([_ink(color="Red")], id="colour-case-sensitive"),
        pytest.param([{"id": "t", "kind": "text", "page": 1, "x_pt": 90.0, "y_pt": 300.0,
                       "text": "x", "color": "white"}], id="v4-text-white"),
        pytest.param([{"id": "c", "kind": "check", "page": 1, "x_pt": 90.0, "y_pt": 300.0,
                       "color": "pink"}], id="v4-check-bad-colour"),
        # extra keys, per kind
        pytest.param([_hl(opacity=1.0)], id="highlight-extra-key"),
        pytest.param([_ink(width=3)], id="ink-extra-key"),
        pytest.param([_shape(fill_color="white")], id="rect-fill-colour-key"),
        pytest.param([_shape(fill_alpha=1.0, fill=True)], id="rect-fill-alpha-key"),
        pytest.param([_shape(fill="opaque")], id="rect-fill-not-a-bool"),
        pytest.param([_line(x_pt=1.0)], id="line-extra-key"),
        pytest.param([_note(icon="Key")], id="note-extra-key"),
        pytest.param([_image(ASSET, opacity=0.5)], id="image-extra-key"),
        pytest.param([_whiteout(color="black")], id="whiteout-takes-no-colour"),
        pytest.param([_hl(rects=[{"x_pt": 1, "y_pt": 1, "w_pt": 5, "h_pt": 5, "z": 1}])],
                     id="rect-item-extra-key"),
        pytest.param([_ink(paths=[[{"x_pt": 1, "y_pt": 1, "p": 1}, {"x_pt": 2, "y_pt": 2}]])],
                     id="point-extra-key"),
        pytest.param([_hl(text="hi")], id="highlight-carries-no-text"),
        # markup rects
        pytest.param([_hl(rects=[])], id="markup-no-rects"),
        pytest.param([_hl(rects=[{"x_pt": 1.0, "y_pt": 1.0 + i, "w_pt": 5.0, "h_pt": 1.0}
                                 for i in range(51)])], id="markup-51-rects"),
        pytest.param([_hl(rects=[{"x_pt": 1.0, "y_pt": 1.0, "w_pt": 0.9, "h_pt": 5.0}])],
                     id="markup-rect-too-narrow"),
        pytest.param([_hl(kind="squiggly")], id="unknown-markup-kind"),
        # ink
        pytest.param([_ink(paths=[])], id="ink-no-paths"),
        pytest.param([_ink(paths=[_pts(2)] * 21)], id="ink-21-paths"),
        pytest.param([_ink(paths=[_pts(1)])], id="ink-one-point-path"),
        pytest.param([_ink(paths=[_pts(2001)])], id="ink-2001-point-path"),
        pytest.param([_ink(paths=[_pts(2000), _pts(2000), _pts(1)])], id="ink-4001-points"),
        pytest.param([_ink(stroke_pt=0.49)], id="ink-stroke-too-thin"),
        pytest.param([_ink(stroke_pt=12.1)], id="ink-stroke-too-thick"),
        pytest.param([_ink(stroke_pt="NaN")], id="ink-stroke-nan"),
        pytest.param([_ink(paths=[[{"x_pt": "Infinity", "y_pt": 1}, {"x_pt": 1, "y_pt": 1}]])],
                     id="ink-point-inf"),
        pytest.param([_ink(id=f"i{i}", paths=[_pts(2000), _pts(2000)]) for i in range(5)]
                     + [_ink(id="i5")], id="ink-20001-points-in-total"),
        # shapes
        pytest.param([_shape(w_pt=0.5)], id="rect-too-narrow"),
        pytest.param([_shape(kind="ellipse", h_pt=0)], id="ellipse-zero-height"),
        pytest.param([_shape(w_pt=-5)], id="rect-negative-width"),
        pytest.param([_shape(stroke_pt=0)], id="rect-zero-stroke"),
        # lines
        pytest.param([_line(x2_pt=100.0, y2_pt=300.0)], id="line-zero-length"),
        pytest.param([_line(kind="arrow", x2_pt=100.5, y2_pt=300.5)], id="arrow-shorter-than-1pt"),
        pytest.param([_line(stroke_pt=13)], id="line-stroke-too-thick"),
        # note
        pytest.param([_note(text="")], id="note-empty"),
        pytest.param([_note(text="   \n ")], id="note-whitespace"),
        pytest.param([_note(text="x" * 1001)], id="note-too-long"),
        pytest.param([_note(text="Привет")], id="note-cyrillic"),
        pytest.param([_note(text="a\tb")], id="note-tab"),
        pytest.param([{k: v for k, v in _note().items() if k != "text"}], id="note-no-text"),
        # image
        pytest.param([_image("../../etc/passwd")], id="image-asset-path"),
        pytest.param([_image("0" * 31)], id="image-asset-short"),
        pytest.param([_image("A" * 32)], id="image-asset-uppercase"),
        pytest.param([_image(ASSET, w_pt=0.9)], id="image-too-narrow"),
        pytest.param([{k: v for k, v in _image(ASSET).items() if k != "asset_id"}],
                     id="image-no-asset"),
        # whiteout
        pytest.param([_whiteout(h_pt=0.5)], id="whiteout-too-thin"),
        # common
        pytest.param([_hl(group_id="bad id")], id="group-id-pattern"),
        pytest.param([_hl(group_id="g" * 65)], id="group-id-too-long"),
        pytest.param([_hl(page=0)], id="page-zero"),
        pytest.param([_hl(), _ink(id="h1")], id="duplicate-id-across-kinds"),
    ],
)
def test_a_bad_v5_annotation_is_422_and_nothing_is_finalised(
    api, contractor_token, uploaded_contract, signature_data_url, annotations
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract,
                         contract_date=DATE, annotations=annotations)
    assert response.status_code == 422, response.text
    contract = _contract(api, uploaded_contract)
    assert contract.status != "completed"
    assert contract.final_file_path is None


def test_more_than_five_hundred_elements_is_422_and_five_hundred_is_fine(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    notes = [_whiteout(id=f"w{i}", x_pt=10.0, y_pt=300.0, w_pt=2.0, h_pt=2.0) for i in range(501)]
    assert _finalize(api, contractor_token, uploaded_contract, contract_date=DATE,
                     annotations=notes).status_code == 422
    assert _finalize(api, contractor_token, uploaded_contract, contract_date=DATE,
                     annotations=notes[:500]).status_code == 200


@pytest.mark.parametrize(
    "annotations",
    [
        pytest.param([_hl(), _hl(id="u", kind="underline"), _hl(id="s", kind="strikeout")],
                     id="markup-kinds-default-colour"),
        pytest.param([_hl(rects=[{"x_pt": 1.0, "y_pt": 100.0 + i, "w_pt": 1.0, "h_pt": 1.0}
                                 for i in range(50)])], id="markup-50-rects-1pt"),
        pytest.param([_ink(paths=[_pts(2)] * 20)], id="ink-20-paths"),
        pytest.param([_ink(paths=[_pts(2000), _pts(2000)])], id="ink-4000-points"),
        pytest.param([_ink(id=f"i{i}", paths=[_pts(2000), _pts(2000)]) for i in range(5)],
                     id="ink-20000-points-in-total"),
        pytest.param([_ink(stroke_pt=0.5), _ink(id="i2", stroke_pt=12.0)], id="stroke-bounds"),
        pytest.param([{k: v for k, v in _ink().items() if k not in ("color", "stroke_pt")}],
                     id="ink-defaults"),
        pytest.param([_shape(fill=True), _shape(id="e", kind="ellipse", fill=False)],
                     id="shapes"),
        pytest.param([_shape(w_pt=1.0, h_pt=1.0)], id="rect-1pt"),
        pytest.param([_line(), _line(id="a", kind="arrow"),
                      _line(id="a2", kind="arrow", x2_pt=101.0, y2_pt=300.0)], id="lines"),
        pytest.param([_note(), _note(id="n2", text="x" * 1000, color="blue")], id="notes"),
        pytest.param([_note(text="line one\nline two é €")], id="note-multiline-winansi"),
        pytest.param([_whiteout(), _whiteout(id="w2", x_pt=0.0, y_pt=0.0, w_pt=612.0, h_pt=1.0)],
                     id="whiteouts"),
        pytest.param([_whiteout(group_id="edit-1"),
                      {"id": "t", "kind": "text", "page": 1, "x_pt": 72.0, "y_pt": 72.0,
                       "text": "Replaced", "group_id": "edit-1", "color": "blue"}],
                     id="edit-existing-text-pair"),
        pytest.param([{"id": c, "kind": "text", "page": 1, "x_pt": 90.0, "y_pt": 300.0 + i * 12,
                       "text": c, "color": c} for i, c in enumerate(PALETTE)], id="palette"),
        pytest.param([{"id": "c", "kind": "check", "page": 1, "x_pt": 90.0, "y_pt": 300.0,
                       "color": "green", "text": None, "font": "Courier"}],
                     id="v4-check-keeps-accepting-v4-keys"),
    ],
)
def test_v5_annotations_on_the_boundary_are_accepted(
    api, contractor_token, uploaded_contract, signature_data_url, annotations
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract,
                         contract_date=DATE, annotations=annotations)
    assert response.status_code == 200, response.text


def test_a_missing_kind_is_still_reported_on_the_kind_key(api, contractor_token, uploaded_contract):
    """The union is dispatched by hand so the v4 error shape survives."""
    response = api.post(
        f"/api/contracts/{uploaded_contract}/finalize", headers=auth(contractor_token),
        json={"annotations": [{"id": "a", "page": 1, "rects": []}]},
    )
    assert response.status_code == 422
    entry = response.json()["detail"][0]
    assert entry["loc"] == ["body", "annotations", 0, "kind"]
    assert entry["type"] == "missing"
    # ... and a nested error in a v5 kind has a flat, tag-free loc.
    response = api.post(
        f"/api/contracts/{uploaded_contract}/finalize", headers=auth(contractor_token),
        json={"annotations": [_hl(rects=[{"x_pt": 1, "y_pt": 1, "w_pt": 0, "h_pt": 5}])]},
    )
    assert response.json()["detail"][0]["loc"] == ["body", "annotations", 0, "rects", 0, "w_pt"]


def test_a_v5_422_does_not_quote_note_text_back(api, contractor_token, uploaded_contract):
    secret = "SSN 123-45-6789"
    response = api.post(
        f"/api/contracts/{uploaded_contract}/finalize", headers=auth(contractor_token),
        json={"annotations": [_note(text=secret + " П")]},
    )
    assert response.status_code == 422
    assert secret not in response.text
    assert "U+041F" in response.text


def test_the_openapi_schema_is_a_tagged_union():
    from app.main import app

    schema = app.openapi()
    body = schema["components"]["schemas"]["ContractFinalization"]["properties"]["annotations"]
    items = body["anyOf"][0]["items"]
    mapping = items["discriminator"]["mapping"]
    assert items["discriminator"]["propertyName"] == "kind"
    assert set(mapping) == {
        "text", "date", "check", "cross", "highlight", "underline", "strikeout", "ink",
        "rect", "ellipse", "line", "arrow", "note", "image", "whiteout",
        # markup v7 (editor v2 B2)
        "textbox", "stamp", "image2", "shape2", "ink2", "square_mark",
    }


# -- off the page -------------------------------------------------------------------


@pytest.mark.parametrize(
    "note",
    [
        pytest.param(_hl(rects=[{"x_pt": 500.0, "y_pt": 300.0, "w_pt": 112.5, "h_pt": 10.0}]),
                     id="highlight-past-right-edge"),
        pytest.param(_hl(kind="underline",
                         rects=[{"x_pt": 10.0, "y_pt": 300.0, "w_pt": 10.0, "h_pt": 10.0},
                                {"x_pt": 10.0, "y_pt": 785.0, "w_pt": 10.0, "h_pt": 7.5}]),
                     id="second-markup-rect-past-bottom"),
        pytest.param(_ink(paths=[[{"x_pt": 10.0, "y_pt": 10.0}, {"x_pt": 10.0, "y_pt": 792.1}]]),
                     id="ink-point-below"),
        pytest.param(_ink(paths=[[{"x_pt": -0.1, "y_pt": 10.0}, {"x_pt": 10.0, "y_pt": 10.0}]]),
                     id="ink-point-left"),
        pytest.param(_shape(x_pt=500.0, w_pt=113.0), id="rect-past-right"),
        pytest.param(_shape(kind="ellipse", y_pt=-1.0), id="ellipse-above"),
        pytest.param(_line(x2_pt=612.5), id="line-end-past-right"),
        pytest.param(_line(kind="arrow", y1_pt=800.0), id="arrow-start-below"),
        pytest.param(_note(x_pt=612.5), id="note-anchor-past-right"),
        pytest.param(_image(ASSET, y_pt=760.0), id="image-past-bottom"),
        pytest.param(_whiteout(x_pt=-2.0), id="whiteout-left"),
    ],
)
def test_v5_geometry_off_the_page_is_422(
    api, contractor_token, uploaded_contract, signature_data_url, note
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    if note["kind"] == "image":
        asset = _upload(api, contractor_token, uploaded_contract, _png()).json()["asset_id"]
        note = {**note, "asset_id": asset}
    response = _finalize(api, contractor_token, uploaded_contract,
                         contract_date=DATE, annotations=[note])
    assert response.status_code == 422, response.text
    details = response.json()["details"]
    assert details["reason"] == "annotation_outside_page"
    assert details["annotations"] == [
        {"id": note["id"], "kind": note["kind"], "page": 1,
         "page_width_pt": PAGE_WIDTH, "page_height_pt": PAGE_HEIGHT}
    ]
    assert _contract(api, uploaded_contract).status != "completed"


def test_a_v5_element_on_a_missing_page_is_page_out_of_range(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    response = _finalize(api, contractor_token, uploaded_contract, contract_date=DATE,
                         annotations=[_ink(page=2)])
    assert response.status_code == 422
    assert response.json()["details"]["reason"] == "page_out_of_range"


# -- drawing: measured on a pdfium render ----------------------------------------------

# Blank area of the fixture page (canonical y 250..560 holds nothing).
BLANK = (60.0, 260.0, 560.0, 540.0)


def _drawn(api, token, contract_id, signature, annotations):
    _ready(api, token, contract_id, signature)
    return _render(_finalize_ok(api, token, contract_id, annotations))


def test_highlight_is_translucent_yellow_and_exactly_its_rect(
    api, contractor_token, uploaded_contract, signature_data_url
):
    image = _drawn(api, contractor_token, uploaded_contract, signature_data_url, [_hl()])
    tinted = _box(image, BLANK, lambda p: min(p) < 250)
    _assert_box(tinted, (100.0, 300.0, 300.0, 320.0))
    # white * 0.65 + #FACC15 * 0.35
    expected = tuple(round(255 * 0.65 + c * 0.35) for c in PALETTE["yellow"])
    assert _close(_px(image, 200.0, 310.0), expected, 4), (_px(image, 200.0, 310.0), expected)


def test_text_under_a_highlight_stays_legible(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """The heading's dark pixels, counted without and with a highlight over it.
    Alpha 0.35 over black glyphs leaves them dark: the count barely moves."""
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    window = (72.0, 62.0, 290.0, 75.0)
    plain = _render(_finalize_ok(api, contractor_token, uploaded_contract, []))
    other = api.post(
        "/api/contracts", headers=auth(contractor_token),
        files={"file": ("c.pdf", _make_pdf(filled=True), "application/pdf")},
        data={"template_id": str(_contract(api, uploaded_contract).template_id),
              "contractor_name": "Charles Frye"},
    ).json()["contract_id"]
    highlighted = _drawn(api, contractor_token, other, signature_data_url, [
        _hl(rects=[{"x_pt": 70.0, "y_pt": 62.0, "w_pt": 222.0, "h_pt": 13.0}])
    ])
    before, after = _dark_count(plain, window), _dark_count(highlighted, window)
    assert before > 500, "premise: the heading is there"
    assert after >= before * 0.95, (before, after)
    # ... and the background between letters really is tinted.
    assert _close(_px(highlighted, 71.0, 63.0),
                  tuple(round(255 * 0.65 + c * 0.35) for c in PALETTE["yellow"]), 6)


@pytest.mark.parametrize("kind", ["underline", "strikeout"])
@pytest.mark.parametrize("height", [5.0, 20.0, 40.0])
def test_underline_and_strikeout_bars(
    api, contractor_token, uploaded_contract, signature_data_url, kind, height
):
    from app.services.pdf_builder import markup_line_thickness

    image = _drawn(api, contractor_token, uploaded_contract, signature_data_url, [
        _hl(kind=kind, rects=[{"x_pt": 100.0, "y_pt": 300.0, "w_pt": 200.0, "h_pt": height}])
    ])
    t = markup_line_thickness(height)
    assert t == max(0.75, height * 0.08)
    top = 300.0 + height - t if kind == "underline" else 300.0 + (height - t) / 2
    _assert_box(_box(image, BLANK), (100.0, top, 300.0, top + t))
    assert _close(_px(image, 200.0, top + t / 2), PALETTE["red"], 40)


def test_ink_polyline_with_round_caps(api, contractor_token, uploaded_contract, signature_data_url):
    image = _drawn(api, contractor_token, uploaded_contract, signature_data_url, [_ink()])
    # 4 pt stroke, round caps: 2 pt beyond every extreme point.
    _assert_box(_box(image, BLANK), (98.0, 298.0, 202.0, 402.0))
    assert _close(_px(image, 150.0, 300.0), PALETTE["green"], 10)
    assert _close(_px(image, 200.0, 350.0), PALETTE["green"], 10)
    assert _close(_px(image, 150.0, 350.0), (255, 255, 255), 2), "a polyline, not a fill"


@pytest.mark.parametrize("fill", [False, True])
def test_rect_outline_and_translucent_fill(
    api, contractor_token, uploaded_contract, signature_data_url, fill
):
    image = _drawn(api, contractor_token, uploaded_contract, signature_data_url,
                   [_shape(fill=fill)])
    # Stroke centred on the box edge: 2 pt either side.
    _assert_box(_box(image, BLANK), (98.0, 298.0, 252.0, 382.0))
    assert _close(_px(image, 175.0, 300.0), PALETTE["blue"], 10)
    inside = _px(image, 175.0, 340.0)
    if fill:
        assert _close(inside, tuple(round(255 * 0.8 + c * 0.2) for c in PALETTE["blue"]), 4), inside
    else:
        assert inside == (255, 255, 255)


def test_ellipse_is_inscribed_in_its_box(api, contractor_token, uploaded_contract, signature_data_url):
    image = _drawn(api, contractor_token, uploaded_contract, signature_data_url,
                   [_shape(kind="ellipse", color="purple")])
    _assert_box(_box(image, BLANK), (98.0, 298.0, 252.0, 382.0))
    assert _close(_px(image, 175.0, 300.0), PALETTE["purple"], 12)  # top of the ellipse
    assert _close(_px(image, 100.0, 340.0), PALETTE["purple"], 12)  # left of the ellipse
    assert _px(image, 102.0, 302.0) == (255, 255, 255), "the corner is outside an ellipse"
    assert _px(image, 175.0, 340.0) == (255, 255, 255)


def test_line_has_butt_caps(api, contractor_token, uploaded_contract, signature_data_url):
    image = _drawn(api, contractor_token, uploaded_contract, signature_data_url, [_line()])
    _assert_box(_box(image, BLANK), (100.0, 298.0, 300.0, 302.0), tolerance=0.25)
    assert _close(_px(image, 200.0, 300.0), PALETTE["red"], 10)


def test_diagonal_line_ends_on_its_endpoints(
    api, contractor_token, uploaded_contract, signature_data_url
):
    image = _drawn(api, contractor_token, uploaded_contract, signature_data_url,
                   [_line(x2_pt=300.0, y2_pt=500.0, stroke_pt=1.0, color="black")])
    box = _box(image, BLANK)
    # A 1 pt butt-capped diagonal: the ink box is the segment box +- 0.36 pt.
    _assert_box(box, (100.0, 300.0, 300.0, 500.0))


@pytest.mark.parametrize("stroke", [1.0, 2.0, 6.0])
def test_arrow_head_is_proportional_to_the_stroke(
    api, contractor_token, uploaded_contract, signature_data_url, stroke
):
    image = _drawn(api, contractor_token, uploaded_contract, signature_data_url,
                   [_line(kind="arrow", stroke_pt=stroke)])
    half = 3.0 * stroke
    # Tip exactly at x2; the head is 2 * 3 * stroke tall.
    _assert_box(_box(image, BLANK), (100.0, 300.0 - half, 300.0, 300.0 + half))
    # Just behind the tip the head is solid; well before the head only the shaft.
    assert _close(_px(image, 300.0 - 6.0 * stroke + 0.5, 300.0 + half * 0.8), PALETTE["red"], 40)
    assert _px(image, 200.0, 300.0 + half * 0.8) == (255, 255, 255) or stroke / 2 > half * 0.8


def test_image_fills_its_box_exactly(api, contractor_token, uploaded_contract, signature_data_url):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    left_red_right_blue = Image.new("RGBA", (40, 20), (255, 0, 0, 255))
    left_red_right_blue.paste((0, 0, 255, 255), (20, 0, 40, 20))
    buffer = io.BytesIO()
    left_red_right_blue.save(buffer, format="PNG")
    asset = _upload(api, contractor_token, uploaded_contract, buffer.getvalue()).json()["asset_id"]

    image = _render(_finalize_ok(api, contractor_token, uploaded_contract,
                                 [_image(asset, w_pt=80.0, h_pt=60.0)]))
    _assert_box(_box(image, BLANK), (100.0, 300.0, 180.0, 360.0))
    assert _close(_px(image, 110.0, 330.0), (255, 0, 0), 4)
    assert _close(_px(image, 170.0, 330.0), (0, 0, 255), 4)


def test_a_transparent_asset_shows_the_page_through_it(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    stamp = Image.new("RGBA", (100, 20), (0, 0, 0, 0))
    stamp.paste((255, 0, 0, 255), (0, 0, 5, 20))  # a red bar on the left only
    buffer = io.BytesIO()
    stamp.save(buffer, format="PNG")
    asset = _upload(api, contractor_token, uploaded_contract, buffer.getvalue()).json()["asset_id"]
    image = _render(_finalize_ok(api, contractor_token, uploaded_contract,
                                 [_image(asset, x_pt=60.0, y_pt=60.0, w_pt=240.0, h_pt=16.0)]))
    assert _dark_count(image, (80.0, 62.0, 290.0, 75.0)) > 500, "the heading shows through"
    assert _close(_px(image, 62.0, 68.0), (255, 0, 0), 4)


def test_whiteout_hides_the_original_but_does_not_remove_it(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """WHITEOUT IS NOT REDACTION: nothing dark is left to see, yet a text
    extractor still returns every covered word."""
    from pypdf import PdfReader

    pdf_bytes = (_ready(api, contractor_token, uploaded_contract, signature_data_url)
                 or _finalize_ok(api, contractor_token, uploaded_contract, [_whiteout()]))
    image = _render(pdf_bytes)
    window = (HEADING_BOX[0], HEADING_BOX[1],
              HEADING_BOX[0] + HEADING_BOX[2], HEADING_BOX[1] + HEADING_BOX[3])
    assert _box(image, window, lambda p: p != (255, 255, 255)) is None, "pure white"
    assert HEADING in PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text()
    assert HEADING in "".join(c["text"] for c in _chars(pdf_bytes))


def test_colour_applies_to_v4_text_and_marks(
    api, contractor_token, uploaded_contract, signature_data_url
):
    pdf_bytes = (_ready(api, contractor_token, uploaded_contract, signature_data_url)
                 or _finalize_ok(api, contractor_token, uploaded_contract, [
                     {"id": "t", "kind": "text", "page": 1, "x_pt": 100.0, "y_pt": 320.0,
                      "text": "REDTEXT", "color": "red", "size_pt": 24.0},
                     {"id": "c", "kind": "check", "page": 1, "x_pt": 400.0, "y_pt": 400.0,
                      "color": "blue", "size_pt": 40.0},
                 ]))
    chars = [c for c in _chars(pdf_bytes) if c["text"] in "REDTEXT" and c["size"] > 20]
    assert chars
    for char in chars:
        assert tuple(round(v, 3) for v in char["non_stroking_color"]) == tuple(
            round(c / 255, 3) for c in PALETTE["red"])
    image = _render(pdf_bytes)
    blue = _box(image, (370.0, 370.0, 430.0, 430.0),
                lambda p: _close(p, PALETTE["blue"], 30))
    _assert_box(blue, (380.0, 380.0, 420.0, 420.0), tolerance=8.0)


# -- sticky note: a real /Text annotation ---------------------------------------------


def test_note_is_a_real_text_annotation_with_the_print_flag(
    api, contractor_token, uploaded_contract, signature_data_url
):
    from pypdf import PdfReader

    text = "Please initial here\nsecond line é"
    pdf_bytes = (_ready(api, contractor_token, uploaded_contract, signature_data_url)
                 or _finalize_ok(api, contractor_token, uploaded_contract,
                                 [_note(text=text, color="blue")]))
    page = PdfReader(io.BytesIO(pdf_bytes)).pages[0]
    annots = [a.get_object() for a in page["/Annots"]]
    assert len(annots) == 1
    note = annots[0]
    assert note["/Subtype"] == "/Text"
    assert note["/Contents"] == text
    assert int(note["/F"]) & 4, "Print flag"
    assert int(note["/F"]) == 28
    assert "/Flags" not in note
    assert [round(float(v), 3) for v in note["/C"]] == [round(c / 255, 3) for c in PALETTE["blue"]]
    # (x, y) is the icon's top-left: /Rect is 20 pt square below-right of it.
    assert [round(float(v), 3) for v in note["/Rect"]] == [300.0, 792.0 - 420.0, 320.0, 792.0 - 400.0]
    # Not ink: the page text does not contain it.
    assert "initial" not in page.extract_text()


def test_pdfium_draws_the_note_icon_on_screen_and_in_print(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """pdfium (Chrome/Edge) generates the sticky-note icon itself from /Name +
    /C. The icon is drawn in its /Rect with annotations on, is absent with them
    off (so it is an annotation, not page content), and survives print mode --
    which is what /F Print is for (a copy without the flag disappears there)."""
    import pypdfium2.raw as pdfium_c
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, NumberObject

    pdf_bytes = (_ready(api, contractor_token, uploaded_contract, signature_data_url)
                 or _finalize_ok(api, contractor_token, uploaded_contract, [_note()]))
    window = (290.0, 390.0, 330.0, 430.0)
    on_screen = _box(_render(pdf_bytes), window)
    _assert_box(on_screen, (300.0, 400.0, 320.0, 420.0), tolerance=2.5)
    assert _box(_render(pdf_bytes, draw_annots=False), window) is None
    printed = _box(_render(pdf_bytes, extra_flags=pdfium_c.FPDF_PRINTING), window)
    _assert_box(printed, (300.0, 400.0, 320.0, 420.0), tolerance=2.5)

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes)))
    writer.pages[0]["/Annots"][0].get_object()[NameObject("/F")] = NumberObject(0)
    no_print = io.BytesIO()
    writer.write(no_print)
    assert _box(_render(no_print.getvalue(), extra_flags=pdfium_c.FPDF_PRINTING), window) is None


# Fixed in editor v2 B2 (PdfBuilder._merge_overlay): on /Rotate 90/270 pages
# the old per-mark mapping (geometry.rotate_for_page + to_reportlab) misplaced
# EVERY mark; the overlay is now drawn in displayed coordinates and merged with
# one matrix. These were strict xfails until then.
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_v5_shapes_and_notes_land_in_displayed_coordinates_on_a_rotated_page(rotation):
    """Canonical coordinates are the page AS DISPLAYED. A rect and a note are
    measured on the render (pdfium applies /Rotate, and NoRotate to the note)."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas as rl_canvas

    from app.services.pdf_builder import AnnotationPlacement, PdfBuilder

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

    notes = [
        AnnotationPlacement(id="r", kind="rect", page=1, x_pt=150.0, y_pt=200.0, w_pt=100.0,
                            h_pt=50.0, stroke_pt=2.0, color="blue"),
        AnnotationPlacement(id="n", kind="note", page=1, x_pt=400.0, y_pt=100.0, text="hi"),
    ]
    pdf_bytes = PdfBuilder().build_final_contract(rotated.getvalue(), [], [],
                                                  annotations=notes).pdf_bytes
    image = _render(pdf_bytes)
    _assert_box(_box(image, (130.0, 180.0, 270.0, 270.0)), (149.0, 199.0, 251.0, 251.0))
    _assert_box(_box(image, (390.0, 90.0, 430.0, 130.0)), (400.0, 100.0, 420.0, 120.0),
                tolerance=2.5)
    assert PdfReader(io.BytesIO(pdf_bytes)).pages[0]["/Annots"][0].get_object()["/Subtype"] == "/Text"


# -- z-order ------------------------------------------------------------------------------


def test_a_whiteout_cannot_cover_a_signature(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """Whiteouts are painted before the signatures, whatever the request says."""
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    bbox = {"x": 260.0, "y": 380.0, "width": 200.0, "height": 60.0}
    _free_signature(api, contractor_token, uploaded_contract, signature_data_url, bbox)
    image = _render(_finalize_ok(api, contractor_token, uploaded_contract, [
        _whiteout(x_pt=250.0, y_pt=370.0, w_pt=220.0, h_pt=80.0)
    ]))
    assert _dark_count(image, (255.0, 375.0, 465.0, 445.0)) > 1000, "the signature is on top"


def test_a_whiteout_does_cover_the_original_and_its_replacement_text_is_on_top(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """The "edit existing text" pair, with the TEXT listed BEFORE the whiteout:
    the whiteout still goes under it. The original heading is gone from view,
    the replacement is visible."""
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    pdf_bytes = _finalize_ok(api, contractor_token, uploaded_contract, [
        {"id": "t", "kind": "text", "page": 1, "x_pt": 150.0, "y_pt": 72.0,
         "text": "REPLACED", "group_id": "e1"},
        _whiteout(group_id="e1"),
    ])
    image = _render(pdf_bytes)
    # Left of the replacement text there is only whiteout now.
    assert _dark_count(image, (72.0, 62.0, 148.0, 75.0)) == 0
    assert _dark_count(image, (150.0, 62.0, 220.0, 75.0)) > 300, "replacement visible"


def test_the_rest_is_drawn_in_request_order(
    api, contractor_token, uploaded_contract, signature_data_url
):
    crossing = [
        _ink(id="first", color="red", paths=[[{"x_pt": 100.0, "y_pt": 400.0},
                                              {"x_pt": 300.0, "y_pt": 400.0}]]),
        _ink(id="second", color="blue", paths=[[{"x_pt": 200.0, "y_pt": 300.0},
                                                {"x_pt": 200.0, "y_pt": 500.0}]]),
    ]
    image = _drawn(api, contractor_token, uploaded_contract, signature_data_url, crossing)
    assert _close(_px(image, 200.0, 400.0), PALETTE["blue"], 10)

    other = api.post(
        "/api/contracts", headers=auth(contractor_token),
        files={"file": ("c.pdf", _make_pdf(filled=True), "application/pdf")},
        data={"template_id": str(_contract(api, uploaded_contract).template_id),
              "contractor_name": "Charles Frye"},
    ).json()["contract_id"]
    image = _drawn(api, contractor_token, other, signature_data_url, crossing[::-1])
    assert _close(_px(image, 200.0, 400.0), PALETTE["red"], 10)


def test_annotations_other_than_whiteout_are_on_top_of_signatures(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """An opaque white signature image: a rect outline drawn over it survives."""
    white = Image.new("RGB", (200, 60), (255, 255, 255))
    buffer = io.BytesIO()
    white.save(buffer, format="PNG")
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    bbox = {"x": 260.0, "y": 380.0, "width": 200.0, "height": 60.0}
    _free_signature(api, contractor_token, uploaded_contract, _data_url(buffer.getvalue()), bbox)
    image = _render(_finalize_ok(api, contractor_token, uploaded_contract, [
        _shape(x_pt=300.0, y_pt=390.0, w_pt=100.0, h_pt=40.0)
    ]))
    assert _close(_px(image, 350.0, 390.0), PALETTE["blue"], 10)


# -- v4 regression: byte for byte ----------------------------------------------------------

V4_BODY = [
    {"id": "t", "kind": "text", "page": 1, "x_pt": 120.5, "y_pt": 640.0, "text": "N/A"},
    {"id": "d", "kind": "date", "page": 1, "x_pt": 300.0, "y_pt": 300.0, "text": "09/24/2026",
     "font": "Courier", "size_pt": 14.0},
    {"id": "c", "kind": "check", "page": 1, "x_pt": 300.0, "y_pt": 420.0, "size_pt": 24.0},
    {"id": "x", "kind": "cross", "page": 1, "x_pt": 350.0, "y_pt": 420.0, "text": None},
]


_SOURCE: list[bytes] = []


def _v4_source() -> bytes:
    """ONE source document for every build: reportlab stamps its own creation
    time and file /ID into each new PDF, and pypdf carries that /ID over."""
    if not _SOURCE:
        _SOURCE.append(_make_pdf(filled=True))
    return _SOURCE[0]


def _build(annotations, monkeypatch=None, force_v4_colour=False) -> bytes:
    from app.services import pdf_builder
    from app.services.pdf_builder import AnnotationPlacement, PdfBuilder

    if force_v4_colour:
        # Exactly what v4 passed: the integer literals 0, 0, 0.
        monkeypatch.setattr(pdf_builder.AnnotationPlacement, "rgb", lambda self: (0, 0, 0))
    placements = [
        AnnotationPlacement(**{k: v for k, v in note.items() if k in (
            "id", "kind", "page", "x_pt", "y_pt", "text", "font", "size_pt", "color")})
        for note in annotations
    ]
    return _timeless(PdfBuilder().build_final_contract(
        _v4_source(), [], [], annotations=placements).pdf_bytes)


def test_a_v4_body_without_colour_builds_the_v4_bytes(monkeypatch):
    """The only v4 drawing change is the colour call: `setFillColorRGB(0, 0, 0)`
    became `setFillColorRGB(*note.rgb())`. With `rgb` forced back to v4's
    literal zeroes the output is identical, byte for byte; so is an explicit
    `color: "black"`."""
    without = _build(V4_BODY)
    black = _build([{**note, "color": "black"} for note in V4_BODY])
    assert without == black
    # Premise: the colour really reaches the bytes (so the equality is not vacuous).
    assert _build([{**V4_BODY[0], "color": "red"}]) != _build([V4_BODY[0]])
    # Last: the patch stays in place for the rest of the test.
    assert without == _build(V4_BODY, monkeypatch, force_v4_colour=True)


def test_a_v4_body_through_the_api_matches_the_direct_v4_build(
    api, contractor_token, uploaded_contract, signature_data_url, monkeypatch
):
    """End to end: the finalize route with a v4 body (no colour, no v5 key)."""
    from app.models.signature import Signature
    from app.models.template import Template
    from app.services import pdf_builder
    from app.services.comparison_engine import _index_schema
    from app.services.geometry import BBox
    from app.services.pdf_builder import (
        AnnotationPlacement,
        DatePlacement,
        PdfBuilder,
        SignaturePlacement,
    )

    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    through_api = _finalize_ok(api, contractor_token, uploaded_contract, V4_BODY)

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
    monkeypatch.setattr(pdf_builder.AnnotationPlacement, "rgb", lambda self: (0, 0, 0))
    notes = [AnnotationPlacement(**{k: v for k, v in n.items() if k != "text" or v is not None})
             for n in V4_BODY]
    direct = PdfBuilder().build_final_contract(
        api.objects[contract.file_path], placements, dates,
        title=f"Signed Contract - {contract.contractor_name or contract.id}",
        annotations=notes,
    ).pdf_bytes
    assert _timeless(direct) == _timeless(through_api)


# -- audit -----------------------------------------------------------------------------------


def test_the_audit_counts_every_kind_and_lists_whiteouts_without_text(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url
):
    secret = "123-45-6789"
    name = "Charles Frye SSN"
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    asset = _upload(api, contractor_token, uploaded_contract, _png()).json()["asset_id"]
    notes = [
        _hl(id="pii-h"), _hl(id="pii-u", kind="underline"), _hl(id="pii-s", kind="strikeout"),
        _ink(id="pii-i"), _shape(id="pii-r"), _shape(id="pii-e", kind="ellipse"),
        _line(id="pii-l"), _line(id="pii-a", kind="arrow"),
        _note(id="pii-n", text=f"{name} {secret}"),
        _image(asset, id="pii-g"),
        _whiteout(id="pii-w1", group_id="pii-group"),
        _whiteout(id="pii-w2", page=1, x_pt=10.25, y_pt=500.5, w_pt=30.125, h_pt=5.0),
        {"id": "pii-t", "kind": "text", "page": 1, "x_pt": 90.0, "y_pt": 300.0,
         "text": f"{name} {secret}", "group_id": "pii-group"},
    ]
    _finalize_ok(api, contractor_token, uploaded_contract, notes)

    details = _audit_entry(api, admin_token, uploaded_contract)["details"]
    assert details["annotations"] == {
        "total": 13,
        "by_kind": {"arrow": 1, "ellipse": 1, "highlight": 1, "image": 1, "ink": 1, "line": 1,
                    "note": 1, "rect": 1, "strikeout": 1, "text": 1, "underline": 1,
                    "whiteout": 2},
        "whiteouts": [
            {"page": 1, "x_pt": 70.0, "y_pt": 60.0, "w_pt": 240.0, "h_pt": 16.0},
            {"page": 1, "x_pt": 10.25, "y_pt": 500.5, "w_pt": 30.125, "h_pt": 5.0},
        ],
    }
    dumped = json.dumps(api.get(f"/api/review/{uploaded_contract}/audit",
                                headers=auth(admin_token)).json())
    assert secret not in dumped and name not in dumped
    assert "pii" not in json.dumps(details), "no element ids, no group ids"


def test_note_and_ink_content_is_not_logged(
    api, contractor_token, uploaded_contract, signature_data_url, caplog
):
    secret = "987-65-4321"
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    with caplog.at_level("DEBUG"):
        _finalize_ok(api, contractor_token, uploaded_contract, [_note(text=f"note {secret}")])
    assert secret not in caplog.text


# -- image assets: endpoint --------------------------------------------------------------------


def test_asset_upload_json_and_multipart_then_get(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _force_signable(api, uploaded_contract)
    source = _png(size=(37, 11))
    for multipart in (False, True):
        response = _upload(api, contractor_token, uploaded_contract, source, multipart=multipart)
        assert response.status_code == 201, response.text
        body = response.json()
        assert set(body) == {"asset_id", "width_px", "height_px"}
        assert len(body["asset_id"]) == 32 and int(body["asset_id"], 16) >= 0
        assert (body["width_px"], body["height_px"]) == (37, 11)
        got = api.get(f"/api/contracts/{uploaded_contract}/annotation-assets/{body['asset_id']}",
                      headers=auth(contractor_token))
        assert got.status_code == 200
        assert got.headers["content-type"] == "image/png"
        assert got.headers["x-content-type-options"] == "nosniff"
        key = f"contracts/{uploaded_contract}/asset-{body['asset_id']}.png"
        assert got.content == api.objects[key]
        with Image.open(io.BytesIO(got.content)) as stored:
            assert (stored.format, stored.mode, stored.size) == ("PNG", "RGBA", (37, 11))


def test_asset_background_is_kept_unlike_a_signature(api, contractor_token, uploaded_contract):
    """A logo on an opaque white background with a margin: a signature would be
    made transparent and cropped to its ink; an asset is stored as drawn."""
    _force_signable(api, uploaded_contract)
    logo = Image.new("RGB", (120, 60), (255, 255, 255))
    logo.paste((0, 0, 0), (50, 25, 70, 35))
    buffer = io.BytesIO()
    logo.save(buffer, format="JPEG", quality=95)
    body = _upload(api, contractor_token, uploaded_contract, buffer.getvalue()).json()
    assert (body["width_px"], body["height_px"]) == (120, 60)
    stored = api.objects[f"contracts/{uploaded_contract}/asset-{body['asset_id']}.png"]
    with Image.open(io.BytesIO(stored)) as image:
        assert image.size == (120, 60)
        corner = image.getpixel((2, 2))
        assert corner[3] == 255 and min(corner[:3]) > 245, "white paper kept, opaque"


def test_asset_polyglot_tail_and_metadata_are_not_stored(api, contractor_token, uploaded_contract):
    _force_signable(api, uploaded_contract)
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "SECRET-METADATA")
    image = Image.new("RGBA", (30, 30), (0, 128, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=info)
    polyglot = buffer.getvalue() + b"<script>alert(1)</script>PK\x03\x04zip-tail"
    body = _upload(api, contractor_token, uploaded_contract, polyglot).json()
    stored = api.objects[f"contracts/{uploaded_contract}/asset-{body['asset_id']}.png"]
    assert b"<script>" not in stored and b"zip-tail" not in stored
    assert b"SECRET-METADATA" not in stored and b"tEXt" not in stored
    assert stored.endswith(b"IEND\xaeB`\x82")


def test_asset_jpeg_exif_is_not_stored(api, contractor_token, uploaded_contract):
    _force_signable(api, uploaded_contract)
    image = Image.new("RGB", (30, 30), (200, 10, 10))
    exif = Image.Exif()
    exif[0x010E] = "SECRET-EXIF"  # ImageDescription
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    assert b"SECRET-EXIF" in buffer.getvalue(), "premise"
    body = _upload(api, contractor_token, uploaded_contract, buffer.getvalue()).json()
    stored = api.objects[f"contracts/{uploaded_contract}/asset-{body['asset_id']}.png"]
    assert b"SECRET-EXIF" not in stored and stored.startswith(b"\x89PNG")


def _too_big_png() -> bytes:
    return _png(size=(10, 10)) + b"\0" * (2 * 1024 * 1024)


@pytest.mark.parametrize("multipart", [False, True])
def test_asset_over_two_megabytes_is_413(api, contractor_token, uploaded_contract, multipart):
    _force_signable(api, uploaded_contract)
    response = _upload(api, contractor_token, uploaded_contract, _too_big_png(), multipart=multipart)
    assert response.status_code == 413, response.text
    assert not [k for k in api.objects if "/asset-" in k]


def _bomb_png() -> bytes:
    """A real, small PNG whose header says 4001 x 1 -- past the dimension cap."""
    return _png(size=(4001, 1), colour=(0, 0, 0, 255))


def _huge_pixel_png() -> bytes:
    import struct
    import zlib

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(
            ">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    # Header claims 30000 x 30000; the data is a few bytes. Refused from the header.
    ihdr = struct.pack(">IIBBBBB", 30000, 30000, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(
        b"IDAT", zlib.compress(b"\0" * 10)) + chunk(b"IEND", b"")


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_bomb_png(), id="too-wide"),
        pytest.param(_huge_pixel_png(), id="header-bomb"),
        pytest.param(b"\x89PNG\r\n\x1a\n" + b"garbage" * 20, id="corrupt-png"),
        pytest.param(b"GIF89a" + b"\0" * 20, id="gif"),
        pytest.param(b"<svg xmlns='http://www.w3.org/2000/svg'/>", id="svg"),
        pytest.param(b"%PDF-1.4 not an image", id="pdf"),
        pytest.param(_png(fmt="BMP", mode="RGB"), id="bmp"),
    ],
)
@pytest.mark.parametrize("multipart", [False, True])
def test_asset_bad_image_is_422(api, contractor_token, uploaded_contract, payload, multipart):
    _force_signable(api, uploaded_contract)
    response = _upload(api, contractor_token, uploaded_contract, payload, multipart=multipart)
    assert response.status_code == 422, response.text
    assert not [k for k in api.objects if "/asset-" in k]


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param({"image_data": "data:image/svg+xml;base64,PHN2Zy8+"}, id="svg-data-url"),
        pytest.param({"image_data": "not base64 !!"}, id="not-base64"),
        pytest.param({"image_data": ""}, id="empty"),
        pytest.param({"image": "x"}, id="wrong-key"),
        pytest.param({"image_data": "x", "extra": 1}, id="extra-key"),
        pytest.param([], id="not-an-object"),
    ],
)
def test_asset_bad_json_body_is_422_and_not_echoed(api, contractor_token, uploaded_contract, raw):
    _force_signable(api, uploaded_contract)
    response = _upload(api, contractor_token, uploaded_contract, raw=raw)
    assert response.status_code == 422, response.text
    assert "PHN2Zy8" not in response.text and "not base64" not in response.text


def test_asset_endpoints_need_a_login(api, contractor_token, uploaded_contract):
    _force_signable(api, uploaded_contract)
    url = f"/api/contracts/{uploaded_contract}/annotation-assets"
    assert api.post(url, json={"image_data": _data_url(_png())}).status_code == 401
    asset = _upload(api, contractor_token, uploaded_contract, _png()).json()["asset_id"]
    assert api.get(f"{url}/{asset}").status_code == 401


def test_asset_endpoints_refuse_another_contractor(api, contractor_token, uploaded_contract):
    from tests.conftest import _register

    _force_signable(api, uploaded_contract)
    asset = _upload(api, contractor_token, uploaded_contract, _png()).json()["asset_id"]
    stranger = _register(api, "stranger@example.com")
    url = f"/api/contracts/{uploaded_contract}/annotation-assets"
    assert api.post(url, headers=auth(stranger),
                    json={"image_data": _data_url(_png())}).status_code == 403
    assert api.get(f"{url}/{asset}", headers=auth(stranger)).status_code == 403


def test_asset_upload_follows_the_signing_gate(api, contractor_token, uploaded_contract):
    """Not approved yet -> 409, nothing written (the gate runs before the body
    is even read)."""
    response = _upload(api, contractor_token, uploaded_contract, _png())
    assert response.status_code == 409, response.text
    assert not [k for k in api.objects if "/asset-" in k]


def test_asset_of_another_contract_is_422_at_finalize_and_404_on_get(
    api, contractor_token, uploaded_contract, signature_data_url
):
    """IDOR: the key is built from the contract in the URL, so a foreign asset
    id does not exist here -- whoever can see both contracts."""
    other = api.post(
        "/api/contracts", headers=auth(contractor_token),
        files={"file": ("c.pdf", _make_pdf(filled=True), "application/pdf")},
        data={"template_id": str(_contract(api, uploaded_contract).template_id),
              "contractor_name": "Charles Frye"},
    ).json()["contract_id"]
    _force_signable(api, other)
    foreign = _upload(api, contractor_token, other, _png()).json()["asset_id"]

    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    assert api.get(f"/api/contracts/{uploaded_contract}/annotation-assets/{foreign}",
                   headers=auth(contractor_token)).status_code == 404
    for asset_id in (foreign, "f" * 32):
        response = _finalize(api, contractor_token, uploaded_contract, contract_date=DATE,
                             annotations=[_image(asset_id)])
        assert response.status_code == 422, response.text
        assert response.json()["details"] == {
            "reason": "unknown_annotation_asset",
            "annotations": [{"id": "g1", "kind": "image", "page": 1}],
        }
        assert _contract(api, uploaded_contract).status != "completed"


def test_asset_get_rejects_a_malformed_id(api, contractor_token, uploaded_contract):
    for bad in ("..%2F..%2Fetc", "A" * 32, "0" * 33):
        response = api.get(f"/api/contracts/{uploaded_contract}/annotation-assets/{bad}",
                           headers=auth(contractor_token))
        assert response.status_code in (404, 422), (bad, response.status_code)


def test_asset_upload_is_audited_without_the_image(
    api, contractor_token, admin_token, uploaded_contract
):
    _force_signable(api, uploaded_contract)
    source = _png()
    body = _upload(api, contractor_token, uploaded_contract, source).json()
    entry = _audit_entry(api, admin_token, uploaded_contract, "annotation_asset_added")
    assert set(entry["details"]) == {"asset_id", "sha256", "width_px", "height_px"}
    assert entry["details"]["asset_id"] == body["asset_id"]
    assert base64.b64encode(source).decode()[:40] not in json.dumps(entry)


def test_asset_count_per_contract_is_capped(api, contractor_token, uploaded_contract, monkeypatch):
    from app.api import signatures

    monkeypatch.setattr(signatures, "MAX_ANNOTATION_ASSETS_PER_CONTRACT", 2)
    _force_signable(api, uploaded_contract)
    assert _upload(api, contractor_token, uploaded_contract, _png()).status_code == 201
    assert _upload(api, contractor_token, uploaded_contract, _png()).status_code == 201
    assert _upload(api, contractor_token, uploaded_contract, _png()).status_code == 409


def test_assets_are_deleted_with_the_contract(
    api, admin_token, contractor_token, uploaded_contract, monkeypatch
):
    """The admin deletion removes `contracts/<id>` with `delete_prefix`, which
    takes the regular files of that one folder -- where assets live, flat."""
    from app.services import storage as storage_module

    def fake_delete_prefix(prefix):
        for key in [k for k in api.objects if k.rsplit("/", 1)[0] == prefix]:
            api.objects.pop(key)

    monkeypatch.setattr(storage_module.storage, "delete_prefix", fake_delete_prefix)
    _force_signable(api, uploaded_contract)
    asset = _upload(api, contractor_token, uploaded_contract, _png()).json()["asset_id"]
    key = f"contracts/{uploaded_contract}/asset-{asset}.png"
    assert key in api.objects
    response = api.delete(f"/api/contracts/{uploaded_contract}", headers=auth(admin_token))
    assert response.status_code == 200, response.text
    assert key not in api.objects


def test_local_storage_delete_prefix_takes_the_asset_file(tmp_path):
    import uuid

    from app.services.storage import LocalStorage, annotation_asset_key

    store = LocalStorage(root=str(tmp_path))
    contract_id = uuid.uuid4()
    key = annotation_asset_key(contract_id, "a" * 32)
    store.put_bytes(key, b"png", content_type="image/png")
    store.put_bytes(f"contracts/{contract_id}/original.pdf", b"%PDF-")
    assert (tmp_path / key).exists()
    store.delete_prefix(f"contracts/{contract_id}")
    assert not (tmp_path / "contracts" / str(contract_id)).exists()


@pytest.mark.parametrize("bad", ["../x", "A" * 32, "a" * 31, "", None, "a" * 31 + "/"])
def test_asset_key_refuses_anything_but_a_server_id(bad):
    from app.core.exceptions import StorageError
    from app.services.storage import annotation_asset_key

    with pytest.raises(StorageError):
        annotation_asset_key(UUID(int=1), bad)


# -- at size --------------------------------------------------------------------------------------


def test_five_hundred_elements_and_twenty_thousand_ink_points(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url
):
    """The documented ceilings together, end to end, timed."""
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    asset = _upload(api, contractor_token, uploaded_contract, _png()).json()["asset_id"]
    notes = []
    for i in range(5):  # 5 x 4000 = 20 000 ink points
        paths = [[{"x_pt": 50.0 + (j % 500) * 1.0, "y_pt": 250.0 + i * 20 + p * 5 + (j % 7) * 0.5}
                  for j in range(2000)] for p in range(2)]
        notes.append({"id": f"ink{i}", "kind": "ink", "page": 1, "paths": paths,
                      "stroke_pt": 1.0, "color": "blue"})
    makers = [
        lambda i, x, y: _hl(id=f"e{i}", rects=[{"x_pt": x, "y_pt": y, "w_pt": 20.0, "h_pt": 8.0}]),
        lambda i, x, y: _hl(id=f"e{i}", kind="underline",
                            rects=[{"x_pt": x, "y_pt": y, "w_pt": 20.0, "h_pt": 8.0}]),
        lambda i, x, y: _shape(id=f"e{i}", x_pt=x, y_pt=y, w_pt=20.0, h_pt=8.0, stroke_pt=1.0),
        lambda i, x, y: _shape(id=f"e{i}", kind="ellipse", x_pt=x, y_pt=y, w_pt=20.0, h_pt=8.0,
                               fill=True),
        lambda i, x, y: _line(id=f"e{i}", kind="arrow", x1_pt=x, y1_pt=y, x2_pt=x + 20, y2_pt=y + 8,
                              stroke_pt=1.0),
        lambda i, x, y: _note(id=f"e{i}", x_pt=x, y_pt=y, text=f"note {i}"),
        lambda i, x, y: _image(asset, id=f"e{i}", x_pt=x, y_pt=y, w_pt=20.0, h_pt=8.0),
        lambda i, x, y: _whiteout(id=f"e{i}", x_pt=x, y_pt=y, w_pt=4.0, h_pt=4.0),
        lambda i, x, y: {"id": f"e{i}", "kind": "text", "page": 1, "x_pt": x, "y_pt": y + 8,
                         "text": f"v{i}", "size_pt": 7.0, "color": "purple"},
    ]
    for i in range(495):
        column, row = i % 20, i // 20
        x, y = 20.0 + column * 28.0, 20.0 + row * 30.0
        notes.append(makers[i % len(makers)](i, x, y))
    assert len(notes) == 500

    started = time.perf_counter()
    response = _finalize(api, contractor_token, uploaded_contract, contract_date=DATE,
                         annotations=notes)
    elapsed = time.perf_counter() - started
    assert response.status_code == 200, response.text
    print(f"\nTIMING 500 elements + 20000 ink points: {elapsed:.3f}s "
          f"({len(json.dumps(notes)) / 1024:.0f} KiB body)")
    assert elapsed < 30.0

    pdf_bytes = _final_pdf(api, contractor_token, uploaded_contract)
    from pypdf import PdfReader

    page = PdfReader(io.BytesIO(pdf_bytes)).pages[0]
    assert sum(1 for a in page["/Annots"] if a.get_object()["/Subtype"] == "/Text") == 55
    verify = api.get(f"/api/download/{uploaded_contract}/verify", headers=auth(contractor_token))
    assert verify.json()["intact"] is True
    details = _audit_entry(api, admin_token, uploaded_contract)["details"]["annotations"]
    assert details["total"] == 500
    assert len(details["whiteouts"]) == 55
