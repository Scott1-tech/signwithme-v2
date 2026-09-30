"""Editor v2 B2 engine ops: redact, image.remove, link.*, /SWMPageId.

Measured with the same tools as B1: three text extractors (pdfplumber, pypdf,
pdfium), the raw content-stream bytes, and pdfium renders (8 px/pt where a
pixel count matters).
"""
from __future__ import annotations

import io
import zlib

import pytest
from PIL import Image

from app.services import pdf_engine
from app.services.pdf_engine import PageRef
from app.services.pdf_engine.ops import from_model
from tests.editor_v2_helpers import all_extractors, chrome_style_pdf, compact, reportlab_pdf, render_gray

UIDS = ["pg_00000000000a", "pg_00000000000b", "pg_00000000000c"]
SCALE = 8.0


def _refs(n: int = 1):
    return [PageRef(UIDS[i], i + 1) for i in range(n)]


def _op(data: dict):
    from pydantic import TypeAdapter

    from app.schemas.document import ContentOp

    return from_model(TypeAdapter(ContentOp).validate_python(data))


def _rect(x, y, w, h):
    return {"x_pt": x, "y_pt": y, "w_pt": w, "h_pt": h}


def _secret_pdf() -> bytes:
    def draw(pdf, _index):
        pdf.setFont("Helvetica", 12)
        pdf.drawString(72, 700, "Name: SECRETVALUE123")
        pdf.drawString(72, 650, "Keep this line")
        pdf.setStrokeColorRGB(0, 0, 0)
        pdf.line(72, 600, 400, 600)

    return reportlab_pdf(draw)


