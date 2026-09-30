"""Signature capture, placement, and final PDF generation."""
from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pydantic
from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    MAX_SIGNATURE_B64_CHARS,
    MAX_SIGNATURE_IMAGE_BYTES,
    assert_can_view_contract,
    decode_annotation_image,
    decode_signature_image,
    get_contract_or_404,
    image_size,
)
from app.core.database import get_db
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionError_,
    StorageError,
    ValidationError,
)
from app.core.security import ROLE_ADMIN, client_ip, get_current_user
from app.models.audit import AuditLog
from app.models.contract import Contract
from app.models.signature import Signature
from app.models.template import Template
from app.models.user import User
from app.schemas.contract import (
    ANNOTATION_ASSET_ID_PATTERN,
    AnnotationAssetIn,
    AnnotationAssetOut,
    BulkSignatureFailed,
    BulkSignaturePlaced,
    BulkSignaturePlacementIn,
    BulkSignatureResult,
    BulkSignatureSkipped,
    ContractFinalization,
    FinalizeLayout,
    FinalizeResponse,
    SignatureOut,
    SignaturePlacementIn,
)
from app.services import audit
from app.services.comparison_engine import (
    _index_schema,
    _is_slot_field,
    _paired_date_ids,
    pending_signature_slots,
)
from app.services.geometry import BBox
from app.services.pdf_builder import (
    RULE_UNSET,
    AnnotationPlacement,
    AnnotationPlacementError,
    DatePlacement,
    Layout,
    PageOutOfRangeError,
    PdfBuilder,
    SignaturePlacement,
    sha256_bytes,
)
from app.services.review_override import (
    INCONSISTENCY_PREFIX,
    NEW_ISSUES_REASON,
    critical_inconsistencies,
    inconsistency_key,
    override_new_issues,
    override_permits_signing,
)
from app.services.rule_locator import (
    RULE_Y_KEY,
    contract_page_count,
    ensure_template_rules,
    page_in_range,
)
from app.services.documents import signing_source
from app.services.storage import annotation_asset_key, final_key, signature_key, storage
from app.services.validators import format_date, parse_date

router = APIRouter(prefix="/api/contracts", tags=["signatures"])


def _assert_signable(contract: Contract) -> dict:
    """The authoritative signing gate.

    The frontend hides the finalise button when `can_sign` is false, but that is
    a convenience. This check is the control: without it, a client could post
    straight to this endpoint and sign a contract with critical FMCSA
    violations outstanding.

    A reviewer override (services.review_override) lets an approved contract
    through with the critical missing fields / inconsistencies it accepted. It
    is re-checked against the CURRENT comparison here, so an FMCSA violation or
    any critical issue the reviewer did not accept blocks signing again.
    """
    comparison = contract.comparison_result or {}
    if contract.status in ("uploaded", "parsing"):
        raise ConflictError("Contract has not finished analysis")
    if contract.status == "completed":
        raise ConflictError("Contract is already finalised")
    if not comparison:
        raise ConflictError("Contract has not been compared against its template")
    if contract.review_status != "approved":
        raise ConflictError("Contract must be approved by a reviewer before signing")
    if not comparison.get("can_sign", False) and contract.review_override:
        # Subset gate: the override stands only while every current critical
        # issue was accepted by the reviewer (FMCSA never is).
        new_issues = override_new_issues(contract)
        if new_issues:
            raise ConflictError(
                "New critical issues appeared after the reviewer override; "
                "the contract must be reviewed again.",
                details={"reason": NEW_ISSUES_REASON, "new_issues": new_issues},
            )
    if not comparison.get("can_sign", False) and not override_permits_signing(contract):
        raise ConflictError(
            "Contract has unresolved critical issues and cannot be signed: "
            f"{len(comparison.get('missing_required', []))} missing required field(s), "
            f"{len(comparison.get('inconsistencies', []))} inconsistency(ies), "
            f"{len(comparison.get('fmcsa_violations', []))} FMCSA violation(s)"
        )
    return comparison


# Why a requested field id could not take a signature. Closed set: the bulk
# response carries these verbatim, so the frontend can branch on them.
SLOT_UNKNOWN = "unknown_field"
SLOT_WRONG_TYPE = "not_a_signature_field"
SLOT_TAKEN = "already_signed"
SLOT_PAGE_OUT_OF_RANGE = "page_out_of_range"
# PDF editor v2: the slot's template page was removed from the current version.
SLOT_PAGE_REMOVED = "page_removed"


def _assert_page_exists(page: int, page_count: int | None) -> None:
    """422 for a placement on a page the uploaded document does not have: the
    builder could only drop it, and a signature must never vanish silently."""
    if not page_in_range(page, page_count):
        raise ValidationError(
            f"Page {page} does not exist in this contract ({page_count} page(s))",
            details={"reason": SLOT_PAGE_OUT_OF_RANGE, "page": page, "page_count": page_count},
        )


def _stored_rule(field: dict | None):
    """The template's measured `rule_y` for a slot, or "measure it live"."""
    if field is None or RULE_Y_KEY not in field:
        return RULE_UNSET
    return field[RULE_Y_KEY]


def _resolve_signature_slot(
    index: dict[str, dict], field_id: str
) -> tuple[tuple[int, dict] | None, str | None]:
    """Resolve a template signature slot to its (page, bbox), or say why it is not one.

    The single source of truth for "is this a signature slot of *this*
    contract's template", shared by the single and the bulk placement endpoint
    so the two can never drift apart on what is an acceptable target. Returning
    the template's own coordinates here is also what stops a client steering a
    signature onto a different clause: no caller supplies a bbox for a slot.
    """
    field = index.get(field_id)
    if field is None:
        return None, SLOT_UNKNOWN
    if (
        field.get("field_type") != "signature"
        or not field.get("bbox")
        or field.get("page") is None
    ):
        return None, SLOT_WRONG_TYPE
    return (int(field["page"]), dict(field["bbox"])), None


