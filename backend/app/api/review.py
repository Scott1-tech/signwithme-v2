"""Review session: a reviewer approves or rejects a compared contract."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_contract_or_404
from app.core.database import get_db
from app.core.exceptions import ConflictError, ValidationError
from app.core.security import ROLE_ADMIN, ROLE_REVIEWER, client_ip, require_role
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas.contract import ContractOut, ReviewSubmission
from app.services import audit
from app.services.review_override import (
    critical_fmcsa_rules,
    critical_inconsistencies,
    critical_issue_keys,
    critical_missing_field_ids,
)

OVERRIDE_REASON_MIN_CHARS = 10

router = APIRouter(prefix="/api/review", tags=["review"])


@router.get("/queue", response_model=list[ContractOut])
def review_queue(
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REVIEWER, ROLE_ADMIN)),
):
    from app.models.contract import Contract

    query = (
        select(Contract)
        .where(Contract.status.in_(("analyzed", "reviewing")), Contract.review_status == "pending")
        .order_by(Contract.created_at)
    )
    return list(db.scalars(query))


@router.post("/{contract_id}", response_model=ContractOut)
def submit_review(
    contract_id: UUID,
    review: ReviewSubmission,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REVIEWER, ROLE_ADMIN)),
):
    contract = get_contract_or_404(db, contract_id)
    if contract.status in ("uploaded", "parsing"):
        raise ConflictError("Contract has not finished analysis yet")
    if contract.status == "completed":
        raise ConflictError("Contract is already finalised and cannot be re-reviewed")

    comparison = contract.comparison_result or {}
    override_fields: list[str] | None = None
    override_inconsistencies: list[dict] = []
    if review.decision == "approved" and not comparison.get("can_sign", False):
        # The engine already decided this contract cannot be signed; approving
        # it anyway would let the signing step run on non-compliant data.
        if not review.override_missing:
            raise ConflictError(
                "Contract cannot be approved: unresolved critical issues remain. "
                "Correct the fields and re-run the comparison first."
            )
        # A reviewer may knowingly accept empty required fields, critical
        # cross-field inconsistencies and critical FMCSA violations -- with a
        # reason on record. `override_missing` keeps its name for API
        # compatibility; since 2026-09-17 it covers inconsistencies, and since
        # 2026 it covers FMCSA violations too.
        reason = (review.notes or "").strip()
        if len(reason) < OVERRIDE_REASON_MIN_CHARS:
            raise ValidationError(
                f"Override requires a reason (at least {OVERRIDE_REASON_MIN_CHARS} characters)"
            )
        override_fields = critical_issue_keys(comparison)
        override_inconsistencies = critical_inconsistencies(comparison)
        if not override_fields:
            # can_sign is false yet nothing overridable is critical: the stored
            # comparison is inconsistent, and an empty override would open the
            # signing gate unconditionally. Refuse rather than guess.
            raise ConflictError(
                "Contract cannot be approved: no overridable critical issue was found. "
                "Re-run the comparison first.",
                details={"reason": "override_not_allowed", "blocking_categories": []},
            )

    contract.review_status = review.decision
    contract.review_notes = review.notes
    contract.reviewed_by = user.id
    contract.reviewed_at = datetime.now(timezone.utc)
    contract.status = "signing" if review.decision == "approved" else "reviewing"
    # Every submission restates the override: a plain approve or a reject
    # leaves nothing behind that a later signing step could lean on.
    contract.review_override = override_fields is not None
    contract.review_override_reason = (review.notes or "").strip() if override_fields is not None else None
    contract.review_override_fields = list(override_fields) if override_fields is not None else None

    if override_fields is not None:
        details = {
            "field_ids": critical_missing_field_ids(comparison),
            # type / issue text / pages only -- never the mismatched values.
            "inconsistencies": override_inconsistencies,
            "reason": contract.review_override_reason,
        }
        fmcsa_rules = critical_fmcsa_rules(comparison)
        if fmcsa_rules:
            # rule / issue text / page only: which FMCSA checks the reviewer accepted.
            details["fmcsa_rules"] = fmcsa_rules
        audit.log(
            db,
            audit.REVIEW_OVERRIDE,
            contract_id=contract.id,
            actor=user,
            details=details,
            ip_address=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    audit.log(
        db,
        audit.REVIEW_SUBMITTED,
        contract_id=contract.id,
        actor=user,
        details={
            "decision": review.decision,
            "notes": review.notes,
            "override": override_fields is not None,
        },
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(contract)
    return contract


@router.get("/{contract_id}/audit")
def contract_audit_trail(
    contract_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REVIEWER, ROLE_ADMIN)),
):
    contract = get_contract_or_404(db, contract_id)
    entries = db.scalars(
        select(AuditLog)
        .where(AuditLog.contract_id == contract.id)
        .order_by(AuditLog.timestamp)
    )
    return [
        {
            "action": entry.action,
            "actor_email": entry.actor_email,
            "actor_role": entry.actor_role,
            "details": entry.details,
            "ip_address": entry.ip_address,
            "timestamp": entry.timestamp,
        }
        for entry in entries
    ]