def _all_stream_bytes(pdf_bytes: bytes) -> bytes:
    """Every stream of the file, decompressed where it is Flate."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    out = b""
    for number in range(1, len(reader.xref_objStm) + 1000):
        try:
            obj = reader.get_object(number)
        except Exception:  # noqa: BLE001
            continue
        if obj is not None and hasattr(obj, "get_data"):
            try:
                out += obj.get_data()
            except Exception:  # noqa: BLE001
                out += getattr(obj, "_data", b"")
    return out + pdf_bytes


def _render(pdf_bytes: bytes, index: int = 0, scale: float = SCALE):
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_bytes)
    try:
        page = document[index]
        bitmap = page.render(scale=scale)
        try:
            return bitmap.to_pil().convert("RGB")
        finally:
            bitmap.close()
            page.close()
    finally:
        document.close()


def _box_pixels(image, rect_pt, predicate, scale=SCALE) -> int:
    x0, y0, x1, y1 = (int(v * scale) for v in rect_pt)
    crop = image.crop((x0, y0, x1, y1))
    return sum(1 for p in crop.getdata() if predicate(p))


# -- redact --------------------------------------------------------------------------------


@pytest.mark.parametrize("fill", ["black", "white", "none"])
def test_redact_removes_the_text_from_every_extractor_and_the_stream_bytes(fill):
    source = _secret_pdf()
    # "SECRETVALUE123" sits at x 72 + width("Name: ") on baseline 700 (top-left y 92).
    op = _op({"id": "r1", "kind": "redact", "page_uid": UIDS[0], "rects": [_rect(108, 80, 132, 16)],
              "fill": fill})
    result = pdf_engine.apply_ops(source, _refs(), [op])
    for text in all_extractors(result.pdf_bytes):
        assert "SECRET" not in compact(text)
        assert "Keepthisline" in compact(text)
    raw = _all_stream_bytes(result.pdf_bytes)
    assert b"SECRETVALUE" not in raw and b"SECRET" not in raw
    image = _render(result.pdf_bytes)
    inside = (110, 82, 238, 94)
    dark = _box_pixels(image, inside, lambda p: max(p) < 60)
    white = _box_pixels(image, inside, lambda p: min(p) > 245)
    area = (inside[2] - inside[0]) * (inside[3] - inside[1]) * SCALE * SCALE
    if fill == "black":
        assert dark > 0.95 * area
    else:
        assert white > 0.95 * area  # white box, or nothing on a white page
    assert result.redactions and result.redactions[0]["page_uid"] == UIDS[0]


def test_redact_leaves_the_rest_of_the_page_alone():
    source = _secret_pdf()
    op = _op({"id": "r1", "kind": "redact", "page_uid": UIDS[0], "rects": [_rect(108, 80, 132, 16)],
              "fill": "none"})
    result = pdf_engine.apply_ops(source, _refs(), [op])
    from tests.editor_v2_helpers import pixel_diff

    before, after = render_gray(source), render_gray(result.pdf_bytes)
    assert pixel_diff(before, after, exclude_pt=[(107, 79, 241, 97)]) == 0


def test_redact_keeps_chrome_style_text_in_place():
    """The `0 0 Td` guard applies to area redaction too (spike §10.2.1)."""
    source = chrome_style_pdf()
    op = _op({"id": "r1", "kind": "redact", "page_uid": UIDS[0], "rects": [_rect(60, 30, 300, 20)],
              "fill": "none"})
    result = pdf_engine.apply_ops(source, _refs(), [op])
    from tests.editor_v2_helpers import pixel_diff

    diff = pixel_diff(render_gray(source), render_gray(result.pdf_bytes), exclude_pt=[(55, 25, 365, 55)])
    assert diff <= 16


def test_redact_outside_the_page_is_refused():
    op = _op({"id": "r1", "kind": "redact", "page_uid": UIDS[0], "rects": [_rect(600, 10, 50, 10)]})
    with pytest.raises(pdf_engine.EngineError) as caught:
        pdf_engine.apply_ops(_secret_pdf(), _refs(), [op])
    assert caught.value.code == "op_outside_page"
    problems, _warnings = pdf_engine.validate_ops(_secret_pdf(), _refs(), [op])
    assert problems == [{"id": "r1", "reason": "op_outside_page"}]


def test_redact_schema_rules():
    from pydantic import TypeAdapter, ValidationError

    from app.schemas.document import ContentOp

    adapter = TypeAdapter(ContentOp)
    good = {"id": "r1", "kind": "redact", "page_uid": UIDS[0], "rects": [_rect(1, 1, 5, 5)]}
    assert adapter.validate_python(good).fill == "black"
    for bad in (
        {**good, "rects": []},
        {**good, "fill": "red"},
        {**good, "rects": [_rect(1, 1, 0, 5)]},
        {**good, "layer": "content"},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)


# -- image.remove ---------------------------------------------------------------------------


def _image_pdf() -> bytes:
    from reportlab.lib.utils import ImageReader

    red = io.BytesIO()
    Image.new("RGB", (40, 20), (220, 20, 20)).save(red, format="PNG")
    blue = io.BytesIO()
    Image.new("RGB", (40, 20), (20, 20, 220)).save(blue, format="PNG")

    def draw(pdf, _index):
        pdf.drawImage(ImageReader(io.BytesIO(red.getvalue())), 100, 600, width=120, height=60)
        pdf.drawImage(ImageReader(io.BytesIO(blue.getvalue())), 300, 600, width=120, height=60)
        pdf.setFont("Helvetica", 12)
        pdf.drawString(100, 560, "Caption under the logo")

    return reportlab_pdf(draw)


def test_image_remove_takes_the_image_out_and_keeps_the_rest():
    source = _image_pdf()
    images = pdf_engine.page_images(source, 0)
    assert len(images) == 2
    red = min(images, key=lambda i: i["bbox"]["x_pt"])
    assert red["bbox"] == {"x_pt": 100.0, "y_pt": 132.0, "w_pt": 120.0, "h_pt": 60.0}
    op = _op({"id": "i1", "kind": "image.remove", "page_uid": UIDS[0], "xref": red["xref"], "bbox": red["bbox"]})
    result = pdf_engine.apply_ops(source, _refs(), [op])
    after = pdf_engine.page_images(result.pdf_bytes, 0)
    assert len(after) == 1 and after[0]["bbox"]["x_pt"] == 300.0
    image = _render(result.pdf_bytes, scale=2.0)
    reddish = _box_pixels(image, (100, 132, 220, 192), lambda p: p[0] > 150 and p[1] < 100, scale=2.0)
    blueish = _box_pixels(image, (300, 132, 420, 192), lambda p: p[2] > 150 and p[0] < 100, scale=2.0)
    assert reddish == 0 and blueish > 0.9 * 240 * 120
    assert any("Captionunderthelogo" in compact(t) for t in all_extractors(result.pdf_bytes))
    assert result.images_removed == 1


def test_image_remove_with_a_wrong_box_is_stale():
    source = _image_pdf()
    red = min(pdf_engine.page_images(source, 0), key=lambda i: i["bbox"]["x_pt"])
    op = _op({"id": "i1", "kind": "image.remove", "page_uid": UIDS[0], "xref": red["xref"],
              "bbox": {**red["bbox"], "x_pt": 150.0}})
    with pytest.raises(pdf_engine.EngineError) as caught:
        pdf_engine.apply_ops(source, _refs(), [op])
    assert caught.value.code == "stale_target" and caught.value.status == 409


# -- links -----------------------------------------------------------------------------------


def _pages(n: int = 3) -> bytes:
    def draw(pdf, index):
        pdf.setFont("Helvetica", 12)
        pdf.drawString(72, 700, f"Page {index + 1}")

    return reportlab_pdf(draw, pages=n)


def _link(op_id: str, uid: str, target: dict, kind: str = "link.add", **extra) -> dict:
    return {"id": op_id, "kind": kind, "page_uid": uid, "rect": _rect(72, 80, 100, 20), "target": target, **extra}


def test_link_add_uri_and_internal_page_target():
    source = _pages()
    ops = [
        _op(_link("l1", UIDS[0], {"type": "uri", "uri": "https://example.com/terms?a=1"})),
        _op(_link("l2", UIDS[0], {"type": "page", "page_uid": UIDS[2], "y_pt": 100.0})),
    ]
    result = pdf_engine.apply_ops(source, _refs(3), ops)
    links = pdf_engine.page_links(result.pdf_bytes, 0, {i: uid for i, uid in enumerate(UIDS)})
    targets = sorted((link["target"]["type"], link["target"].get("uri") or link["target"].get("page_uid"))
                     for link in links)
    assert targets == [("page", UIDS[2]), ("uri", "https://example.com/terms?a=1")]
    assert all(link["rect"] == {"x_pt": 72.0, "y_pt": 80.0, "w_pt": 100.0, "h_pt": 20.0} for link in links)


def test_link_to_a_page_the_draft_deletes_is_dropped_with_a_warning():
    source = _pages()
    ops = [
        _op({"id": "d", "kind": "page.delete", "page_uid": UIDS[2]}),
        _op(_link("l1", UIDS[0], {"type": "page", "page_uid": UIDS[2]})),
    ]
    result = pdf_engine.apply_ops(source, _refs(3), ops)
    assert pdf_engine.page_links(result.pdf_bytes, 0) == []
    assert {"code": "link_target_removed", "op_id": "l1"} in result.warnings


def test_an_existing_link_to_a_deleted_page_goes_with_it():
    base = pdf_engine.apply_ops(_pages(), _refs(3), [_op(_link("l1", UIDS[0], {"type": "page", "page_uid": UIDS[2]}))])
    assert len(pdf_engine.page_links(base.pdf_bytes, 0)) == 1
    after = pdf_engine.apply_ops(base.pdf_bytes, _refs(3), [_op({"id": "d", "kind": "page.delete", "page_uid": UIDS[2]})])
    assert pdf_engine.page_links(after.pdf_bytes, 0) == []


def test_link_update_and_remove_by_link_id():
    base = pdf_engine.apply_ops(_pages(), _refs(3), [_op(_link("l1", UIDS[0], {"type": "uri", "uri": "https://a.example"}))])
    link_id = pdf_engine.page_links(base.pdf_bytes, 0)[0]["link_id"]
    updated = pdf_engine.apply_ops(base.pdf_bytes, _refs(3), [
        _op(_link("u1", UIDS[0], {"type": "uri", "uri": "mailto:ops@example.com"}, kind="link.update", link_id=link_id)),
    ])
    links = pdf_engine.page_links(updated.pdf_bytes, 0)
    assert [link["target"]["uri"] for link in links] == ["mailto:ops@example.com"]
    removed = pdf_engine.apply_ops(updated.pdf_bytes, _refs(3), [
        _op({"id": "x1", "kind": "link.remove", "page_uid": UIDS[0], "link_id": links[0]["link_id"]}),
    ])
    assert pdf_engine.page_links(removed.pdf_bytes, 0) == []
    with pytest.raises(pdf_engine.EngineError) as caught:
        pdf_engine.apply_ops(removed.pdf_bytes, _refs(3), [
            _op({"id": "x2", "kind": "link.remove", "page_uid": UIDS[0], "link_id": "lk999999"}),
        ])
    assert caught.value.code == "unknown_link" and caught.value.status == 409


@pytest.mark.parametrize(
    "uri",
    [
        "javascript:alert(1)", "file:///etc/passwd", "data:text/html,x", "/relative", "https://",
        "https://user:pw@example.com", "http://exa mple.com", "mailto:", "mailto:a@b@c",
        "HTTPS://example.com/\x07", "ftp://example.com",
    ],
)
def test_link_uri_validation_refuses(uri):
    from pydantic import TypeAdapter, ValidationError

    from app.schemas.document import ContentOp

    with pytest.raises(ValidationError):
        TypeAdapter(ContentOp).validate_python(_link("l", UIDS[0], {"type": "uri", "uri": uri}))


@pytest.mark.parametrize("uri", ["http://example.com", "https://example.com/a?b=c#d", "mailto:ops@example.com",
                                 "HTTPS://Example.COM/x"])
def test_link_uri_validation_accepts(uri):
    from pydantic import TypeAdapter

    from app.schemas.document import ContentOp

    TypeAdapter(ContentOp).validate_python(_link("l", UIDS[0], {"type": "uri", "uri": uri}))


# -- /SWMPageId --------------------------------------------------------------------------------


def test_every_page_carries_its_uid_readable_by_pypdf():
    from pypdf import PdfReader

    result = pdf_engine.apply_ops(_pages(), _refs(3), [_op({"id": "r", "kind": "page.rotate", "page_uid": UIDS[1],
                                                             "rotation": 90})])
    reader = PdfReader(io.BytesIO(result.pdf_bytes))
    assert [str(page["/SWMPageId"]) for page in reader.pages] == UIDS
    assert result.page_uids == UIDS
    import pdfplumber

    with pdfplumber.open(io.BytesIO(result.pdf_bytes)) as pdf:
        assert len(pdf.pages) == 3
