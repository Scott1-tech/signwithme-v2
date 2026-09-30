"""The caller's image / stamp gallery (EDITOR-V2-PLAN §2 `assets`, §6; B2).

    GET    /api/assets               the caller's own, not deleted
    POST   /api/assets               multipart `file` or JSON {image_data, kind?, label?}
    GET    /api/assets/{id}/image    the stored PNG (owner only)
    DELETE /api/assets/{id}          soft delete (row kept, bytes removed)

IDOR: every per-item route loads the row with `owner_user_id == caller` and
answers 404 otherwise -- another user's id is indistinguishable from a
nonexistent one. Images go through `deps.decode_annotation_image` (size,
magic bytes, header-first pixel limits, verify + decode, RGBA PNG re-encode:
no metadata or trailing bytes the client sent is ever stored).

Placing a gallery image on a contract COPIES it
(`POST /api/contracts/{cid}/annotation-assets {"from_asset_id"}`), so a
deletion here never breaks a placed image.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pydantic
from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import MAX_SIGNATURE_B64_CHARS, MAX_SIGNATURE_IMAGE_BYTES, decode_annotation_image, image_size
from app.core.database import get_db
from app.core.exceptions import ConflictError, NotFoundError, PayloadTooLargeError, ValidationError
from app.core.security import client_ip, get_current_user
from app.models.asset import Asset
from app.models.user import User
from app.schemas.contract import ANNOTATION_ASSET_ID_PATTERN
from app.services import audit
from app.services.pdf_builder import sha256_bytes
from app.services.storage import storage, user_asset_key

router = APIRouter(prefix="/api/assets", tags=["assets"])

MAX_ASSETS_PER_USER = 200
_MAX_JSON_BYTES = MAX_SIGNATURE_B64_CHARS + 2048


class AssetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_data: str
    kind: str = Field(default="image", pattern="^(image|stamp)$")
    label: str | None = Field(default=None, max_length=120)


class AssetOut(BaseModel):
    asset_id: str
    kind: str
    label: str | None
    width_px: int
    height_px: int
    byte_size: int
    sha256: str
    created_at: datetime


def asset_out(asset: Asset) -> dict:
    created = asset.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return {
        "asset_id": asset.id.hex,
        "kind": asset.kind,
        "label": asset.label,
        "width_px": asset.width_px,
        "height_px": asset.height_px,
        "byte_size": asset.byte_size,
        "sha256": asset.sha256,
        "created_at": created,
    }


def owned_asset(db: Session, asset_id: str, user: User) -> Asset:
    """The caller's own, live gallery asset -- or 404 (never 403)."""
    try:
        key = UUID(hex=asset_id)
    except ValueError as exc:
        raise NotFoundError("Asset not found") from exc
    asset = db.get(Asset, key)
    if (
        asset is None
        or asset.scope != "user"
        or asset.owner_user_id != user.id
        or asset.deleted_at is not None
    ):
        raise NotFoundError("Asset not found")
    return asset


async def _read_upload(request: Request) -> tuple[bytes, str, str | None]:
    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith("multipart/form-data"):
        form = await request.form(max_files=1, max_fields=4)
        try:
            upload = form.get("file")
            if upload is None or isinstance(upload, str):
                raise ValidationError("Expected the image in a multipart 'file' part")
            data = await upload.read(MAX_SIGNATURE_IMAGE_BYTES + 1)
            kind = str(form.get("kind") or "image")
            label = form.get("label")
        finally:
            await form.close()
        if len(data) > MAX_SIGNATURE_IMAGE_BYTES:
            raise PayloadTooLargeError("Image too large")
        try:
            meta = AssetIn(image_data="-", kind=kind, label=label if isinstance(label, str) else None)
        except pydantic.ValidationError as exc:
            raise RequestValidationError(
                [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]
            ) from None
        return decode_annotation_image(data), meta.kind, meta.label
    body = await request.body()
    if len(body) > _MAX_JSON_BYTES:
        raise PayloadTooLargeError("Image too large")
    try:
        payload = AssetIn.model_validate_json(body or b"{}")
    except pydantic.ValidationError as exc:
        raise RequestValidationError(
            [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]
        ) from None
    return decode_annotation_image(payload.image_data), payload.kind, payload.label


@router.get("", response_model=list[AssetOut])
def list_assets(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.scalars(
        select(Asset)
        .where(Asset.scope == "user", Asset.owner_user_id == user.id, Asset.deleted_at.is_(None))
        .order_by(Asset.created_at.desc())
    )
    return [asset_out(row) for row in rows]


@router.post("", response_model=AssetOut, status_code=201)
async def add_asset(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    live = db.scalar(
        select(func.count()).select_from(Asset).where(
            Asset.scope == "user", Asset.owner_user_id == user.id, Asset.deleted_at.is_(None)
        )
    )
    if (live or 0) >= MAX_ASSETS_PER_USER:
        raise ConflictError(
            f"A gallery holds at most {MAX_ASSETS_PER_USER} images", details={"limit": MAX_ASSETS_PER_USER}
        )
    png, kind, label = await _read_upload(request)
    width, height = image_size(png)
    asset_id = uuid4()
    key = user_asset_key(user.id, asset_id.hex)
    storage.put_bytes(key, png, content_type="image/png")
    asset = Asset(
        id=asset_id,
        scope="user",
        owner_user_id=user.id,
        kind=kind,
        label=label,
        storage_key=key,
        sha256=sha256_bytes(png),
        width_px=width,
        height_px=height,
        byte_size=len(png),
    )
    db.add(asset)
    audit.log(
        db,
        audit.ASSET_ADDED,
        actor=user,
        details={"asset_id": asset_id.hex, "scope": "user", "kind": kind, "sha256": asset.sha256,
                 "width_px": width, "height_px": height},
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(key)
        raise
    db.refresh(asset)
    return asset_out(asset)


@router.get("/{asset_id}/image")
def get_asset_image(
    asset_id: str = Path(pattern=ANNOTATION_ASSET_ID_PATTERN),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    asset = owned_asset(db, asset_id, user)
    return Response(
        content=storage.get_bytes(asset.storage_key),
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{asset_id}", status_code=204)
def delete_asset(
    request: Request,
    asset_id: str = Path(pattern=ANNOTATION_ASSET_ID_PATTERN),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Soft delete: the row stays (audit, `source_asset_id` of copies), the
    bytes go. Copies placed on contracts are separate objects and stay. The
    row itself is purged `deleted_asset_retention_days` later by the admin
    script `scripts/purge_deleted_assets.py` (services.asset_purge)."""
    asset = owned_asset(db, asset_id, user)
    asset.deleted_at = datetime.now(timezone.utc)
    audit.log(
        db,
        audit.ASSET_DELETED,
        actor=user,
        details={"asset_id": asset.id.hex, "scope": "user"},
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    try:
        storage.delete(asset.storage_key)
    except Exception:  # noqa: BLE001 - the row is gone from the gallery either way
        pass
    return Response(status_code=204)
