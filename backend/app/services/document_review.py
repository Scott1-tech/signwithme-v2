"""Review integration of the PDF editor v2 (EDITOR-V2-PLAN §5, §11).

1. A CONTENT commit (or a change of review-relevant markup, §11 Q1) re-opens
   review: `review_status='pending'`, the reviewer override is cleared,
   `reviewed_by/at` are kept (history), audit `review_reset_by_edit`.
   `can_sign` is never touched here -- the re-parse recomputes it with the
   unchanged engine; an FMCSA violation therefore still blocks, and the
   cleared override cannot let anything through.
2. Signatures on a content commit are refused (409 `signatures_present`) or,
   with `discard_signatures`, removed by `documents.commit_draft`.
3. Reviewer field corrections are dropped ONLY where the edit changed the page
   (`changed_regions` intersecting the field's box); the others survive the
   re-parse as before (audit `corrections_dropped`, field ids only).
4. Page map: `page_map(source)` / `present_pages(source)` give the parser and
   the comparison the origin-page -> position mapping. B1 has no page ops, so
   both are identity (None); the interfaces exist so B2 only fills them in.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.contract import Contract
from app.models.field import ContractField
from app.models.template import Template
from app.models.user import User
from app.services import audit
from app.services.geometry import BBox


def reset_review(db: Session, contract: Contract, user: User | None, *, trigger: str, version_no: int | None = None) -> bool:
    """Back to `pending` (override cleared). False when it already was."""
    if contract.review_status == "pending":
        return False
    previous = contract.review_status
    contract.review_status = "pending"
    contract.review_override = False
    contract.review_override_reason = None
    contract.review_override_fields = None
    details = {"previous_review_status": previous, "trigger": trigger}
    if version_no is not None:
        details["version_no"] = version_no
    audit.log(db, audit.REVIEW_RESET_BY_EDIT, contract_id=contract.id, actor=user, details=details)
    return True


def _region_hits(field: dict, position: int | None, regions: list[dict], uid_at) -> bool:
    bbox = field.get("bbox")
    if not bbox or position is None:
        return False
    uid = uid_at(position)
    box = BBox.from_dict(bbox)
    for region in regions:
        if region.get("page_uid") != uid:
            continue
        rect = region["rect"]
        if (
            box.x < rect["x_pt"] + rect["w_pt"]
            and rect["x_pt"] < box.x1
            and box.y < rect["y_pt"] + rect["h_pt"]
            and rect["y_pt"] < box.y1
        ):
            return True
    return False


def corrected_counts(db: Session, contract_id) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in db.scalars(
        select(AuditLog).where(
            AuditLog.contract_id == contract_id, AuditLog.action == audit.FIELD_CORRECTED
        )
    ):
        field_id = (entry.details or {}).get("field_id")
        if field_id:
            counts[field_id] = counts.get(field_id, 0) + 1
    return counts


def dropped_counts(db: Session, contract_id) -> dict[str, int]:
    """field id -> the number of corrections a `corrections_dropped` entry
    discarded. A field counts as corrected again only once it has MORE
    `field_corrected` entries than that -- order-free, so equal timestamps
    (SQLite: one-second resolution) cannot confuse it."""
    dropped: dict[str, int] = {}
    for entry in db.scalars(
        select(AuditLog).where(
            AuditLog.contract_id == contract_id, AuditLog.action == audit.CORRECTIONS_DROPPED
        )
    ):
        for field_id, count in ((entry.details or {}).get("corrected_counts") or {}).items():
            dropped[field_id] = max(dropped.get(field_id, 0), int(count))
    return dropped


def drop_corrections(db: Session, contract: Contract, user: User | None, *, changed_regions: list[dict], source, version_no: int) -> list[str]:
    from app.services.comparison_engine import _index_schema
    from app.services.template_mapper import reviewer_corrections

    template = db.get(Template, contract.template_id)
    if template is None or not changed_regions:
        return []
    index = _index_schema(template.field_schema or {})
    corrections = reviewer_corrections(db, contract.id)
    hit = [
        field_id
        for field_id in corrections
        if field_id in index
        and _region_hits(index[field_id], source.position_for(index[field_id].get("page")), changed_regions, source.page_uid_at)
    ]
    if not hit:
        return []
    counts = corrected_counts(db, contract.id)
    for row in db.scalars(
        select(ContractField).where(
            ContractField.contract_id == contract.id, ContractField.template_field_id.in_(hit)
        )
    ):
        if row.status == "verified":
            row.status = "filled"
    audit.log(
        db,
        audit.CORRECTIONS_DROPPED,
        contract_id=contract.id,
        actor=user,
        details={
            "field_ids": sorted(hit),
            "corrected_counts": {field_id: counts.get(field_id, 0) for field_id in sorted(hit)},
            "version_no": version_no,
            "reason": "content_edited",
        },
    )
    return hit


def on_content_commit(db: Session, contract: Contract, user: User | None, *, version_no: int, changed_regions: list[dict], source) -> bool:
    """Everything a committed content change does to review. Returns whether
    review was reset. The caller starts the re-parse after COMMIT."""
    reset = reset_review(db, contract, user, trigger="content_commit", version_no=version_no)
    drop_corrections(db, contract, user, changed_regions=changed_regions, source=source, version_no=version_no)
    contract.status = "parsing"
    contract.error_message = None
    return reset


def on_markup_content_change(db: Session, contract: Contract, user: User | None) -> bool:
    """Review-relevant markup changed (§11 Q1). The PDF did not change, so no
    re-parse; an approved contract goes back to the review queue."""
    reset = reset_review(db, contract, user, trigger="markup_content")
    if reset and contract.status in ("signing", "reviewing"):
        contract.status = "analyzed"
    return reset


def page_map(source) -> dict[int, int | None] | None:
    """Origin page -> position for the parser; None = identity."""
    return source.page_map


def present_pages(source) -> set[int] | None:
    """Template pages still present in the current version; None = all (no
    page was removed -- also when pages were only inserted or moved, so the
    comparison stays exactly the pre-v2 one then)."""
    mapping = source.page_map
    if mapping is None:
        return None
    if all(position is not None for position in mapping.values()):
        return None
    return {origin for origin, position in mapping.items() if position is not None}
