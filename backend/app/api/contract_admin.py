"""Permanent, admin-only contract deletion.

Kept apart from ``app.api.contracts`` so the destructive route has one small,
auditable home. Order of work matters:

1. database, in one transaction: earlier audit rows are *kept* with their
   ``contract_id`` set to NULL (the audit trail is append-only), a
   ``contract_deleted`` row is added, and the contract row is deleted -- its
   ``contract_fields`` and ``signatures`` go with it through the ORM cascade
   (the live SQLite database runs without FK enforcement, so the database's own
   ON DELETE rules cannot be relied on);
2. COMMIT;
3. stored files. A failure here is logged and does **not** turn a completed
   deletion into a 500: the row is gone and cannot be restored by an error
   response. What is left behind is an orphaned file under a UUID folder.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.deps import get_contract_or_404
from app.core.database import get_db
from app.core.exceptions import ConflictError
from app.core.security import ROLE_ADMIN, client_ip, require_role
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas.deletion import DeletionOut
from app.services import audit
from app.services.storage import PREFIX_CONTRACTS, PREFIX_FINAL, PREFIX_SIGNATURES, storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/contracts", tags=["contracts-admin"])

# The background parser still owns these rows; deleting under it would leave
# the worker writing to a contract that no longer exists.
BUSY_STATUSES = ("uploaded", "parsing")


def remove_stored_objects(keys: Iterable[str | None], prefixes: Iterable[str]) -> None:
    """Delete files after a committed deletion. Never raises; logs instead."""
    for key in keys:
        if not key:
            continue
        try:
            storage.delete(key)
        except Exception:  # noqa: BLE001 - the DB change is already committed
            logger.warning("Stored object %s could not be deleted", key, exc_info=True)
    for prefix in prefixes:
        try:
            storage.delete_prefix(prefix)
        except Exception:  # noqa: BLE001
            logger.warning("Stored prefix %s could not be deleted", prefix, exc_info=True)


@router.delete("/{contract_id}", response_model=DeletionOut)
def delete_contract(
    contract_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN)),
):
    contract = get_contract_or_404(db, contract_id)
    if contract.status in BUSY_STATUSES:
        raise ConflictError(
            "Contract is still being processed; delete it once parsing has finished",
            details={"status": contract.status},
        )

    keys = [contract.file_path, contract.final_file_path]
    keys.extend(signature.signature_image_path for signature in contract.signatures)
    status = contract.status
    template_id = contract.template_id

    db.execute(
        update(AuditLog)
        .where(AuditLog.contract_id == contract.id)
        .values(contract_id=None)
        .execution_options(synchronize_session=False)
    )
    audit.log(
        db,
        audit.CONTRACT_DELETED,
        template_id=template_id,
        actor=user,
        details={"contract_id": str(contract.id), "status": status},
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.delete(contract)
    db.commit()

    remove_stored_objects(
        keys,
        (
            f"{PREFIX_CONTRACTS}/{contract_id}",
            f"{PREFIX_SIGNATURES}/{contract_id}",
            f"{PREFIX_FINAL}/{contract_id}",
        ),
    )
    return DeletionOut(id=str(contract_id), deleted=True)
