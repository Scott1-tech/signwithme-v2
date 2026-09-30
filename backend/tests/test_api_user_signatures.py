"""The per-user reusable signature library and its ownership boundary.

Two things matter here. First, the reuse loop: a user saves a signature once,
lists it, and can place its stored PNG on a contract and finalise -- the same
placement path a freshly drawn signature takes. Second, and the reason this
feature needs its own test file, the IDOR boundary: one user must never read or
delete another user's stored signature. Every per-item route (get-one, image
bytes, delete) is checked from the perspective of a second, unrelated user.
"""
import base64
import hashlib
import io

import pytest
from PIL import Image
from sqlalchemy import select

from tests.conftest import _register, auth
from tests.test_api_signing import _force_signable


@pytest.fixture
def png_data_url() -> str:
    """A minimal but valid PNG data URL, as the canvas pad would emit."""
    image = Image.new("RGBA", (240, 60), (0, 0, 0, 0))
    for x in range(10, 230):
        image.putpixel((x, 30), (0, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _save(api, token, png_data_url, *, label=None, is_default=False):
    return api.post(
        "/api/signatures",
        headers=auth(token),
        json={"image_data": png_data_url, "label": label, "is_default": is_default},
    )


# --------------------------------------------------------------------------
# Happy path: save -> list -> reuse on a contract -> finalise
# --------------------------------------------------------------------------


def test_a_saved_signature_is_returned_without_the_image_bytes(
    api, contractor_token, png_data_url
):
    response = _save(api, contractor_token, png_data_url, label="My mark", is_default=True)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["label"] == "My mark"
    assert body["is_default"] is True
    assert len(body["signature_hash"]) == 64
    # The list projection deliberately never carries the raw image or its path.
    assert "image_data" not in body
    assert "image_path" not in body


def test_list_returns_only_the_callers_signatures(api, png_data_url):
    alice = _register(api, "alice@example.com")
    bob = _register(api, "bob@example.com")

    _save(api, alice, png_data_url, label="alice-1")
    _save(api, alice, png_data_url, label="alice-2")
    _save(api, bob, png_data_url, label="bob-1")

    alice_list = api.get("/api/signatures", headers=auth(alice)).json()
    bob_list = api.get("/api/signatures", headers=auth(bob)).json()

    assert {s["label"] for s in alice_list} == {"alice-1", "alice-2"}
    assert {s["label"] for s in bob_list} == {"bob-1"}


def test_the_stored_image_bytes_round_trip(api, contractor_token, png_data_url):
    saved = _save(api, contractor_token, png_data_url).json()
    image = api.get(f"/api/signatures/{saved['id']}/image", headers=auth(contractor_token))
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
    # The bytes served back are the server's *re-encoded* RGBA PNG, not the
    # client's bytes verbatim: re-encoding is what strips metadata and any
    # polyglot payload. The image is also cropped to its ink (2 px margin on
    # the left, top and right; the bottom edge is the lowest ink row), and the
    # ink pixels inside that crop must match the original exactly.
    original = Image.open(io.BytesIO(base64.b64decode(png_data_url.split(",", 1)[1])))
    stored = Image.open(io.BytesIO(image.content))
    assert stored.mode == "RGBA"
    # Fixture ink: x 10..229, y 30 -> crop (8, 28) .. (231, 30) inclusive.
    expected = original.convert("RGBA").crop((8, 28, 232, 31))
    assert stored.size == expected.size
    assert list(stored.getdata()) == list(expected.getdata())
    assert image.headers["x-content-sha256"] == saved["signature_hash"]
    assert hashlib.sha256(image.content).hexdigest() == saved["signature_hash"]


def test_a_stored_signature_can_be_placed_and_finalised(
    api, contractor_token, uploaded_contract, png_data_url
):
    """The reuse loop end to end: the stored PNG's bytes drive placement.

    This mirrors Option A -- the client fetches the stored image and feeds its
    base64 into the unchanged placement endpoint. Here we do exactly that at the
    byte level to prove the stored library round-trips through the signing path.
    """
    saved = _save(api, contractor_token, png_data_url).json()
    _force_signable(api, uploaded_contract)

    stored_png = api.get(
        f"/api/signatures/{saved['id']}/image", headers=auth(contractor_token)
    ).content
    reused_data_url = "data:image/png;base64," + base64.b64encode(stored_png).decode()

    placed = api.post(
        f"/api/contracts/{uploaded_contract}/signatures",
        headers=auth(contractor_token),
        json={
            "page": 1,
            "bbox": {"x": 200, "y": 580, "width": 150, "height": 34},
            "field_id": "p1_contractor_signature",
            "image_data": reused_data_url,
        },
    )
    assert placed.status_code == 201, placed.text

    finalized = api.post(
        f"/api/contracts/{uploaded_contract}/finalize",
        headers=auth(contractor_token),
        json={"contract_date": "2026-08-29", "apply_paired_dates": True},
    )
    assert finalized.status_code == 200, finalized.text
    assert len(finalized.json()["file_hash"]) == 64


def test_saving_a_new_default_demotes_the_previous_one(api, contractor_token, png_data_url):
    first = _save(api, contractor_token, png_data_url, label="first", is_default=True).json()
    second = _save(api, contractor_token, png_data_url, label="second", is_default=True).json()

    listed = {s["id"]: s for s in api.get("/api/signatures", headers=auth(contractor_token)).json()}
    assert listed[first["id"]]["is_default"] is False
    assert listed[second["id"]]["is_default"] is True


def test_a_corrupt_signature_image_is_rejected(api, contractor_token):
    response = api.post(
        "/api/signatures",
        headers=auth(contractor_token),
        json={"image_data": "data:image/png;base64,bm90LWFuLWltYWdl", "is_default": False},
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Image input hardening: the upload path accepts files chosen by the user, so
# the server must decide format and size from the bytes, bound the decode, and
# never persist the client's bytes verbatim.
# --------------------------------------------------------------------------


def _encode(image: Image.Image, fmt: str, **kwargs) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _post_image(api, token, image_data: str):
    return api.post(
        "/api/signatures", headers=auth(token), json={"image_data": image_data, "is_default": False}
    )


def test_a_bare_base64_png_file_upload_is_stored_re_encoded(api, contractor_token):
    """The file picker path: real PNG bytes, no data-URL prefix."""
    source = Image.new("RGB", (300, 80), (255, 255, 255))
    for x in range(20, 280):
        source.putpixel((x, 40), (0, 0, 0))
    raw = _encode(source, "PNG")

    response = _post_image(api, contractor_token, _b64(raw))
    assert response.status_code == 201, response.text
    saved = response.json()

    stored = api.get(f"/api/signatures/{saved['id']}/image", headers=auth(contractor_token)).content
    assert stored.startswith(b"\x89PNG\r\n\x1a\n")
    assert stored != raw  # re-encoded, not the client's bytes
    assert Image.open(io.BytesIO(stored)).mode == "RGBA"
    assert hashlib.sha256(stored).hexdigest() == saved["signature_hash"]


def test_a_jpeg_upload_is_accepted_and_normalised_to_png(api, contractor_token):
    """A photo of a signature (JPEG) is accepted, but only PNG is ever stored."""
    source = Image.new("RGB", (320, 120), (250, 250, 250))
    raw = _encode(source, "JPEG", quality=85)
    assert raw.startswith(b"\xff\xd8\xff")

    response = _post_image(api, contractor_token, "data:image/jpeg;base64," + _b64(raw))
    assert response.status_code == 201, response.text
    saved = response.json()

    image = api.get(f"/api/signatures/{saved['id']}/image", headers=auth(contractor_token))
    assert image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert Image.open(io.BytesIO(image.content)).format == "PNG"


def test_a_data_url_png_is_still_accepted(api, contractor_token, png_data_url):
    """The canvas pad's existing contract: `data:image/png;base64,` prefix."""
    assert png_data_url.startswith("data:image/png;base64,")
    response = _post_image(api, contractor_token, png_data_url)
    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    "image_data",
    [
        # SVG carrying script, declared honestly...
        "data:image/svg+xml;base64,"
        + _b64(b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"),
        # ...and SVG lying about its MIME. The bytes decide, not the prefix.
        "data:image/png;base64,"
        + _b64(b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"),
        # Bare SVG, no prefix at all.
        _b64(b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
        # GIF and WebP magic bytes.
        _b64(b"GIF89a" + b"\x00" * 32),
        _b64(b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32),
        # A text/html data URL.
        "data:text/html;base64," + _b64(b"<html><script>alert(1)</script></html>"),
    ],
)
def test_non_png_jpeg_formats_are_refused(api, contractor_token, image_data):
    response = _post_image(api, contractor_token, image_data)
    assert response.status_code == 422, response.text
    assert response.json()["message"] == "Unsupported image format"


def test_png_magic_with_a_corrupt_body_is_refused(api, contractor_token):
    """Magic bytes alone must not be trusted: the file has to actually parse."""
    fake = b"\x89PNG\r\n\x1a\n" + b"not really a png" * 32
    response = _post_image(api, contractor_token, _b64(fake))
    assert response.status_code == 422, response.text
    assert "corrupt" in response.json()["message"]


def test_a_truncated_png_is_refused(api, contractor_token, png_data_url):
    raw = base64.b64decode(png_data_url.split(",", 1)[1])
    response = _post_image(api, contractor_token, _b64(raw[: len(raw) // 2]))
    assert response.status_code == 422, response.text


def test_an_image_over_two_megabytes_is_refused_with_413(api, contractor_token):
    # Valid magic so the size gate, not the format gate, is what fires.
    oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024 + 1)
    response = _post_image(api, contractor_token, _b64(oversized))
    assert response.status_code == 413, response.text
    assert response.json()["error"] == "payload_too_large"
    assert response.json()["message"] == "Signature image too large"


def test_a_decompression_bomb_is_refused(api, contractor_token):
    """A 10000x10000 1-bit PNG is ~30 KB on the wire but 100 megapixels
    decoded (400 MB as RGBA). It passes the byte-size gate and must be stopped
    by the dimension gate before any pixel is decoded."""
    bomb = _encode(Image.new("1", (10000, 10000), 1), "PNG")
    assert len(bomb) < 2 * 1024 * 1024
    response = _post_image(api, contractor_token, _b64(bomb))
    assert response.status_code == 422, response.text
    assert "dimensions" in response.json()["message"]


def test_an_image_just_over_the_dimension_limit_is_refused(api, contractor_token):
    """4500x4000 = 18 MP, under the pixel cap but over the 4000 px edge limit."""
    wide = _encode(Image.new("1", (4500, 4000), 1), "PNG")
    response = _post_image(api, contractor_token, _b64(wide))
    assert response.status_code == 422, response.text
    assert response.json()["details"]["max_dimension"] == 4000


def test_a_polyglot_tail_is_stripped_by_re_encoding(api, contractor_token, png_data_url):
    """PNG bytes with an HTML/script payload appended still parse as a PNG.
    Storing them verbatim would turn the image route into a stored payload;
    re-encoding drops everything after the image data."""
    raw = base64.b64decode(png_data_url.split(",", 1)[1])
    polyglot = raw + b"<html><script>alert('xss')</script></html>"
    response = _post_image(api, contractor_token, _b64(polyglot))
    assert response.status_code == 201, response.text

    stored = api.get(
        f"/api/signatures/{response.json()['id']}/image", headers=auth(contractor_token)
    ).content
    assert b"<script" not in stored
    assert b"<html" not in stored


# --------------------------------------------------------------------------
# IDOR boundary: a second user must not reach the first user's signature
# --------------------------------------------------------------------------


def test_another_user_cannot_read_get_one(api, png_data_url):
    alice = _register(api, "alice@example.com")
    bob = _register(api, "bob@example.com")
    sig_id = _save(api, alice, png_data_url).json()["id"]

    response = api.get(f"/api/signatures/{sig_id}", headers=auth(bob))
    # 404, not 403: the endpoint must not confirm that someone else's id exists.
    assert response.status_code == 404


def test_another_user_cannot_read_the_image(api, png_data_url):
    alice = _register(api, "alice@example.com")
    bob = _register(api, "bob@example.com")
    sig_id = _save(api, alice, png_data_url).json()["id"]

    response = api.get(f"/api/signatures/{sig_id}/image", headers=auth(bob))
    assert response.status_code == 404


def test_another_user_cannot_delete(api, png_data_url):
    alice = _register(api, "alice@example.com")
    bob = _register(api, "bob@example.com")
    saved = _save(api, alice, png_data_url).json()
    sig_id = saved["id"]

    forbidden = api.delete(f"/api/signatures/{sig_id}", headers=auth(bob))
    assert forbidden.status_code == 404

    # The signature and its stored bytes survived the failed cross-user delete.
    still_there = api.get(f"/api/signatures/{sig_id}", headers=auth(alice))
    assert still_there.status_code == 200
    image = api.get(f"/api/signatures/{sig_id}/image", headers=auth(alice))
    assert image.status_code == 200


def test_an_owner_can_delete_their_own_signature(api, contractor_token, png_data_url):
    saved = _save(api, contractor_token, png_data_url).json()
    sig_id = saved["id"]

    from app.models.user_signature import UserSignature

    db = api.db_factory()
    try:
        key = db.scalar(select(UserSignature.image_path))
        assert key in api.objects
    finally:
        db.close()

    removed = api.delete(f"/api/signatures/{sig_id}", headers=auth(contractor_token))
    assert removed.status_code == 200

    gone = api.get(f"/api/signatures/{sig_id}", headers=auth(contractor_token))
    assert gone.status_code == 404
    # The stored bytes were removed from object storage too.
    assert key not in api.objects


def test_signatures_require_authentication(api, png_data_url):
    assert api.get("/api/signatures").status_code in (401, 403)
    assert api.post("/api/signatures", json={"image_data": png_data_url}).status_code in (401, 403)


def test_a_saved_signature_is_recorded_in_the_audit_trail(api, contractor_token, png_data_url):
    from app.models.audit import AuditLog

    _save(api, contractor_token, png_data_url)
    db = api.db_factory()
    try:
        actions = set(db.scalars(select(AuditLog.action)))
        assert "user_signature_saved" in actions
    finally:
        db.close()
