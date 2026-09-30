"""A user's private library of reusable signatures.

Distinct from ``app.api.signatures``, which records a signing *event* against a
contract. These rows belong to one user and are picked when signing any
contract, so every read and delete is scoped to the current user.

IDOR is the primary security concern here: without an ownership check on the
single-item read, image-byte and delete routes, any authenticated user could
read or destroy another user's stored signature by guessing its id. The
``_get_owned_signature`` helper below is that check, and it returns 404 (not
403) on a mismatch so the endpoint never confirms that someone else's id exists.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import decode_signature_image
from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.core.security import client_ip, get_current_user
from app.models.user import User
from app.models.user_signature import UserSignature
from app.schemas.contract import UserSignatureCreate, UserSignatureOut
from app.services import audit
from app.services.pdf_builder import sha256_bytes
from app.services.storage import storage, user_signature_key

router = APIRouter(prefix="/api/signatures", tags=["user-signatures"])


def _get_owned_signature(db: Session, signature_id: UUID, user: User) -> UserSignature:
    """Load a signature only if it belongs to the caller.

    This is the IDOR guard for every per-item route. A row owned by another
    user is reported as ``NotFoundError`` (404) rather than ``PermissionError_``
    (403) so the response cannot be used to probe which ids exist.
    """
    signature = db.get(UserSignature, signature_id)
    if signature is None or signature.user_id != user.id:
        raise NotFoundError("Signature not found")
    return signature


@router.post("", response_model=UserSignatureOut, status_code=201)
def save_signature(
    payload: UserSignatureCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Store a reusable signature in the current user's private library.

    `typed` (editor v2 B2): the server renders the text in a script font and
    stores only the PNG (`source='typed'`); the text is not persisted and not
    audited."""
    if payload.typed is not None:
        from app.services.typed_signature import typed_signature_png

        image_bytes = typed_signature_png(payload.typed.text, payload.typed.font)
        source = "typed"
    else:
        image_bytes = decode_signature_image(payload.image_data)
        source = payload.source

    signature = UserSignature(
        id=uuid4(),
        user_id=user.id,
        label=payload.label,
        image_path="",
        signature_hash=sha256_bytes(image_bytes),
        is_default=payload.is_default,
        source=source,
    )
    key = user_signature_key(user.id, signature.id)
    storage.put_bytes(key, image_bytes, content_type="image/png")
    signature.image_path = key

    if payload.is_default:
        # Only one default per user; demote any previous default.
        db.execute(
            update(UserSignature)
            .where(UserSignature.user_id == user.id, UserSignature.is_default.is_(True))
            .values(is_default=False)
        )

    db.add(signature)
    audit.log(
        db,
        audit.USER_SIGNATURE_SAVED,
        actor=user,
        details={
            "signature_id": str(signature.id),
            "signature_hash": signature.signature_hash,
            **({"source": source} if source else {}),
        },
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(signature)
    return signature


@router.get("", response_model=list[UserSignatureOut])
def list_signatures(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List only the caller's own signatures."""
    return list(
        db.scalars(
            select(UserSignature)
            .where(UserSignature.user_id == user.id)
            .order_by(UserSignature.is_default.desc(), UserSignature.created_at.desc())
        )
    )


@router.get("/{signature_id}", response_model=UserSignatureOut)
def get_signature(
    signature_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _get_owned_signature(db, signature_id, user)


@router.get("/{signature_id}/image")
def get_signature_image(
    signature_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Serve the stored PNG bytes, ownership-checked, through the API only."""
    signature = _get_owned_signature(db, signature_id, user)
    stream = storage.stream(signature.image_path)
    return StreamingResponse(
        stream,
        media_type="image/png",
        headers={
            "X-Content-SHA256": signature.signature_hash or "",
            "Cache-Control": "no-store",
        },
    )


@router.delete("/{signature_id}")
def delete_signature(
    signature_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    signature = _get_owned_signature(db, signature_id, user)

    storage.delete(signature.image_path)
    db.delete(signature)
    audit.log(
        db,
        audit.USER_SIGNATURE_DELETED,
        actor=user,
        details={"signature_id": str(signature_id)},
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return {"signature_id": str(signature_id), "removed": True}
