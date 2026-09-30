"""Contract upload, comparison results, and per-field inspection."""
from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    assert_can_view_contract,
    get_contract_or_404,
    get_template_or_404,
    read_pdf_upload,
)
from app.core import pii
from app.core.database import get_db
from app.core.exceptions import ConflictError, PermissionError_
from app.core.security import (
    ROLE_ADMIN,
    ROLE_REVIEWER,
    client_ip,
    get_current_user,
    require_role,
)
from app.models.contract import Contract
from app.models.field import ContractField
from app.models.template import Template
from app.models.user import User
from app.schemas.comparison import ComparisonOut
from app.schemas.contract import (
    MAX_DUPLICATE_RESULTS,
    SHA256_HEX_PATTERN,
    ContractCreateResponse,
    ContractDetailOut,
    ContractDuplicateOut,
    ContractDuplicatesOut,
    ContractFieldOut,
    ContractListOut,
    FieldCorrection,
)
from app.services import audit
from app.services.comparison_engine import ComparisonEngine
from app.services.pdf_parser import sha256_bytes
from app.services.review_override import critical_counts
from app.services.rule_locator import signing_comparison
from app.services.storage import contract_key, storage
from app.services.template_mapper import extracted_cache, normalized_for
from app.workers.tasks import process_contract_task

router = APIRouter(prefix="/api/contracts", tags=["contracts"])


def _field_out(row: ContractField) -> ContractFieldOut:
    """Serialise a field row with sensitive values masked.

    Stored sensitive values are ciphertext, so returning them raw would be
    useless as well as unsafe; the plaintext is available only through the
    audited reveal endpoint below.
    """
    out = ContractFieldOut.model_validate(row)
    if pii.is_sensitive(row.template_field_id):
        out.raw_value = pii.mask(pii.decrypt(row.raw_value), row.template_field_id)
        out.normalized_value = None
    return out


def _can_view(contract: Contract, user: User) -> bool:
    """`assert_can_view_contract` as a predicate -- the same rule, not a copy of it."""
    try:
        assert_can_view_contract(contract, user)
    except PermissionError_:
        return False
    return True


def _visible_with_hash(db: Session, user: User, file_hash: str) -> list[tuple[Contract, str | None]]:
    """Contracts with this upload hash that ``user`` may open, newest first.

    Filtering happens after the query, row by row, through the one view rule
    every contract route uses. A contract the caller cannot open is dropped
    before anything -- including the count -- is derived from the result.
    """
    rows = db.execute(
        select(Contract, Template.name)
        .outerjoin(Template, Template.id == Contract.template_id)
        .where(Contract.file_hash == file_hash)
        .order_by(Contract.created_at.desc())
    ).all()
    return [(contract, name) for contract, name in rows if _can_view(contract, user)]


