from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schemas.common import BBoxModel

# `info` never blocks approval; it marks work left for the signing step.
Severity = Literal["critical", "warning", "info"]


class MissingField(BaseModel):
    field_id: str
    label: str
    page: int | None = None
    type: str | None = None
    bbox: BBoxModel | None = None
    severity: Severity = "critical"


class Inconsistency(BaseModel):
    type: str
    issue: str
    values: dict[str, object] = {}
    pages: list[int] = []
    field_id: str | None = None
    severity: Severity = "critical"


class FmcsaViolation(BaseModel):
    rule: str
    issue: str
    page: int | None = None
    field_ids: list[str] = []
    severity: Severity = "critical"


class SignatureSlot(BaseModel):
    """A signature or date slot the template expects to be filled at signing
    time. Not an issue: it never blocks approval, and finalize re-checks every
    required slot on the server regardless of what the client sends."""

    field_id: str
    label: str
    page: int | None = None
    bbox: BBoxModel | None = None
    kind: Literal["signature", "date"]
    required: bool = True
    # Set when kind=signature: the date field printed alongside it.
    paired_date_field_id: str | None = None
    # Set when kind=date: the signature this date belongs to.
    paired_signature_field_id: str | None = None
    severity: Literal["info"] = "info"
    # Top edge of the visible fill line's ink, canonical PDF points (same space
    # as `bbox`). None: no line found -- clients use bbox.y + bbox.height.
    rule_y: float | None = None
    # PDF editor v2: stable id of the slot's page in the current document
    # version (`GET .../compare` only). None: legacy contract (no document yet).
    page_uid: str | None = None


class TemplateMatch(BaseModel):
    """Whether the uploaded contract is, in effect, its template file again
    (a warning only; never affects can_sign)."""

    identical: bool
    # Least similar page's text similarity (0..1; 0 when page counts differ).
    page_similarity: float
    # Share of signature slots with images whose image hashes are identical.
    image_match_ratio: float


class ComparisonOut(BaseModel):
    contract_id: str | None = None
    completion_percentage: float = 0.0
    missing_required: list[MissingField] = []
    inconsistencies: list[Inconsistency] = []
    fmcsa_violations: list[FmcsaViolation] = []
    warnings: list[Inconsistency] = []
    # Absent on comparisons stored before slots were reported.
    signature_slots: list[SignatureSlot] = []
    # None on comparisons stored before the check existed (or if it failed).
    template_match: TemplateMatch | None = None
    can_sign: bool = False
    field_count: int = 0
    filled_count: int = 0
