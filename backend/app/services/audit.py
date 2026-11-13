"""Append-only audit logging. Every state change on a contract lands here."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.pii import redact_audit_details
from app.models.audit import AuditLog
from app.models.user import User

# Actions recorded across the lifecycle.
TEMPLATE_UPLOADED = "template_uploaded"
TEMPLATE_ANALYZED = "template_analyzed"
TEMPLATE_FIELD_UPDATED = "template_field_updated"
# An operator re-ran detection on the stored template file.
TEMPLATE_REANALYZED = "template_reanalyzed"
CONTRACT_UPLOADED = "contract_uploaded"
CONTRACT_PARSED = "contract_parsed"
# An operator re-read the stored contract PDF against the current template schema.
CONTRACT_REPARSED = "contract_reparsed"
COMPARISON_RUN = "comparison_run"
FIELD_CORRECTED = "field_corrected"
FIELD_REVEALED = "field_revealed"
REVIEW_SUBMITTED = "review_submitted"
# A reviewer approved past critical missing-required fields, with a reason.
REVIEW_OVERRIDE = "review_override"
SIGNATURE_ADDED = "signature_added"
# One "apply to all" request. Written *in addition to* a `signature_added` per
# placed slot, never instead of one: the per-signature trail stays complete.
SIGNATURES_BULK_ADDED = "signatures_bulk_added"
SIGNATURE_REMOVED = "signature_removed"
# A free signature's box moved or resized on its own page (old and new bbox).
SIGNATURE_RESIZED = "signature_resized"
DATE_ADDED = "date_added"
# An image uploaded for an `image` annotation (contract v5). Id, hash and pixel
# size only -- never the image.
ANNOTATION_ASSET_ADDED = "annotation_asset_added"
CONTRACT_FINALIZED = "contract_finalized"
FINAL_DOWNLOADED = "final_downloaded"
# Per-user reusable signature library (not tied to a contract).
USER_SIGNATURE_SAVED = "user_signature_saved"
USER_SIGNATURE_DELETED = "user_signature_deleted"
# An admin permanently deleted a contract / purged an unreferenced template.
# The row outlives its subject: earlier audit rows keep existing with their FK
# set to NULL, and `details` carries the deleted id (never contract content).
CONTRACT_DELETED = "contract_deleted"
TEMPLATE_DELETED = "template_deleted"

# PDF editor v2 (EDITOR-V2-PLAN §4.6). None of these carries document text or
# anything the user typed: ids, counts, hashes, page ids and rectangles only.
DOCUMENT_CREATED = "document_created"
DOCUMENT_VERSION_COMMITTED = "document_version_committed"
DOCUMENT_DRAFT_DISCARDED = "document_draft_discarded"
DOCUMENT_DRAFT_TAKEN_OVER = "document_draft_taken_over"
DOCUMENT_MARKUP_SAVED = "document_markup_saved"
# A content edit (or review-relevant markup, §11 Q1) re-opened review.
REVIEW_RESET_BY_EDIT = "review_reset_by_edit"
# Reviewer corrections inside an edited region, dropped before the re-parse.
CORRECTIONS_DROPPED = "corrections_dropped"
# Signatures removed because a content edit replaced the document they were
# on (§11 Q3). One `signature_removed` per row as well.
SIGNATURES_SUPERSEDED = "signatures_superseded"
# B2: gallery images (id, hash, pixel size -- never the image) and exports.
ASSET_ADDED = "asset_added"
ASSET_DELETED = "asset_deleted"
# Admin clean-up of soft-deleted gallery rows (ids and counts only).
ASSETS_PURGED = "assets_purged"
DOCUMENT_EXPORTED = "document_exported"
# F3: share-for-fill links. NEVER the token (nor its hash), never a submitted
# value, never the reject reason text -- ids, counts and dates only.
SHARE_LINK_CREATED = "share_link_created"
SHARE_LINK_REVOKED = "share_link_revoked"
FORM_SUBMISSION_RECEIVED = "form_submission_received"
FORM_SUBMISSION_ACCEPTED = "form_submission_accepted"
FORM_SUBMISSION_REJECTED = "form_submission_rejected"


def log(
    db: Session,
    action: str,
    *,
    contract_id: UUID | None = None,
    template_id: UUID | None = None,
    actor: User | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    commit: bool = False,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        contract_id=contract_id,
        template_id=template_id,
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        actor_role=actor.role if actor else None,
        # A field correction carries the old and new value; for a sensitive
        # field those must never land in an append-only table.
        details=redact_audit_details(details or {}),
        ip_address=ip_address,
        user_agent=(user_agent or "")[:500] or None,
    )
    # AuditLog has foreign keys to contracts and templates but no ORM
    # relationship, so the unit of work does not know to insert a contract or
    # template added in the same transaction before this row. Postgres then
    # rejects the insert (SQLite in tests does not enforce the key). Flushing
    # first writes any pending parent rows so the reference is valid.
    db.flush()
    db.add(entry)
    if commit:
        db.commit()
    return entry