@router.get("/duplicates", response_model=ContractDuplicatesOut)
def find_duplicates(
    sha256: str = Query(..., pattern=SHA256_HEX_PATTERN),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Earlier contracts uploaded from byte-identical PDF, checked before upload.

    ``sha256`` is the hex SHA-256 of the raw PDF bytes, as stored in
    ``contracts.file_hash``. Advisory only: the upload itself never blocks.
    The answer is limited to contracts the caller can open, and carries no
    contractor details or extracted values.
    """
    visible = _visible_with_hash(db, user, sha256.lower())[:MAX_DUPLICATE_RESULTS]
    return ContractDuplicatesOut(
        duplicates=[
            ContractDuplicateOut(
                id=contract.id,
                status=contract.status,
                review_status=contract.review_status,
                template_id=contract.template_id,
                template_name=name,
                created_at=contract.created_at,
            )
            for contract, name in visible
        ]
    )


@router.post("", response_model=ContractCreateResponse, status_code=202)
async def upload_contract(
    request: Request,
    template_id: UUID = Form(...),
    file: UploadFile = File(...),
    contractor_name: str | None = Form(None),
    contractor_email: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload a filled contract. Parsing and comparison run in the background."""
    template = get_template_or_404(db, template_id)
    if template.status != "ready":
        raise ConflictError(
            f"Template is '{template.status}'; it must finish analysis before contracts are uploaded"
        )

    data = await read_pdf_upload(file)
    file_hash = sha256_bytes(data)
    # Counted before the new row is added, so it is "earlier uploads" only.
    # Informational: a duplicate is recorded, never refused.
    duplicate_of_count = len(_visible_with_hash(db, user, file_hash))
    contract = Contract(
        id=uuid4(),
        template_id=template.id,
        contractor_name=contractor_name,
        contractor_email=contractor_email,
        file_hash=file_hash,
        file_path="",
        status="uploaded",
        uploaded_by=user.id,
    )
    key = contract_key(contract.id)
    storage.ensure_bucket()
    storage.put_bytes(key, data)
    contract.file_path = key

    db.add(contract)
    audit.log(
        db,
        audit.CONTRACT_UPLOADED,
        contract_id=contract.id,
        template_id=template.id,
        actor=user,
        details={
            "bytes": len(data),
            "file_hash": contract.file_hash,
            "duplicate_of_count": duplicate_of_count,
        },
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    task = process_contract_task.delay(str(contract.id))
    return ContractCreateResponse(contract_id=contract.id, task_id=task.id, status="uploaded")


def _list_out(contract: Contract, template_name: str | None) -> ContractListOut:
    """One dashboard row.

    Reads nothing: the template name arrives from the list query's join and
    every count comes from the comparison already stored on the contract row,
    so the number of statements the endpoint runs does not grow with the
    number of contracts. Only counts and flags cross the wire -- the stored
    comparison itself (labels, values, field ids) never does.
    """
    out = ContractListOut.model_validate(contract)
    out.template_name = template_name
    comparison = contract.comparison_result
    if not isinstance(comparison, dict) or not comparison:
        return out  # no comparison yet: every derived field stays None

    counts = critical_counts(comparison)
    out.critical_missing_count = counts["missing_required"]
    out.critical_inconsistency_count = counts["inconsistencies"]
    out.critical_fmcsa_count = counts["fmcsa_violations"]

    can_sign = comparison.get("can_sign")
    out.can_sign = bool(can_sign) if can_sign is not None else None
    slots = comparison.get("signature_slots")
    if isinstance(slots, list):
        out.stored_signature_slot_count = len(slots)
    return out


@router.get("", response_model=list[ContractListOut])
def list_contracts(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    status: str | None = None,
    template_id: UUID | None = None,
):
    """The contract list the dashboard renders.

    One statement for the whole page. The template name comes from a LEFT JOIN
    -- a contract always keeps its template (a purge is refused while any
    contract references it), but an outer join means a template that somehow
    went missing costs a null name rather than a dropped contract, the same
    rule `_visible_with_hash` uses. Who may see what is unchanged: contractors
    still get only the contracts they uploaded.
    """
    query = (
        select(Contract, Template.name)
        .outerjoin(Template, Template.id == Contract.template_id)
        .order_by(Contract.created_at.desc())
    )
    if status:
        query = query.where(Contract.status == status)
    if template_id:
        query = query.where(Contract.template_id == template_id)
    if user.role not in (ROLE_ADMIN, ROLE_REVIEWER):
        query = query.where(Contract.uploaded_by == user.id)
    return [_list_out(contract, name) for contract, name in db.execute(query).all()]


@router.get("/{contract_id}", response_model=ContractDetailOut)
def get_contract(
    contract_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    detail = ContractDetailOut.model_validate(contract)
    detail.final_available = bool(contract.final_file_path)
    detail.document = _document_summary(contract)
    return detail


def _document_summary(contract: Contract) -> dict | None:
    """PDF editor v2 summary (None = no editor document yet: legacy)."""
    document = contract.document
    if document is None or document.current_version is None:
        return None
    return {
        "document_id": str(document.id),
        "current_version_no": document.current_version.version_no,
        "versions_count": len(document.versions),
        "draft_active": document.draft is not None,
    }


@router.get("/{contract_id}/compare", response_model=ComparisonOut)
def get_comparison(
    contract_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """What is missing, inconsistent or non-compliant."""
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    if not contract.comparison_result:
        raise ConflictError(f"Contract is '{contract.status}'; comparison is not ready yet")
    # Read-time signing view: slots past the uploaded PDF's last page dropped,
    # `rule_y` from the current template schema. Nothing stored is rewritten.
    return signing_comparison(db, contract)


@router.post("/{contract_id}/compare", response_model=ComparisonOut)
def rerun_comparison(
    contract_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Re-evaluate the rules against the current field values, without
    re-parsing the PDF. Used after a reviewer corrects a misread field."""
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    template = get_template_or_404(db, contract.template_id)

    # Editor v2 B2: pages removed from the current version (identity -> None).
    from app.services.document_review import present_pages
    from app.services.documents import signing_source

    present = present_pages(signing_source(contract))
    extra = {} if present is None else {"present_pages": present}
    result = ComparisonEngine().compare(
        contract.extracted_data, template.field_schema, contract_id=str(contract.id), **extra
    ).to_dict()
    # template_match is a file-level fact computed at parse time (the engine
    # does not see the PDFs); a re-run over field values keeps the stored one.
    previous = contract.comparison_result or {}
    if "template_match" in previous:
        result["template_match"] = previous["template_match"]
    contract.comparison_result = result
    audit.log(
        db,
        audit.COMPARISON_RUN,
        contract_id=contract.id,
        actor=user,
        details={"trigger": "manual", "can_sign": result["can_sign"]},
    )
    db.commit()
    return result


@router.post("/{contract_id}/reparse", response_model=ContractCreateResponse, status_code=202)
def reparse_contract(
    contract_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REVIEWER, ROLE_ADMIN)),
):
    """Re-read the stored contract PDF against the template's CURRENT schema.

    Unlike POST .../compare this runs the parser again, so it picks up a
    re-analysed template. Kept across the run: review_status and reviewer
    fields, reviewer corrections (see template_mapper.reviewer_corrections),
    placed signatures and the audit trail. Same role gate as review approval.
    A finalised document is never re-read (409): its signed bytes are the record.
    """
    contract = get_contract_or_404(db, contract_id)
    if contract.status == "completed":
        raise ConflictError("Contract is finalised; a signed document is not re-parsed")
    if contract.status in ("uploaded", "parsing"):
        raise ConflictError(f"Contract is '{contract.status}'; wait for the current parse to finish")
    if contract.status == "signing":
        raise ConflictError("Contract is being signed; remove signatures before re-parsing")
    template = get_template_or_404(db, contract.template_id)
    if template.status != "ready":
        raise ConflictError(f"Template is '{template.status}'; it must be ready before re-parsing")

    previous = contract.comparison_result or {}
    audit.log(
        db,
        audit.CONTRACT_REPARSED,
        contract_id=contract.id,
        template_id=template.id,
        actor=user,
        details={
            "previous_status": contract.status,
            "review_status": contract.review_status,
            "previous_missing": len(previous.get("missing_required", []) or []),
            "previous_can_sign": previous.get("can_sign"),
            "template_field_count": (template.field_schema or {}).get("field_count", 0),
        },
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    contract.status = "parsing"
    contract.error_message = None
    db.commit()

    task = process_contract_task.delay(str(contract.id))
    return ContractCreateResponse(contract_id=contract.id, task_id=task.id, status="parsing")


@router.get("/{contract_id}/fields", response_model=list[ContractFieldOut])
def list_contract_fields(
    contract_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    page: int | None = None,
):
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    query = select(ContractField).where(ContractField.contract_id == contract.id)
    if page is not None:
        query = query.where(ContractField.page_number == page)
    return [_field_out(row) for row in db.scalars(query.order_by(ContractField.page_number))]


@router.patch("/{contract_id}/fields/{field_id}", response_model=ContractFieldOut)
def correct_field(
    contract_id: UUID,
    field_id: str,
    correction: FieldCorrection,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """A reviewer overriding what the parser read from the page."""
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)

    row = db.scalar(
        select(ContractField).where(
            ContractField.contract_id == contract.id,
            ContractField.template_field_id == field_id,
        )
    )
    if row is None:
        raise ConflictError(f"Field {field_id} does not exist on this contract")

    sensitive = pii.is_sensitive(field_id)
    previous = pii.decrypt(row.raw_value) if sensitive else row.raw_value
    patch = correction.model_dump(exclude_unset=True)
    if "value" in patch:
        normalized = normalized_for(row.field_type, field_id, patch["value"])
        row.raw_value = pii.encrypt(patch["value"]) if sensitive else patch["value"]
        row.normalized_value = pii.encrypt(normalized) if sensitive else normalized
    if "selected" in patch and patch["selected"] is not None:
        row.extra = {**(row.extra or {}), "selected": patch["selected"]}
    if "checked" in patch and patch["checked"] is not None:
        row.extra = {**(row.extra or {}), "checked": patch["checked"]}

    row.confidence_score = 1.0
    row.validation_error = None
    row.is_valid = True
    row.status = "verified" if correction.mark_verified else "filled"

    db.flush()
    rows = list(db.scalars(select(ContractField).where(ContractField.contract_id == contract.id)))
    contract.extracted_data = extracted_cache(rows)

    audit.log(
        db,
        audit.FIELD_CORRECTED,
        contract_id=contract.id,
        actor=user,
        # audit.log scrubs these for a sensitive field before they are written.
        details={
            "field_id": field_id,
            "from": previous,
            "to": patch.get("value", previous),
        },
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(row)
    return _field_out(row)


@router.get("/{contract_id}/fields/{field_id}/reveal")
def reveal_field(
    contract_id: UUID,
    field_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN)),
):
    """Decrypt one sensitive value. Admin only, and always audited.

    Reading an SSN is a deliberate act with a record attached, rather than a
    side effect of opening the contract.
    """
    contract = get_contract_or_404(db, contract_id)
    row = db.scalar(
        select(ContractField).where(
            ContractField.contract_id == contract.id,
            ContractField.template_field_id == field_id,
        )
    )
    if row is None:
        raise ConflictError(f"Field {field_id} does not exist on this contract")

    audit.log(
        db,
        audit.FIELD_REVEALED,
        contract_id=contract.id,
        actor=user,
        details={"field_id": field_id},
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        commit=True,
    )
    return {
        "field_id": field_id,
        "value": pii.decrypt(row.raw_value),
        "sensitive": pii.is_sensitive(field_id),
    }


@router.get("/{contract_id}/file")
def get_contract_file(
    contract_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Stream the original upload.

    These documents carry SSNs, so the bytes travel through an authorised
    request rather than any link that could be forwarded or cached.
    """
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    return StreamingResponse(
        storage.stream(contract.file_path),
        media_type="application/pdf",
        headers={"Cache-Control": "no-store"},
    )
