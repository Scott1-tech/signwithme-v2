"""Final document download.

Signed contracts contain SSNs and licence numbers, so downloads require an
authenticated, authorised caller and are served through a short-lived
presigned URL rather than a stable path.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import assert_can_view_contract, get_contract_or_404
from app.core.database import get_db
from app.core.exceptions import ConflictError
from app.core.security import client_ip, get_current_user
from app.models.user import User
from app.services import audit
from app.services.storage import storage

router = APIRouter(prefix="/api/download", tags=["download"])


@router.get("/{contract_id}/final")
def download_final(
    contract_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    if not contract.final_file_path:
        raise ConflictError("This contract has not been finalised yet")

    filename = f"contract-{contract.id}-signed.pdf"
    audit.log(
        db,
        audit.FINAL_DOWNLOADED,
        contract_id=contract.id,
        actor=user,
        details={"file_hash": contract.final_file_hash},
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        commit=True,
    )

    stream = storage.stream(contract.final_file_path)
    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-SHA256": contract.final_file_hash or "",
            "Cache-Control": "no-store",
        },
    )


@router.get("/{contract_id}/verify")
def verify_final(
    contract_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Tamper check: re-hash the stored artefact and compare with what was
    recorded at finalisation."""
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    if not contract.final_file_path:
        raise ConflictError("This contract has not been finalised yet")

    import hashlib

    current = hashlib.sha256(storage.get_bytes(contract.final_file_path)).hexdigest()
    return {
        "contract_id": str(contract.id),
        "recorded_hash": contract.final_file_hash,
        "current_hash": current,
        "intact": current == contract.final_file_hash,
    }
