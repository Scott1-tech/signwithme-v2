"""Shared route dependencies: upload validation and record loaders."""
from __future__ import annotations

import base64
import binascii
import io
import re
import warnings
from uuid import UUID

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import (
    NotFoundError,
    PayloadTooLargeError,
    PermissionError_,
    ValidationError,
)
from app.core.security import ROLE_ADMIN, ROLE_REVIEWER
from app.models.contract import Contract
from app.models.template import Template
from app.models.user import User
from app.services.signature_image import prepare_signature_image

PDF_MAGIC = b"%PDF-"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|jpg);base64,", re.IGNORECASE)
ANY_DATA_URL_RE = re.compile(r"^data:", re.IGNORECASE)

# Signature image limits. A signature is a small line drawing or a photo of
# one; anything past these bounds is not a signature, it is an attack surface.
MAX_SIGNATURE_IMAGE_BYTES = 2 * 1024 * 1024
# Base64 grows 3 bytes to 4 characters; checked *before* decoding so an
# oversized payload is refused without allocating its decoded form.
MAX_SIGNATURE_B64_CHARS = (MAX_SIGNATURE_IMAGE_BYTES * 4 + 2) // 3 + 4
MAX_SIGNATURE_DIMENSION = 4000
MAX_SIGNATURE_PIXELS = 20_000_000
SIGNATURE_IMAGE_FORMATS = ("PNG", "JPEG")

# Decompression-bomb guard for every Pillow open in this process. The
# explicit dimension check below is the real control (it runs from the header
# before any pixel is decoded); this makes Pillow refuse the same class of
# input everywhere else it is used, e.g. reportlab's ImageReader. Nothing in
# app/ legitimately opens an image anywhere near this size.
Image.MAX_IMAGE_PIXELS = MAX_SIGNATURE_PIXELS


async def read_pdf_upload(file: UploadFile) -> bytes:
    """Read an upload, enforcing type, size and actual PDF content.

    Trusting the declared content type alone would let any file through.
    """
    if file.content_type not in (None, "application/pdf", "application/octet-stream"):
        raise ValidationError(f"Expected a PDF, got content type {file.content_type}")

    data = await file.read()
    if not data:
        raise ValidationError("Uploaded file is empty")
    if len(data) > settings.max_upload_bytes:
        raise ValidationError(
            f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)}MB limit"
        )
    if not data.startswith(PDF_MAGIC):
        raise ValidationError("File is not a valid PDF")
    return data


def decode_signature_image(image_data: str) -> bytes:
    """Turn a client-supplied signature image into safe, normalised PNG bytes.

    Accepts a PNG/JPEG data URL (as the canvas pad and the file picker emit)
    or bare base64. Both signature endpoints go through this single function;
    it is the only place image input is trusted, so every check lives here:

    * size: base64 length is capped before decoding and the decoded bytes
      after it (413);
    * format: decided by magic bytes and by what Pillow actually parses, never
      by the data-URL MIME or a file name. Only PNG and JPEG (422);
    * bombs: dimensions and pixel count are read from the header and bounded
      before any pixel is decoded, and Pillow's own bomb guard is treated as a
      hard error (422);
    * integrity: ``verify()`` then a full decode, so a truncated or corrupt
      file is refused rather than stored (422);
    * sanitising: the image is always re-encoded to RGBA PNG. Nothing the
      client sent -- metadata, extra chunks, a polyglot tail, JPEG EXIF -- is
      persisted. The returned bytes are what gets stored and hashed;
    * clean-up: an opaque image has its paper removed, and every image is
      cropped to its ink with the bottom edge on the lowest ink row
      (``app.services.signature_image``).
    """
    return _normalise_signature_image(_decode_image_payload(image_data))


def normalise_signature_png(data: bytes) -> bytes:
    """Server-made signature bytes (a typed signature, B2) through the same
    gates and clean-up as an uploaded one: size, magic, Pillow limits,
    RGBA re-encode, background removal and ink crop."""
    return _normalise_signature_image(_check_raw_image(data, "Signature image"))


def _decode_image_payload(image_data: str, label: str = "Signature image") -> bytes:
    """Data URL / bare base64 -> raw bytes, with the size and magic-byte gates."""
    text = image_data.strip()
    if ANY_DATA_URL_RE.match(text) and not DATA_URL_RE.match(text):
        # A data URL with any other MIME (svg+xml, gif, webp, text/html ...).
        raise ValidationError("Unsupported image format")
    payload = DATA_URL_RE.sub("", text)

    if len(payload) > MAX_SIGNATURE_B64_CHARS:
        raise PayloadTooLargeError(f"{label} too large")
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError(f"{label} is not valid base64") from exc
    return _check_raw_image(data, label)


