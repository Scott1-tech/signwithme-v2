"""Share-for-fill links, internal side (EDITOR-V2-PLAN §8, F3).

Every route: the contract exists (404), the caller may view it AND may edit
its document (`assert_can_edit_document`, §11 Q2: admin, reviewer, uploader).

    POST   /api/contracts/{cid}/share-links                    create (token ONCE)
    GET    /api/contracts/{cid}/share-links                    list (no token)
    DELETE /api/contracts/{cid}/share-links/{id}               revoke (idempotent)
    GET    /api/contracts/{cid}/submissions[?status=]          list (values decrypted, PII masked)
    POST   /api/contracts/{cid}/submissions/{id}/accept        -> a new draft of `field.value` ops
    POST   /api/contracts/{cid}/submissions/{id}/reject        {reason}

Accept puts the values into a DRAFT only; the editor opens it (same
`client_session_id`), looks, and saves -- a content commit that re-opens a past
review like any other `field.value` (B3). Another draft on the document -> 409
`draft_locked` (nothing is merged into someone's edit).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import assert_can_edit_document, assert_can_view_contract, get_contract_or_404
from app.core import pii
from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.core.security import client_ip, get_current_user
from app.models.contract import Contract
from app.models.document import DocumentDraft, DocumentVersion
from app.models.share import FormSubmission, ShareLink
from app.models.user import User
from app.schemas.share import (
    AcceptIn,
    AcceptOut,
    RejectIn,
    ShareLinkCreatedOut,
    ShareLinkCreateIn,
    ShareLinkOut,
    SubmissionOut,
)
from app.services import audit, documents, share
from app.services.documents import DocumentError, now_utc

router = APIRouter(prefix="/api/contracts/{contract_id}", tags=["share"])


def _editable(db: Session, contract_id: UUID, user: User) -> Contract:
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    assert_can_edit_document(contract, user)
    return contract


def _version_no(db: Session, version_id) -> int | None:
    version = db.get(DocumentVersion, version_id)
    return version.version_no if version is not None else None


def _link_out(db: Session, link: ShareLink) -> dict:
    pending = db.scalar(
        select(func.count()).select_from(FormSubmission).where(
            FormSubmission.share_link_id == link.id, FormSubmission.status == "received"
        )
    )
    return {
        "id": str(link.id),
        "version_no": _version_no(db, link.version_id) or 0,
        "allowed_fields": list(link.allowed_fields or []),
        "recipient_label": link.recipient_label,
        "expires_at": documents._aware(link.expires_at),
        "max_submissions": link.max_submissions,
        "submission_count": link.submission_count,
        "submissions_pending": int(pending or 0),
        "status": share.status_of(link),
        "revoked_at": documents._aware(link.revoked_at) if link.revoked_at else None,
        "last_used_at": documents._aware(link.last_used_at) if link.last_used_at else None,
        "created_by_name": documents.owner_name(db, link.created_by) if link.created_by else None,
        "created_at": documents._aware(link.created_at),
    }


def _submission_out(db: Session, submission: FormSubmission) -> dict:
    values = share.decrypt_values(submission.values_encrypted)
    return {
        "id": str(submission.id),
        "share_link_id": str(submission.share_link_id),
        "recipient_label": submission.share_link.recipient_label if submission.share_link else None,
        "version_no": _version_no(db, submission.version_id),
        "status": submission.status,
        "submitter_name": submission.submitter_name,
        "submitter_email": submission.submitter_email,
        "field_count": submission.field_count,
        "values": pii.redact_mapping(values) if values is not None else None,
        "values_readable": values is not None,
        "ip_address": submission.ip_address,
        "created_at": documents._aware(submission.created_at),
        "reviewed_by_name": documents.owner_name(db, submission.reviewed_by) if submission.reviewed_by else None,
        "reviewed_at": documents._aware(submission.reviewed_at) if submission.reviewed_at else None,
        "reject_reason": submission.reject_reason,
    }


def _link_of(db: Session, contract: Contract, link_id: UUID) -> ShareLink:
    link = db.get(ShareLink, link_id)
    if link is None or link.contract_id != contract.id:
        raise NotFoundError("Share link not found")
    return link


def _submission_of(db: Session, contract: Contract, submission_id: UUID) -> FormSubmission:
    submission = db.get(FormSubmission, submission_id)
    if submission is None or submission.share_link is None or submission.share_link.contract_id != contract.id:
        raise NotFoundError("Submission not found")
    return submission


def _assert_pending(submission: FormSubmission) -> None:
    if submission.status != "received":
        raise DocumentError(409, "submission_not_pending", "This submission was already reviewed",
                            {"status": submission.status})


# -- links ------------------------------------------------------------------------


@router.post("/share-links", response_model=ShareLinkCreatedOut, status_code=201)
def create_share_link(
    contract_id: UUID,
    body: ShareLinkCreateIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = _editable(db, contract_id, user)
    document = documents.ensure_document(db, contract, user)
    link, token = share.create_link(db, contract, document, user, body)
    audit.log(
        db,
        audit.SHARE_LINK_CREATED,
        contract_id=contract.id,
        actor=user,
        details={
            "share_link_id": str(link.id),
            "version_no": _version_no(db, link.version_id),
            "allowed_fields": len(link.allowed_fields),
            "max_submissions": link.max_submissions,
            "expires_at": documents._aware(link.expires_at).isoformat(),
            "has_label": link.recipient_label is not None,
        },
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(link)
    response.headers["Cache-Control"] = "no-store"
    return {**_link_out(db, link), "token": token, "url": share.share_url(token)}


@router.get("/share-links", response_model=list[ShareLinkOut])
def list_share_links(
    contract_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = _editable(db, contract_id, user)
    links = db.scalars(
        select(ShareLink).where(ShareLink.contract_id == contract.id).order_by(ShareLink.created_at.desc())
    )
    return [_link_out(db, link) for link in links]


@router.delete("/share-links/{link_id}", status_code=204)
def revoke_share_link(
    contract_id: UUID,
    link_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = _editable(db, contract_id, user)
    link = _link_of(db, contract, link_id)
    if link.revoked_at is None:
        link.revoked_at = now_utc()
        link.revoked_by = user.id
        audit.log(
            db,
            audit.SHARE_LINK_REVOKED,
            contract_id=contract.id,
            actor=user,
            details={"share_link_id": str(link.id), "submission_count": link.submission_count},
            ip_address=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
    return Response(status_code=204)


# -- submissions ------------------------------------------------------------------


@router.get("/submissions", response_model=list[SubmissionOut])
def list_submissions(
    contract_id: UUID,
    status: str | None = Query(default=None, pattern="^(received|accepted|rejected)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = _editable(db, contract_id, user)
    query = (
        select(FormSubmission)
        .join(ShareLink, ShareLink.id == FormSubmission.share_link_id)
        .where(ShareLink.contract_id == contract.id)
        .order_by(FormSubmission.created_at.desc())
    )
    if status is not None:
        query = query.where(FormSubmission.status == status)
    return [_submission_out(db, submission) for submission in db.scalars(query)]


@router.post("/submissions/{submission_id}/accept", response_model=AcceptOut)
def accept_submission(
    contract_id: UUID,
    submission_id: UUID,
    body: AcceptIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = _editable(db, contract_id, user)
    submission = _submission_of(db, contract, submission_id)
    _assert_pending(submission)
    documents.assert_not_completed(contract)
    documents.assert_not_busy(contract)
    document = documents.ensure_document(db, contract, user)
    draft = document.draft
    if draft is not None:
        raise DocumentError(
            409, "draft_locked", "The document has a draft; save or cancel it first",
            {
                "owner_name": documents.owner_name(db, draft.owner_user_id),
                "lease_expires_at": documents._aware(draft.lease_expires_at).isoformat(),
                "is_mine": draft.owner_user_id == user.id,
                "lease_active": documents.lease_active(draft),
            },
        )
    values = share.decrypt_values(submission.values_encrypted)
    if values is None:
        raise DocumentError(409, "submission_unreadable",
                            "The submitted values cannot be decrypted (the key changed)", {})
    current = db.get(DocumentVersion, document.current_version_id)
    ops, names, problems = share.value_ops(values)
    if not problems:
        # The link's version may no longer be the current one: the values
        # must still fit the fields as they are NOW.
        problems = share.validate_against(current, ops, names)
    if problems:
        raise DocumentError(422, "invalid_ops", "The submitted values no longer fit this document",
                            {"fields": problems[:200]})
    draft = DocumentDraft(
        document_id=document.id,
        base_version_id=current.id,
        revision=0,
        ops=ops,
        markup=None,
        payload_hash=documents.payload_hash(ops, None),
        owner_user_id=user.id,
        client_session_id=body.client_session_id,
        lease_expires_at=documents.lease_until(),
    )
    db.add(draft)
    submission.status = "accepted"
    submission.reviewed_by = user.id
    submission.reviewed_at = now_utc()
    audit.log(
        db,
        audit.FORM_SUBMISSION_ACCEPTED,
        contract_id=contract.id,
        actor=user,
        details={
            "submission_id": str(submission.id),
            "share_link_id": str(submission.share_link_id),
            "fields": len(ops),
            "base_version_no": current.version_no,
        },
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DocumentError(409, "draft_locked", "Someone else started editing this document", {})
    db.refresh(draft)
    db.refresh(submission)
    return {
        "submission": _submission_out(db, submission),
        "draft": {
            "revision": draft.revision,
            "base_version_no": current.version_no,
            "ops": list(draft.ops or []),
            "markup": draft.markup,
            "lease_expires_at": documents._aware(draft.lease_expires_at),
            "is_mine": True,
            "owner_name": documents.owner_name(db, user.id),
        },
    }


@router.post("/submissions/{submission_id}/reject", response_model=SubmissionOut)
def reject_submission(
    contract_id: UUID,
    submission_id: UUID,
    body: RejectIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = _editable(db, contract_id, user)
    submission = _submission_of(db, contract, submission_id)
    _assert_pending(submission)
    submission.status = "rejected"
    submission.reviewed_by = user.id
    submission.reviewed_at = now_utc()
    submission.reject_reason = body.reason
    audit.log(
        db,
        audit.FORM_SUBMISSION_REJECTED,
        contract_id=contract.id,
        actor=user,
        # The reason is the reviewer's free text: its length only.
        details={
            "submission_id": str(submission.id),
            "share_link_id": str(submission.share_link_id),
            "fields": submission.field_count,
            "reason_chars": len(body.reason),
        },
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(submission)
    return _submission_out(db, submission)