def _template_index(db: Session, contract: Contract) -> dict[str, dict]:
    template = db.get(Template, contract.template_id)
    if template is None:
        raise NotFoundError("Template for this contract no longer exists")
    return _index_schema(template.field_schema)


def _taken_slot_ids(db: Session, contract: Contract) -> set[str]:
    """Slot ids on this contract that already hold a signature."""
    return {
        row.field_id
        for row in db.scalars(
            select(Signature).where(
                Signature.contract_id == contract.id, Signature.field_id.is_not(None)
            )
        )
    }


def _store_signature(
    db: Session,
    contract: Contract,
    user: User,
    request: Request,
    *,
    page: int,
    bbox: dict,
    field_id: str | None,
    signer_name: str | None,
    signer_email: str | None,
    signer_role: str,
    image_bytes: bytes,
    image_hash: str,
) -> Signature:
    """Create one signature row, write its own image object, and audit it.

    Every placement -- single or bulk -- goes through here, so `placed_by_user_id`
    (which the delete authorisation depends on) and the `signature_added` audit
    entry can never be omitted by one caller and not the other. Each row gets
    its own storage key even when the bytes are identical, so removing one
    signature cannot strip the image out from under the others.
    """
    signature = Signature(
        id=uuid4(),
        contract_id=contract.id,
        signer_name=signer_name or user.full_name,
        signer_email=signer_email or user.email,
        signer_role=signer_role,
        signature_image_path="",
        signature_hash=image_hash,
        page_number=page,
        bbox=bbox,
        field_id=field_id,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        placed_by_user_id=user.id,
        # PDF editor v2: the stable id of the page it sits on (None = legacy).
        page_uid=signing_source(contract).page_uid_at(page),
    )
    key = signature_key(contract.id, signature.id)
    storage.put_bytes(key, image_bytes, content_type="image/png")
    signature.signature_image_path = key

    db.add(signature)
    audit.log(
        db,
        audit.SIGNATURE_ADDED,
        contract_id=contract.id,
        actor=user,
        details={
            "signature_id": str(signature.id),
            "field_id": field_id,
            "page": page,
            "signature_hash": signature.signature_hash,
        },
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return signature


def _assert_can_remove_signature(signature: Signature, user: User) -> None:
    """Only the account that placed a signature, or an admin, may remove it.

    Being able to view the contract is not enough: a contractor must not be
    able to strip the company representative's signature (or vice versa). A
    row with no recorded placer predates the column, so its placer is unknown
    and only an admin may remove it.
    """
    if user.role == ROLE_ADMIN:
        return
    if signature.placed_by_user_id is not None and signature.placed_by_user_id == user.id:
        return
    raise PermissionError_("Only the user who placed this signature, or an admin, can remove it")


@router.post("/{contract_id}/signatures", response_model=SignatureOut, status_code=201)
def place_signature(
    contract_id: UUID,
    placement: SignaturePlacementIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Store a captured signature image and its placement.

    The canvas produces base64; it is uploaded to object storage here so the
    signature row can hold a path and a hash, as the audit trail requires.
    """
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    _assert_signable(contract)

    if placement.field_id is not None:
        # Slot placement: the template decides where the image goes. The
        # client's page/bbox are ignored so it cannot move a signature onto a
        # different clause than the one the slot belongs to.
        resolved, _reason = _resolve_signature_slot(
            _template_index(db, contract), placement.field_id
        )
        if resolved is None:
            raise ValidationError(
                f"{placement.field_id!r} is not a signature field of this contract's template"
            )
        page, bbox = resolved
        # Template page -> position in the current version (identity for a
        # legacy contract and for a document without page ops).
        source = signing_source(contract)
        position = source.position_for(page)
        if position is None:
            raise ValidationError(
                f"The page of slot {placement.field_id!r} is no longer in this document",
                details={"reason": SLOT_PAGE_REMOVED, "page": page},
            )
        # A page rotated since the template: the slot box moves with it.
        bbox = source.template_bbox(page, bbox)
        page = position
        _assert_page_exists(page, contract_page_count(contract))
        if placement.field_id in _taken_slot_ids(db, contract):
            raise ConflictError(
                f"Slot {placement.field_id!r} already has a signature; remove it first"
            )
    else:
        # Free placement: the schema validator guarantees both are present.
        page, bbox = placement.page, placement.bbox.model_dump()
        _assert_page_exists(page, contract_page_count(contract))

    image_bytes = decode_signature_image(placement.image_data)
    signature = _store_signature(
        db,
        contract,
        user,
        request,
        page=page,
        bbox=bbox,
        field_id=placement.field_id,
        signer_name=placement.signer_name,
        signer_email=str(placement.signer_email) if placement.signer_email else None,
        signer_role=placement.signer_role,
        image_bytes=image_bytes,
        image_hash=sha256_bytes(image_bytes),
    )
    db.commit()
    db.refresh(signature)
    return signature


@router.post("/{contract_id}/signatures/bulk", response_model=BulkSignatureResult)
def place_signatures_bulk(
    contract_id: UUID,
    placement: BulkSignaturePlacementIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Apply one captured image to many template signature slots.

    Exists because "apply to all" otherwise re-uploads the same 2MB image once
    per slot and pays for a full Pillow decode/re-encode each time. Here the
    image is validated and normalised **once** -- so an SVG, an oversized
    payload or a decompression bomb is refused for the whole request, which is
    correct: there is only one image -- and the resulting bytes are written to
    one storage object per slot.

    Nothing else is relaxed. Same access check, same signing gate, same
    "`field_id` must be a signature slot of this contract's template" rule, same
    template-supplied coordinates (this endpoint accepts no bbox at all), same
    refusal to overwrite a slot that is already signed, and the same
    `placed_by_user_id` + `signature_added` audit entry per row.

    Always 200: per-slot problems are reported per slot (`skipped` / `failed`)
    instead of discarding the placements that succeeded.
    """
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    _assert_signable(contract)

    index = _template_index(db, contract)
    taken = _taken_slot_ids(db, contract)
    page_count = contract_page_count(contract)

    # One decode for the whole request: the single expensive step this endpoint
    # exists to avoid repeating.
    image_bytes = decode_signature_image(placement.image_data)
    image_hash = sha256_bytes(image_bytes)

    placed: list[BulkSignaturePlaced] = []
    skipped: list[BulkSignatureSkipped] = []
    failed: list[BulkSignatureFailed] = []

    source = signing_source(contract)
    for field_id in placement.field_ids:
        resolved, reason = _resolve_signature_slot(index, field_id)
        if resolved is None:
            failed.append(BulkSignatureFailed(field_id=field_id, reason=reason))
            continue
        position = source.position_for(resolved[0])
        if position is None:
            failed.append(BulkSignatureFailed(field_id=field_id, reason=SLOT_PAGE_REMOVED))
            continue
        resolved = (position, source.template_bbox(resolved[0], resolved[1]))
        if not page_in_range(resolved[0], page_count):
            failed.append(BulkSignatureFailed(field_id=field_id, reason=SLOT_PAGE_OUT_OF_RANGE))
            continue
        if field_id in taken:
            skipped.append(BulkSignatureSkipped(field_id=field_id, reason=SLOT_TAKEN))
            continue

        page, bbox = resolved
        signature = _store_signature(
            db,
            contract,
            user,
            request,
            page=page,
            bbox=bbox,
            field_id=field_id,
            signer_name=placement.signer_name,
            signer_email=str(placement.signer_email) if placement.signer_email else None,
            signer_role=placement.signer_role,
            image_bytes=image_bytes,
            image_hash=image_hash,
        )
        # Guards against a slot being filled twice within one request; the
        # duplicate validator already rejects the obvious case.
        taken.add(field_id)
        placed.append(
            BulkSignaturePlaced(
                field_id=field_id,
                signature_id=signature.id,
                page_number=page,
                bbox=bbox,
            )
        )

    if placed:
        audit.log(
            db,
            audit.SIGNATURES_BULK_ADDED,
            contract_id=contract.id,
            actor=user,
            details={
                "requested": len(placement.field_ids),
                "placed": len(placed),
                "skipped": len(skipped),
                "failed": len(failed),
                "signature_hash": image_hash,
            },
            ip_address=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    db.commit()

    return BulkSignatureResult(
        contract_id=contract.id, placed=placed, skipped=skipped, failed=failed
    )


@router.get("/{contract_id}/signatures", response_model=list[SignatureOut])
def list_signatures(
    contract_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    return list(
        db.scalars(select(Signature).where(Signature.contract_id == contract.id).order_by(Signature.signed_at))
    )


@router.get("/{contract_id}/signatures/{signature_id}/image")
def get_signature_image(
    contract_id: UUID,
    signature_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The stored (normalised) PNG of one placed signature, for the signing
    preview. Same access rule as viewing the contract; a signature id from a
    different contract is a 404, never a cross-contract read."""
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    signature = db.get(Signature, signature_id)
    if signature is None or signature.contract_id != contract.id or not signature.signature_image_path:
        raise NotFoundError("Signature not found on this contract")
    return Response(
        content=storage.get_bytes(signature.signature_image_path),
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{contract_id}/signatures/{signature_id}")
def remove_signature(
    contract_id: UUID,
    signature_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    if contract.status == "completed":
        raise ConflictError("Cannot remove a signature from a finalised contract")

    signature = db.get(Signature, signature_id)
    if signature is None or signature.contract_id != contract.id:
        raise NotFoundError("Signature not found on this contract")
    _assert_can_remove_signature(signature, user)

    storage.delete(signature.signature_image_path)
    db.delete(signature)
    audit.log(
        db,
        audit.SIGNATURE_REMOVED,
        contract_id=contract.id,
        actor=user,
        details={"signature_id": str(signature_id)},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"signature_id": str(signature_id), "removed": True}


# -- annotation image assets (contract v5) --------------------------------------

# Abuse bound on uploads per contract (counted from the audit trail, which is
# append-only, so deleting nothing resets it). A package needs a handful of
# stamps/logos; this is two orders of magnitude above that.
MAX_ANNOTATION_ASSETS_PER_CONTRACT = 100
# A JSON body is the base64 image plus a few bytes of framing.
_MAX_ASSET_JSON_BYTES = MAX_SIGNATURE_B64_CHARS + 1024
ASSET_UNKNOWN = "unknown_annotation_asset"


async def _read_asset_upload(request: Request, db: Session | None = None, user: User | None = None):
    """Normalised RGBA PNG from either upload form:

    * `multipart/form-data` with the image in a `file` part (what a file picker
      posts), or
    * JSON `{"image_data": "<data URL or base64>"}` (what the signature pad
      endpoints take), or
    * JSON `{"from_asset_id": "<hex>"}` (editor v2 B2): a copy of the caller's
      OWN gallery image (another user's id is a 404, like a missing one).

    The first two end in `deps.decode_annotation_image`, the same gates as a
    signature minus its background removal. Returns (png, gallery asset|None).
    """
    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith("multipart/form-data"):
        form = await request.form(max_files=1, max_fields=4)
        try:
            upload = form.get("file")
            if upload is None or isinstance(upload, str):
                raise ValidationError("Expected the image in a multipart 'file' part")
            data = await upload.read(MAX_SIGNATURE_IMAGE_BYTES + 1)
        finally:
            await form.close()
        if len(data) > MAX_SIGNATURE_IMAGE_BYTES:
            raise PayloadTooLargeError("Image too large")
        return decode_annotation_image(data), None

    body = await request.body()
    if len(body) > _MAX_ASSET_JSON_BYTES:
        raise PayloadTooLargeError("Image too large")
    try:
        payload = AnnotationAssetIn.model_validate_json(body or b"{}")
    except pydantic.ValidationError as exc:
        # Through the app's own 422 handler, which never echoes the input.
        raise RequestValidationError(
            [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]
        ) from None
    if payload.from_asset_id is not None:
        from app.api.assets import owned_asset

        source = owned_asset(db, payload.from_asset_id, user)
        return storage.get_bytes(source.storage_key), source
    return decode_annotation_image(payload.image_data), None


def _stored_asset(contract_id, asset_id: str) -> bytes | None:
    """The asset's bytes if it exists UNDER THIS CONTRACT, else None."""
    try:
        return storage.get_bytes(annotation_asset_key(contract_id, asset_id))
    except (StorageError, KeyError, FileNotFoundError):
        return None


@router.post(
    "/{contract_id}/annotation-assets", response_model=AnnotationAssetOut, status_code=201
)
async def upload_annotation_asset(
    contract_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Store an image (stamp, logo, picture) for an `image` annotation.

    Authorisation is that of PLACING A SIGNATURE, not of viewing: the caller
    must be able to see the contract (`assert_can_view_contract`) AND the
    contract must be signable right now (`_assert_signable`: analysed,
    approved, not finalised, no unaccepted critical issue). The asset exists
    only to be drawn by `finalize`, which applies the same two checks, so an
    upload that finalize could never use is refused before anything is
    written -- no storage growth on completed, rejected or unapproved contracts.

    Both checks run before the body is read, so an unauthorised caller cannot
    make the server decode anything.
    """
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    _assert_signable(contract)

    uploaded = db.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.contract_id == contract.id,
            AuditLog.action == audit.ANNOTATION_ASSET_ADDED,
        )
    )
    if (uploaded or 0) >= MAX_ANNOTATION_ASSETS_PER_CONTRACT:
        raise ConflictError(
            f"A contract can carry at most {MAX_ANNOTATION_ASSETS_PER_CONTRACT} annotation images",
            details={"limit": MAX_ANNOTATION_ASSETS_PER_CONTRACT},
        )

    png, gallery = await _read_asset_upload(request, db, user)
    width, height = image_size(png)
    asset_uuid = uuid4()
    asset_id = asset_uuid.hex
    key = annotation_asset_key(contract.id, asset_id)
    storage.put_bytes(key, png, content_type="image/png")
    if gallery is not None:
        # Editor v2 B2: a contract-scope COPY of the gallery image; removing it
        # from the gallery later does not touch this one.
        from app.models.asset import Asset

        db.add(
            Asset(
                id=asset_uuid,
                scope="contract",
                contract_id=contract.id,
                kind=gallery.kind,
                label=gallery.label,
                storage_key=key,
                sha256=gallery.sha256,
                width_px=width,
                height_px=height,
                byte_size=len(png),
                source_asset_id=gallery.id,
            )
        )
    audit.log(
        db,
        audit.ANNOTATION_ASSET_ADDED,
        contract_id=contract.id,
        actor=user,
        details={
            "asset_id": asset_id,
            "sha256": sha256_bytes(png),
            "width_px": width,
            "height_px": height,
            **({"from_asset_id": gallery.id.hex} if gallery is not None else {}),
        },
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return AnnotationAssetOut(asset_id=asset_id, width_px=width, height_px=height)


@router.get("/{contract_id}/annotation-assets/{asset_id}")
def get_annotation_asset(
    contract_id: UUID,
    asset_id: str = Path(pattern=ANNOTATION_ASSET_ID_PATTERN),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The stored (re-encoded) PNG, for the editor preview. Viewing, so the
    viewing rule (`assert_can_view_contract`) -- it keeps working after the
    contract is finalised. An asset id of another contract is a 404: the key is
    built from THIS contract's id, so it does not exist here."""
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    data = _stored_asset(contract.id, asset_id)
    if data is None:
        raise NotFoundError("Annotation image not found on this contract")
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/{contract_id}/finalize", response_model=FinalizeResponse)
def finalize_contract(
    contract_id: UUID,
    finalization: ContractFinalization,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate the final signed PDF with all signatures and dates overlaid."""
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    _assert_signable(contract)

    template = db.get(Template, contract.template_id)
    if template is None:
        raise NotFoundError("Template for this contract no longer exists")
    # `rule_y` is derived once for templates analysed before it existed, so the
    # PDF uses the same line the signing preview was shown.
    index = _index_schema(ensure_template_rules(db, template))
    page_count = contract_page_count(contract)
    layout = finalization.layout or FinalizeLayout()
    _assert_layout_slots(layout, index, page_count)

    signatures = list(
        db.scalars(select(Signature).where(Signature.contract_id == contract.id))
    )
    if not signatures:
        raise ConflictError("No signatures have been placed on this contract")

    # PDF editor v2: the signed document is the CURRENT version; template
    # pages map to positions there, and a signature follows its page by
    # `page_uid` (a structure commit may have renumbered nothing, but a
    # position is never trusted over the stable id).
    signed_source = signing_source(contract)
    for row in signatures:
        position = signed_source.position_of_uid(row.page_uid) if row.page_uid else None
        if position is not None and position != row.page_number:
            row.page_number = position

    date_placements = _resolve_dates(finalization, signatures, index, signed_source)

    # A placement on a page the uploaded document does not have could only be
    # dropped by the builder. Refuse instead: never a silently missing mark.
    stray_signatures = [row for row in signatures if not page_in_range(row.page_number, page_count)]
    stray_dates = [p for p in date_placements if not page_in_range(p.page, page_count)]
    if stray_signatures or stray_dates:
        raise ConflictError(
            f"Contract cannot be finalised: {len(stray_signatures)} signature(s) and "
            f"{len(stray_dates)} date(s) are placed on pages this {page_count}-page document "
            "does not have; remove them first",
            details={
                "reason": SLOT_PAGE_OUT_OF_RANGE,
                "page_count": page_count,
                "signatures": [
                    {"signature_id": str(r.id), "field_id": r.field_id, "page": r.page_number}
                    for r in stray_signatures
                ],
                "date_fields": [{"field_id": p.field_id, "page": p.page} for p in stray_dates],
            },
        )

    # Every required signature slot and date field the form still shows as
    # empty must be covered by a placed signature or a supplied date. The
    # comparison reports these as `signature_slots` (never blocking approval);
    # this is the control that stops an unsigned clause reaching the final PDF.
    from app.services.document_review import present_pages

    pending = pending_signature_slots(
        contract.extracted_data, index, page_count=page_count, present_pages=present_pages(signed_source)
    )
    required = [slot for slot in pending if slot["required"]]
    signed_ids = {row.field_id for row in signatures if row.field_id}
    dated_ids = {placement.field_id for placement in date_placements if placement.field_id}
    missing_sigs = [
        s for s in required if s["kind"] == "signature" and s["field_id"] not in signed_ids
    ]
    missing_dates = [
        s for s in required if s["kind"] == "date" and s["field_id"] not in dated_ids
    ]
    if missing_sigs or missing_dates:
        raise ConflictError(
            "Contract cannot be finalised: "
            f"{len(missing_sigs)} signature slot(s) and {len(missing_dates)} date field(s) "
            "are still empty",
            details={
                "missing_signature_slots": [_slot_ref(s) for s in missing_sigs],
                "missing_date_fields": [_slot_ref(s) for s in missing_dates],
            },
        )

    signature_placements = [
        SignaturePlacement(
            page=row.page_number,
            bbox=BBox.from_dict(row.bbox),
            image_bytes=storage.get_bytes(row.signature_image_path),
            field_id=row.field_id,
            # Contract v1.1: a free placement (no slot) never snaps to a line --
            # line = bbox bottom, only the GLOBAL signature offset applies
            # (Layout.slot_offset(None) is 0), exactly as the preview draws it.
            rule_y=(
                _stored_rule(index.get(row.field_id))
                if row.field_id and not signed_source.rotated_since_template((index.get(row.field_id) or {}).get("page"))
                else None
            ),
        )
        for row in signatures
    ]

    # PDF editor v2: the final is built from the CURRENT version (the document
    # that was reviewed and signed); a legacy contract (no `documents` row)
    # reads the original upload exactly as before.
    source = storage.get_bytes(signed_source.bytes_key)
    legacy_models, v7_elements, markup_source = _finalize_markup(finalization, contract, signed_source)
    annotations = _annotation_placements(finalization, contract.id, legacy_models)
    v7_placements = _v7_placements(v7_elements, contract.id, source)
    try:
        from app.services.final_pipeline import FinalInputs, build_final

        result = build_final(
            FinalInputs(
                source=source,
                signatures=signature_placements,
                dates=date_placements,
                legacy_annotations=annotations,
                v7=v7_placements,
                layout=Layout(**layout.model_dump()),
                metadata={
                    "title": f"Signed Contract - {contract.contractor_name or contract.id}",
                    **_override_metadata(contract, index),
                },
            )
        )
    except AnnotationPlacementError as exc:
        # A bad request, not a stale stored row: the annotations arrived in this
        # very call, so the client can fix them and retry. `details` carries the
        # client's own element ids and nothing the user typed, for the same
        # reason the audit entry carries counts only (`_annotation_stats`).
        raise ValidationError(
            str(exc),
            details={
                "reason": exc.reason,
                "page_count": exc.page_count,
                "annotations": exc.entries[:50],
            },
        ) from exc
    except PageOutOfRangeError as exc:
        # Defence in depth: the page count above was unknown or stale.
        raise ConflictError(
            str(exc),
            details={"reason": SLOT_PAGE_OUT_OF_RANGE, "pages": exc.pages, "page_count": exc.page_count},
        ) from exc

    key = final_key(contract.id)
    storage.put_bytes(key, result.pdf_bytes)
    contract.final_file_path = key
    contract.final_file_hash = result.sha256
    contract.status = "completed"

    for placement in date_placements:
        audit.log(
            db,
            audit.DATE_ADDED,
            contract_id=contract.id,
            actor=user,
            details={"field_id": placement.field_id, "page": placement.page, "value": placement.value},
        )
    audit.log(
        db,
        audit.CONTRACT_FINALIZED,
        contract_id=contract.id,
        actor=user,
        details={
            "signatures": len(signature_placements),
            "dates": len(date_placements),
            # Recorded so any later copy can be checked against the original.
            "final_file_hash": result.sha256,
            "page_count": result.page_count,
            # How the marks were positioned, so the PDF can be re-derived.
            # (v6 date_bold/date_italic only when set: a v5 body records as before.)
            "layout": layout.audit_record(),
            # Counts only -- see `_annotation_stats`.
            "annotations": _annotation_stats(annotations, [p.element for p in v7_placements]),
            # Editor v2: where the marks came from (only when not the body).
            **({"markup_source": markup_source} if markup_source != "body" else {}),
            # Editor v2 only (absent for a legacy contract): which version was signed.
            **(
                {}
                if signed_source.legacy
                else {"source_version_no": signed_source.version_no, "source_file_hash": signed_source.file_hash}
            ),
        },
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    return FinalizeResponse(
        contract_id=contract.id,
        status="completed",
        file_hash=result.sha256,
        download_url=f"/api/download/{contract.id}/final",
    )


ANNOTATIONS_IN_TWO_PLACES = "annotations_in_two_places"


def _finalize_markup(finalization: ContractFinalization, contract: Contract, source):
    """(legacy models, v7 element dicts with `page`, source label).

    Editor v2: markup saved on the document (`documents.markup`, page by
    `page_uid`) is what the final carries. The body's `annotations` are the
    pre-v2 way; both at once is a client bug -> 422
    `annotations_in_two_places` (never a silent pick of one)."""
    from app.schemas.contract import ANNOTATION_MODELS, V7_KINDS

    document = getattr(contract, "document", None)
    server = list(document.markup or []) if document is not None else []
    body = list(finalization.annotations or [])
    if server and body:
        raise ValidationError(
            "The document has saved markup; do not send 'annotations' in the finalize body as well",
            details={"reason": ANNOTATIONS_IN_TWO_PLACES, "saved": len(server), "sent": len(body)},
        )
    if not server:
        legacy = [note for note in body if note.kind not in V7_KINDS]
        v7 = [
            {**note.model_dump(mode="json", exclude_unset=False), "page": note.page}
            for note in body if note.kind in V7_KINDS
        ]
        return legacy, v7, "body"
    legacy, v7, stray = [], [], []
    for element in server:
        position = source.position_of_uid(element.get("page_uid"))
        if position is None:
            stray.append({"id": element.get("id"), "kind": element.get("kind"), "page_uid": element.get("page_uid")})
            continue
        data = {k: v for k, v in element.items() if k != "page_uid"}
        model = ANNOTATION_MODELS[data["kind"]].model_validate({**data, "page": position})
        if data["kind"] in V7_KINDS:
            v7.append({**model.model_dump(mode="json"), "page": position})
        else:
            legacy.append(model)
    if stray:
        raise ValidationError(
            f"{len(stray)} saved markup element(s) are on a page this document no longer has",
            details={"reason": AnnotationPlacementError.PAGE_OUT_OF_RANGE, "annotations": stray[:50]},
        )
    return legacy, v7, "server"


def _v7_placements(elements: list[dict], contract_id, source_pdf: bytes) -> list:
    """v7 elements -> engine placements: page on the document, geometry on
    the (unrotated) page, `image2` asset under THIS contract (422 otherwise)."""
    if not elements:
        return []
    from app.services import pdf_engine
    from app.services.pdf_engine.markup import Placement, element_points

    pages, _warnings = pdf_engine.inspect_pages(source_pdf)
    placements, stray, outside, unknown = [], [], [], []
    assets: dict[str, bytes | None] = {}
    for element in elements:
        page = int(element["page"])
        if not 1 <= page <= len(pages):
            stray.append({"id": element["id"], "kind": element["kind"], "page": page})
            continue
        info = pages[page - 1]
        if not all(-0.01 <= x <= info.width_pt + 0.01 and -0.01 <= y <= info.height_pt + 0.01
                   for x, y in element_points(element)):
            outside.append({"id": element["id"], "kind": element["kind"], "page": page,
                            "page_width_pt": info.width_pt, "page_height_pt": info.height_pt})
            continue
        image = None
        if element["kind"] == "image2":
            if element["asset_id"] not in assets:
                assets[element["asset_id"]] = _stored_asset(contract_id, element["asset_id"])
            image = assets[element["asset_id"]]
            if image is None:
                unknown.append({"id": element["id"], "kind": "image2", "page": page})
                continue
        clean = {k: v for k, v in element.items() if k != "page"}
        placements.append(Placement(element=clean, page_index=page - 1, image_bytes=image))
    if unknown:
        raise ValidationError(
            f"{len(unknown)} image annotation(s) name an image that was not uploaded to this contract",
            details={"reason": ASSET_UNKNOWN, "annotations": unknown[:50]},
        )
    if stray or outside:
        reason = AnnotationPlacementError.PAGE_OUT_OF_RANGE if stray else AnnotationPlacementError.OUTSIDE_PAGE
        raise ValidationError(
            f"{len(stray) + len(outside)} annotation(s) cannot be placed on this {len(pages)}-page document",
            details={"reason": reason, "page_count": len(pages), "annotations": (stray + outside)[:50]},
        )
    return placements


def _annotation_placements(
    finalization: ContractFinalization, contract_id, notes: list | None = None
) -> list[AnnotationPlacement]:
    """The hand-placed elements, as the builder wants them (contracts v4, v5).

    A straight hand-over: the schema has already settled every bound that does
    not need the document itself (kind, id, text, font, size, colour, counts),
    and the page and the geometry are settled by the builder, which has the
    real page sizes. The one lookup made here is the `image` asset: it must
    exist under THIS contract's storage prefix, or the request is a 422 -- an
    asset id from another contract resolves to nothing (no IDOR).
    """
    placements: list[AnnotationPlacement] = []
    assets: dict[str, bytes | None] = {}
    unknown: list[dict] = []
    for note in (finalization.annotations or []) if notes is None else notes:
        common = {"id": note.id, "kind": note.kind, "page": note.page, "group_id": note.group_id}
        kind = note.kind
        if kind in ("text", "date", "check", "cross"):
            placement = AnnotationPlacement(
                **common, x_pt=note.x_pt, y_pt=note.y_pt, text=note.text,
                font=note.font, size_pt=note.size_pt, color=note.color,
                bold=note.bold, italic=note.italic,
            )
        elif kind in ("highlight", "underline", "strikeout"):
            placement = AnnotationPlacement(
                **common, color=note.color,
                rects=tuple((r.x_pt, r.y_pt, r.w_pt, r.h_pt) for r in note.rects),
            )
        elif kind == "ink":
            placement = AnnotationPlacement(
                **common, color=note.color, stroke_pt=note.stroke_pt,
                paths=tuple(tuple((p.x_pt, p.y_pt) for p in path) for path in note.paths),
            )
        elif kind in ("rect", "ellipse"):
            placement = AnnotationPlacement(
                **common, x_pt=note.x_pt, y_pt=note.y_pt, w_pt=note.w_pt, h_pt=note.h_pt,
                stroke_pt=note.stroke_pt, color=note.color, fill=note.fill,
            )
        elif kind in ("line", "arrow"):
            placement = AnnotationPlacement(
                **common, x_pt=note.x1_pt, y_pt=note.y1_pt, x2_pt=note.x2_pt, y2_pt=note.y2_pt,
                stroke_pt=note.stroke_pt, color=note.color,
            )
        elif kind == "note":
            placement = AnnotationPlacement(
                **common, x_pt=note.x_pt, y_pt=note.y_pt, text=note.text, color=note.color,
            )
        elif kind == "image":
            if note.asset_id not in assets:
                assets[note.asset_id] = _stored_asset(contract_id, note.asset_id)
            image = assets[note.asset_id]
            if image is None:
                unknown.append({"id": note.id, "kind": kind, "page": note.page})
            placement = AnnotationPlacement(
                **common, x_pt=note.x_pt, y_pt=note.y_pt, w_pt=note.w_pt, h_pt=note.h_pt,
                asset_id=note.asset_id, image_bytes=image,
            )
        else:  # whiteout; the schema is a closed set
            placement = AnnotationPlacement(
                **common, x_pt=note.x_pt, y_pt=note.y_pt, w_pt=note.w_pt, h_pt=note.h_pt,
            )
        placements.append(placement)
    if unknown:
        raise ValidationError(
            f"{len(unknown)} image annotation(s) name an image that was not uploaded "
            "to this contract",
            details={"reason": ASSET_UNKNOWN, "annotations": unknown[:50]},
        )
    return placements


def _annotation_stats(annotations: list[AnnotationPlacement], v7: list[dict] | None = None) -> dict:
    """What the audit trail records about the hand-placed elements: HOW MANY of
    each kind, plus WHERE every whiteout is -- and nothing else.

    Deliberately no text, no ids and (whiteouts aside) no coordinates. A free
    annotation or a sticky note is whatever the user typed -- a name, an SSN, a
    note about a medical card -- and the audit table is read by every admin,
    exported, and kept for years. The counts are enough to answer "was this
    page annotated, and with what sort of mark"; the content itself lives only
    in the signed PDF, which is access-controlled and covered by
    `final_file_hash` in the same entry.

    Whiteouts are the exception because they HIDE original content: the list
    `{page, x_pt, y_pt, w_pt, h_pt}` (a box, no text) is what lets anyone
    later look at the same spot of the original upload and see what was
    covered. The key is present only when there is at least one whiteout.
    """
    by_kind: dict[str, int] = {}
    whiteouts: list[dict] = []
    for note in annotations:
        by_kind[note.kind] = by_kind.get(note.kind, 0) + 1
        if note.kind == "whiteout":
            whiteouts.append(
                {
                    "page": int(note.page),
                    "x_pt": round(float(note.x_pt), 3),
                    "y_pt": round(float(note.y_pt), 3),
                    "w_pt": round(float(note.w_pt), 3),
                    "h_pt": round(float(note.h_pt), 3),
                }
            )
    for element in v7 or []:  # editor v2: counts only, like everything above
        by_kind[element["kind"]] = by_kind.get(element["kind"], 0) + 1
    total = len(annotations) + len(v7 or [])
    stats: dict = {"total": total, "by_kind": {k: by_kind[k] for k in sorted(by_kind)}}
    if whiteouts:
        stats["whiteouts"] = whiteouts
    return stats


def _resolve_dates(
    finalization: ContractFinalization,
    signatures: list[Signature],
    index: dict[str, dict],
    source=None,
) -> list[DatePlacement]:
    """Turn the requested dates into placements at the template's own
    date-field coordinates.

    Explicit dates are keyed by template field id only; the client never
    supplies a bbox, so it cannot print a date anywhere but in a date slot.

    Editor v2 (B2): the template page is mapped to its position in the
    signed version (`source`); a date field whose page was removed is 422
    `page_removed`, and on a page rotated since the template the box moves
    with it (and the measured `rule_y`, which no longer applies, is dropped).
    """
    placements: list[DatePlacement] = []
    seen: set[str] = set()
    paired_from = _paired_date_ids(index)

    def placed(field: dict, template_page: int, value: str, field_id: str) -> DatePlacement:
        position, bbox, rule = template_page, field["bbox"], _stored_rule(field)
        if source is not None and not source.legacy:
            position = source.position_for(template_page)
            if position is None:
                raise ValidationError(
                    f"The page of date field {field_id!r} is no longer in this document",
                    details={"reason": SLOT_PAGE_REMOVED, "field_id": field_id, "page": template_page},
                )
            if source.rotated_since_template(template_page):
                bbox, rule = source.template_bbox(template_page, bbox), None
        return DatePlacement(
            page=int(position), bbox=BBox.from_dict(bbox), value=value, field_id=field_id, rule_y=rule,
        )

    for explicit in finalization.dates or []:
        field = index.get(explicit.field_id)
        if (
            field is None
            or field.get("field_type") == "signature"
            or not _is_slot_field(field, paired_from)
            or not field.get("bbox")
        ):
            raise ValidationError(
                f"{explicit.field_id!r} is not a date field of this contract's template"
            )
        if explicit.field_id in seen:
            raise ValidationError(f"Date field {explicit.field_id!r} was given more than once")
        page = field.get("page") or explicit.page
        if page is None:
            raise ValidationError(f"Date field {explicit.field_id!r} has no page in the template")
        placements.append(placed(field, int(page), _render_date(explicit.value), explicit.field_id))
        seen.add(explicit.field_id)

    if finalization.apply_paired_dates and finalization.contract_date:
        rendered = _render_date(finalization.contract_date)
        for signature in signatures:
            field = index.get(signature.field_id or "")
            paired_id = (field or {}).get("paired_date_field_id")
            if not paired_id or paired_id in seen:
                continue
            paired = index.get(paired_id)
            if paired is None:
                continue
            if paired.get("page"):
                placements.append(placed(paired, int(paired["page"]), rendered, paired_id))
            else:  # no template page: beside its signature, as before
                placements.append(
                    DatePlacement(
                        page=int(signature.page_number),
                        bbox=BBox.from_dict(paired["bbox"]),
                        value=rendered,
                        field_id=paired_id,
                        rule_y=_stored_rule(paired),
                    )
                )
            seen.add(paired_id)

    return placements


OVERRIDE_KEYWORD = "reviewer-override"
_OVERRIDE_REASON_MAX_CHARS = 500
_OVERRIDE_SUBJECT_MAX_CHARS = 2000


def _override_metadata(contract: Contract, index: dict[str, dict]) -> dict[str, str]:
    """The note the final PDF carries when a reviewer override let it through.

    The builder has no certificate page, so the record lives in the document
    metadata (Subject/Keywords): it travels with the signed bytes and is
    covered by the file hash.
    """
    if not contract.review_override:
        return {}
    reason = (contract.review_override_reason or "").strip()[:_OVERRIDE_REASON_MAX_CHARS]
    accepted = [str(key) for key in (contract.review_override_fields or [])]
    fields = [
        f"{index.get(fid, {}).get('label', fid)} ({fid})"
        for fid in accepted
        if not fid.startswith(INCONSISTENCY_PREFIX)
    ]
    # Describe each accepted inconsistency from the current comparison (the
    # signing gate guarantees it holds nothing beyond what was accepted); a type
    # that has since been corrected is still named from the stored key.
    current = {}
    for entry in critical_inconsistencies(contract.comparison_result):
        current.setdefault(inconsistency_key(entry["type"]), []).append(entry)
    inconsistencies: list[str] = []
    for key in accepted:
        if not key.startswith(INCONSISTENCY_PREFIX):
            continue
        entries = current.get(key) or [{"type": key[len(INCONSISTENCY_PREFIX):], "issue": "", "pages": []}]
        for entry in entries:
            text = entry["issue"] or entry["type"]
            if entry["pages"]:
                text += f" (pages {', '.join(str(p) for p in entry['pages'])})"
            inconsistencies.append(text)

    subject = f"Reviewer override: {reason}"
    sections = []
    if fields:
        sections.append(f"fields: {', '.join(fields)}")
    if inconsistencies:
        sections.append(f"inconsistencies: {', '.join(inconsistencies)}")
    if sections:
        subject += " - " + "; ".join(sections)
    return {
        "subject": subject[:_OVERRIDE_SUBJECT_MAX_CHARS],
        "keywords": OVERRIDE_KEYWORD,
    }


def _assert_layout_slots(layout: FinalizeLayout, index: dict[str, dict], page_count: int | None) -> None:
    """Per-slot layout map keys must be this contract's own slots (the ones
    GET .../compare lists): anything else is a client bug, refused (422).

    `slot_offsets_pt` / `slot_offsets_x_pt` accept any slot, signature or date.
    `slot_scales` accepts SIGNATURE slots only: it sizes the drawn image, and
    date text has `date_font_size` -- so a date field id there is a client bug
    too, not a silently ignored key. Unknown keys of all three maps are
    reported in one `unknown_slot_offsets` list.
    """
    requested = set(layout.slot_offsets_pt) | set(layout.slot_offsets_x_pt)
    if not requested and not layout.slot_scales:
        return
    paired_from = _paired_date_ids(index)
    known = {
        field_id
        for field_id, field in index.items()
        if _is_slot_field(field, paired_from) and page_in_range(field.get("page"), page_count)
    }
    signature_slots = {
        field_id for field_id in known if index[field_id].get("field_type") == "signature"
    }
    unknown = sorted((requested - known) | (set(layout.slot_scales) - signature_slots))
    if unknown:
        raise ValidationError(
            "layout.slot_offsets_pt / layout.slot_offsets_x_pt / layout.slot_scales name "
            "field(s) that are not slots of this contract (slot_scales: signature slots only)",
            details={"unknown_slot_offsets": unknown[:50]},
        )


def _slot_ref(slot: dict) -> dict:
    """The subset of a slot the finalize 409 carries: enough to name it in the UI."""
    return {"field_id": slot["field_id"], "label": slot["label"], "page": slot["page"]}


def _render_date(value: str) -> str:
    """Contracts print MM/DD/YYYY; the date input submits ISO."""
    parsed = parse_date(value)
    if parsed is None:
        raise ValidationError(f"Could not parse date: {value!r}")
    if parsed > date(2100, 1, 1):
        raise ValidationError("Date is implausibly far in the future")
    return format_date(parsed)