def _check_raw_image(data: bytes, label: str) -> bytes:
    if not data:
        raise ValidationError(f"{label} is empty")
    if len(data) > MAX_SIGNATURE_IMAGE_BYTES:
        raise PayloadTooLargeError(f"{label} too large")
    if not data.startswith(PNG_MAGIC) and not data.startswith(JPEG_MAGIC):
        raise ValidationError("Unsupported image format")
    return data


def decode_annotation_image(image_data: str | bytes) -> bytes:
    """An annotation `image` asset (contract v5): the same gates as a signature
    -- size (413), magic bytes, Pillow header-first dimension/bomb limits,
    verify + full decode (422), and ALWAYS a fresh RGBA PNG re-encode, so no
    metadata, extra chunk or polyglot tail the client sent is ever stored.

    The one difference: NO clean-up. A signature has its paper removed and is
    cropped to its ink; a stamp or a logo is kept exactly as it was drawn
    (background, margins and all).

    Takes a data URL / base64 string (JSON upload) or raw bytes (multipart).
    """
    label = "Image"
    if isinstance(image_data, (bytes, bytearray)):
        data = _check_raw_image(bytes(image_data), label)
    else:
        data = _decode_image_payload(image_data, label)
    return _normalise_signature_image(data, clean=False, label=label)


def image_size(png_bytes: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(png_bytes)) as image:
        return image.size


def _normalise_signature_image(
    data: bytes, *, clean: bool = True, label: str = "Signature image"
) -> bytes:
    """Validate ``data`` with Pillow and return it re-encoded as RGBA PNG.

    ``clean`` runs the signature clean-up (background removal + ink crop);
    annotation assets pass False."""
    with warnings.catch_warnings():
        # Past MAX_IMAGE_PIXELS Pillow only *warns* until twice that. A
        # signature past the limit is refused outright, not stored with a log line.
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            # Lazy open: parses the header only, so size/format are known
            # before a single pixel is decoded.
            with Image.open(io.BytesIO(data)) as probe:
                image_format = probe.format
                width, height = probe.size
                if image_format not in SIGNATURE_IMAGE_FORMATS:
                    raise ValidationError("Unsupported image format")
                if (
                    width <= 0
                    or height <= 0
                    or width > MAX_SIGNATURE_DIMENSION
                    or height > MAX_SIGNATURE_DIMENSION
                    or width * height > MAX_SIGNATURE_PIXELS
                ):
                    raise ValidationError(
                        f"{label} dimensions are too large",
                        details={
                            "width": width,
                            "height": height,
                            "max_dimension": MAX_SIGNATURE_DIMENSION,
                            "max_pixels": MAX_SIGNATURE_PIXELS,
                        },
                    )
                probe.verify()

            # verify() leaves the file unusable; reopen for the real decode.
            with Image.open(io.BytesIO(data)) as image:
                if image.format != image_format:
                    raise ValidationError("Unsupported image format")
                rgba = image.convert("RGBA")
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValidationError(f"{label} dimensions are too large") from exc
        except ValidationError:
            raise
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError) as exc:
            # Pillow surfaces corrupt/truncated/mismatched files as any of these.
            raise ValidationError(f"{label} is corrupt or unreadable") from exc

    # Only a fully validated, decoded image reaches the clean-up: background
    # removal for opaque photos/scans, then crop to the ink (see the module).
    if clean:
        rgba = prepare_signature_image(rgba)

    buffer = io.BytesIO()
    rgba.save(buffer, format="PNG")
    return buffer.getvalue()


def get_template_or_404(db: Session, template_id: UUID) -> Template:
    template = db.get(Template, template_id)
    if template is None:
        raise NotFoundError(f"Template {template_id} not found")
    return template


def get_contract_or_404(db: Session, contract_id: UUID) -> Contract:
    contract = db.get(Contract, contract_id)
    if contract is None:
        raise NotFoundError(f"Contract {contract_id} not found")
    return contract


def can_edit_document(contract: Contract, user: User) -> bool:
    """PDF editor v2, §11 Q2: CONTENT of the document may be edited by an
    admin, a reviewer, or the account that uploaded the contract. A contractor
    who sees the contract through `contractor_email` may add markup (and, from
    B3, fill forms) but never change the document itself."""
    if user.role in (ROLE_ADMIN, ROLE_REVIEWER):
        return True
    return contract.uploaded_by == user.id


def assert_can_edit_document(contract: Contract, user: User) -> None:
    """403 unless `can_edit_document`. Call after `assert_can_view_contract`."""
    if not can_edit_document(contract, user):
        raise PermissionError_(
            "Only an admin, a reviewer or the uploader can edit this document's content"
        )


def assert_can_view_contract(contract: Contract, user: User) -> None:
    """Contractors see only their own contracts; reviewers and admins see all."""
    if user.role in (ROLE_ADMIN, ROLE_REVIEWER):
        return
    if contract.uploaded_by == user.id:
        return
    if contract.contractor_email and contract.contractor_email.lower() == user.email.lower():
        return
    raise PermissionError_("You do not have access to this contract")
