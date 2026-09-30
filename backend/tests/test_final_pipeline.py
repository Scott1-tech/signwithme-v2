"""Editor v2 B2: `services/final_pipeline.py` and finalize with server markup."""
from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from tests.conftest import auth
from tests.editor_v2_helpers import all_extractors, compact, doc_url, first_page_uid
from tests.test_api_annotations import (  # noqa: F401 - fixtures
    _contract,
    _final_pdf,
    _finalize,
    _free_signature,
    _ready,
    signature_data_url,
)


def _freeze(monkeypatch):
    """/CreationDate is the only moving part of a final: pin it."""
    import datetime as real

    class Frozen(real.datetime):
        @classmethod
        def now(cls, tz=None):
            return real.datetime(2026, 9, 29, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr("app.services.pdf_builder.datetime", Frozen)


def _inputs(signature_png: bytes, **extra):
    from app.services.final_pipeline import FinalInputs
    from app.services.geometry import BBox
    from app.services.pdf_builder import AnnotationPlacement, DatePlacement, Layout, SignaturePlacement
    from tests.conftest import _make_pdf

    return FinalInputs(
        source=_make_pdf(filled=True),
        signatures=[SignaturePlacement(page=1, bbox=BBox(200, 560, 200, 40), image_bytes=signature_png, rule_y=None)],
        dates=[DatePlacement(page=1, bbox=BBox(440, 580, 80, 14), value="2026-09-29", rule_y=None)],
        legacy_annotations=[
            AnnotationPlacement(id="w", kind="whiteout", page=1, x_pt=190, y_pt=550, w_pt=240, h_pt=60),
            AnnotationPlacement(id="t", kind="text", page=1, x_pt=72, y_pt=100, text="LEGACY TEXT"),
        ],
        layout=Layout(),
        metadata={"title": "T", "subject": "S", "keywords": "K"},
        **extra,
    )


def _sig_png() -> bytes:
    image = Image.new("RGBA", (200, 40), (0, 0, 0, 0))
    for x in range(5, 195):
        for y in range(18, 24):
            image.putpixel((x, y), (0, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _image_xobjects(pdf_bytes: bytes) -> int:
    from pypdf import PdfReader

    count = 0

    def walk(resources, seen):
        nonlocal count
        xobjects = (resources or {}).get("/XObject") or {}
        for name in xobjects:
            obj = xobjects[name].get_object()
            key = id(obj)
            if key in seen:
                continue
            seen.add(key)
            if obj.get("/Subtype") == "/Image":
                count += 1
            elif obj.get("/Subtype") == "/Form":
                walk(obj.get("/Resources"), seen)

    for page in PdfReader(io.BytesIO(pdf_bytes)).pages:
        walk(page.get("/Resources"), set())
    return count


def test_default_path_is_byte_for_byte_the_builder(monkeypatch):
    from app.services.final_pipeline import build_final
    from app.services.pdf_builder import PdfBuilder

    _freeze(monkeypatch)
    inputs = _inputs(_sig_png())
    direct = PdfBuilder().build_final_contract(
        inputs.source, inputs.signatures, inputs.dates, layout=inputs.layout,
        annotations=inputs.legacy_annotations, **inputs.metadata,
    )
    assert build_final(inputs).pdf_bytes == direct.pdf_bytes
    # ... and finish=False + finish_pdf is the same document (only serialisation differs).
    from app.services.pdf_builder import finish_pdf

    unfinished = PdfBuilder().build_final_contract(
        inputs.source, inputs.signatures, inputs.dates, layout=inputs.layout,
        annotations=inputs.legacy_annotations, finish=False, **inputs.metadata,
    )
    finished = finish_pdf(unfinished.pdf_bytes, **inputs.metadata)
    from pypdf import PdfReader

    info = PdfReader(io.BytesIO(finished.pdf_bytes)).metadata
    assert (info["/Title"], info["/Subject"], info["/Keywords"]) == ("T", "S", "K")
    assert (PdfReader(io.BytesIO(unfinished.pdf_bytes)).metadata or {}).get("/Title") != "T"  # the source's own


def test_v7_is_drawn_above_signatures_and_signatures_once():
    from app.schemas.contract import ANNOTATION_MODELS
    from app.services.final_pipeline import build_final
    from app.services.pdf_engine.markup import Placement

    box = ANNOTATION_MODELS["shape2"].model_validate(
        {"id": "s", "kind": "shape2", "page": 1, "shape": "rect", "x_pt": 250, "y_pt": 570, "w_pt": 100,
         "h_pt": 20, "stroke_color": "blue", "fill_color": "blue", "stroke_pt": 1}).model_dump(mode="json")
    text = ANNOTATION_MODELS["textbox"].model_validate(
        {"id": "t7", "kind": "textbox", "page": 1, "x_pt": 72, "y_pt": 40, "w_pt": 300, "h_pt": 20,
         "runs": [{"text": "V7 ON TOP", "size_pt": 12}]}).model_dump(mode="json")
    result = build_final(_inputs(_sig_png(), v7=[Placement(box, 0), Placement(text, 0)]))
    pdf = result.pdf_bytes
    assert _image_xobjects(pdf) == 1  # one signature row, one image
    assert "V7ONTOP" in compact(all_extractors(pdf)[0]) and "LEGACYTEXT" in compact(all_extractors(pdf)[0])
    import pypdfium2 as pdfium

    page = pdfium.PdfDocument(pdf)[0]
    image = page.render(scale=4).to_pil().convert("RGB")
    # Inside the signature stroke, under the blue box: blue wins (v7 on top).
    pixel = image.getpixel((int(300 * 4), int(580 * 4)))
    assert pixel[2] > 150 and pixel[0] < 120
    from pypdf import PdfReader

    info = PdfReader(io.BytesIO(pdf)).metadata
    assert info["/Title"] == "T" and info["/Subject"] == "S"


# -- API -----------------------------------------------------------------------------------------


def _put_markup(api, token, contract_id, elements, revision=None):
    if revision is None:
        revision = api.get(doc_url(contract_id, "/markup"), headers=auth(token)).json()["revision"]
    return api.put(doc_url(contract_id, "/markup"), headers=auth(token),
                   json={"revision": revision, "elements": elements})


def test_finalize_draws_the_saved_markup(api, contractor_token, admin_token, uploaded_contract, signature_data_url):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    saved = _put_markup(api, contractor_token, uploaded_contract, [
        {"id": "tb", "kind": "textbox", "page_uid": uid, "x_pt": 72, "y_pt": 30, "w_pt": 300, "h_pt": 30,
         "runs": [{"text": "SERVER TEXTBOX", "size_pt": 12, "color": "blue"}]},
        {"id": "hl", "kind": "highlight", "page_uid": uid, "rects": [{"x_pt": 72, "y_pt": 70, "w_pt": 100, "h_pt": 14}]},
    ])
    assert saved.status_code == 200, saved.text
    # The contract was approved: a textbox is review-relevant (Q1) -> re-approve.
    from tests.test_api_signing import _force_signable

    _force_signable(api, uploaded_contract)
    response = _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-29")
    assert response.status_code == 200, response.text
    pdf = _final_pdf(api, contractor_token, uploaded_contract)
    assert "SERVERTEXTBOX" in compact(all_extractors(pdf)[0])
    trail = api.get(f"/api/review/{uploaded_contract}/audit", headers=auth(admin_token)).json()
    entry = next(e for e in trail if e["action"] == "contract_finalized")
    assert entry["details"]["markup_source"] == "server"
    assert entry["details"]["annotations"]["by_kind"] == {"highlight": 1, "textbox": 1}
    assert "SERVER" not in json.dumps(trail)


def test_saved_markup_and_body_annotations_together_are_refused(
    api, contractor_token, uploaded_contract, signature_data_url
):
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    assert _put_markup(api, contractor_token, uploaded_contract, [
        {"id": "sq", "kind": "square_mark", "page_uid": uid, "x_pt": 100, "y_pt": 100},
    ]).status_code == 200
    response = _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-29",
                         annotations=[{"id": "t", "kind": "check", "page": 1, "x_pt": 50, "y_pt": 50}])
    assert response.status_code == 422
    assert response.json()["details"]["reason"] == "annotations_in_two_places"


def test_v7_in_the_finalize_body_and_image2_from_this_contract_only(
    api, contractor_token, uploaded_contract, signature_data_url
):
    import base64

    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    png = io.BytesIO()
    Image.new("RGBA", (20, 10), (0, 160, 0, 255)).save(png, format="PNG")
    asset = api.post(f"/api/contracts/{uploaded_contract}/annotation-assets", headers=auth(contractor_token),
                     json={"image_data": "data:image/png;base64," + base64.b64encode(png.getvalue()).decode()})
    assert asset.status_code == 201, asset.text
    element = {"id": "im", "kind": "image2", "page": 1, "asset_id": asset.json()["asset_id"], "x_pt": 400,
               "y_pt": 40, "w_pt": 60, "h_pt": 30, "rotation_deg": 90, "opacity": 0.8}
    bad = _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-29",
                    annotations=[{**element, "asset_id": "f" * 32}])
    assert bad.status_code == 422 and bad.json()["details"]["reason"] == "unknown_annotation_asset"
    outside = _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-29",
                        annotations=[{**element, "x_pt": 600}])
    assert outside.status_code == 422 and outside.json()["details"]["reason"] == "annotation_outside_page"
    ok = _finalize(api, contractor_token, uploaded_contract, contract_date="2026-09-29", annotations=[element])
    assert ok.status_code == 200, ok.text
    assert _image_xobjects(_final_pdf(api, contractor_token, uploaded_contract)) == 2  # signature + image


def test_markup_content_changes_reopen_review_markup_layer_does_not(
    api, contractor_token, admin_token, uploaded_contract, signature_data_url
):
    from tests.test_api_signing import _force_signable

    _force_signable(api, uploaded_contract)
    uid = first_page_uid(api, contractor_token, uploaded_contract)
    shape = {"id": "s1", "kind": "shape2", "page_uid": uid, "shape": "ellipse", "x_pt": 10, "y_pt": 10,
             "w_pt": 30, "h_pt": 20}
    first = _put_markup(api, contractor_token, uploaded_contract, [shape])
    assert first.status_code == 200 and first.json()["review_reset"] is False
    stamp = {"id": "st", "kind": "stamp", "page_uid": uid, "x_pt": 100, "y_pt": 100, "w_pt": 120, "h_pt": 40,
             "preset": "approved"}
    second = _put_markup(api, contractor_token, uploaded_contract, [shape, stamp])
    assert second.status_code == 200 and second.json()["review_reset"] is True
    assert _contract(api, uploaded_contract).review_status == "pending"
