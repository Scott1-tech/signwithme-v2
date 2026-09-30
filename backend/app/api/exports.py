"""Exports ("Download PDF") of a document version (EDITOR-V2-PLAN §6; B2).

    POST   /api/contracts/{cid}/document/exports         {version_no?, include_markup=true, flatten_forms=false}
    GET    /api/contracts/{cid}/document/exports/{id}    status
    GET    /api/contracts/{cid}/document/exports/{id}/file   the PDF (409 `export_not_ready` until done)
    DELETE /api/contracts/{cid}/document/exports/{id}    cancel / discard -> 204

What an export carries (§4 "Nima qayerda"): the version's content ops, and --
with `include_markup` (default) -- ALL saved markup, including elements the
user hid on screen ("Show annotations" is a view setting; nothing is dropped
silently). Never signatures or slot dates: those exist only in the final.

The job runs in the request (a version is a few MB and one engine pass);
the table keeps queued/running/cancelled for a worker to take over later.
Reuse: the same `(version, options_hash)` returns the existing done job --
`options_hash` includes the markup revision, so a markup change exports anew.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import assert_can_view_contract, get_contract_or_404
from app.core.database import get_db
from app.core.exceptions import AppError, NotFoundError
from app.core.security import client_ip, get_current_user
from app.models.document import DocumentVersion, ExportJob
from app.models.user import User
from app.services import audit, documents
from app.services.documents import DocumentError
from app.services.storage import export_key, storage

router = APIRouter(prefix="/api/contracts/{contract_id}/document/exports", tags=["document"])

_JOB_ID = r"^[0-9a-f]{32}$"


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_no: int | None = Field(default=None, ge=1)
    include_markup: StrictBool = True
    flatten_forms: StrictBool = False


class ExportJobOut(BaseModel):
    id: str
    status: str
    version_no: int
    include_markup: bool
    flatten_forms: bool
    result_sha256: str | None = None
    result_bytes: int | None = None
    error: str | None = None
    warnings: list[dict] = []
    created_at: datetime
    finished_at: datetime | None = None


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _out(db: Session, job: ExportJob) -> dict:
    version = db.get(DocumentVersion, job.version_id)
    return {
        "id": job.id.hex,
        "status": job.status,
        "version_no": version.version_no if version else 0,
        "include_markup": bool((job.options or {}).get("include_markup", True)),
        "flatten_forms": bool((job.options or {}).get("flatten_forms", False)),
        "result_sha256": job.result_sha256,
        "result_bytes": job.result_bytes,
        "error": job.error,
        "warnings": list(job.warnings or []),
        "created_at": _aware(job.created_at),
        "finished_at": _aware(job.finished_at),
    }


def _job(db: Session, contract_id: UUID, job_id: str, user: User):
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    document = contract.document
    try:
        key = UUID(hex=job_id)
    except ValueError as exc:
        raise NotFoundError("Export not found") from exc
    job = db.get(ExportJob, key)
    if document is None or job is None or job.document_id != document.id:
        raise NotFoundError("Export not found")
    return contract, document, job


def options_hash(version_id, options: dict, markup_revision: int) -> str:
    blob = json.dumps(
        {"version": str(version_id), "options": options,
         "markup_revision": markup_revision if options.get("include_markup") else None},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def render_export(contract, document, version: DocumentVersion, options: dict) -> tuple[bytes, list[dict]]:
    """The export bytes and warnings. No markup to draw and no form to
    flatten -> the version's own bytes, unchanged (its hash is the version
    hash). `flatten_forms` (B3): every widget becomes page content first."""
    from app.api.signatures import _annotation_placements, _v7_placements
    from app.schemas.contract import ANNOTATION_MODELS, V7_KINDS
    from app.services.final_pipeline import FinalInputs, build_final, flatten_forms

    data = storage.get_bytes(version.file_path)
    warnings: list[dict] = []
    if options.get("flatten_forms"):
        data = flatten_forms(data)
    if not options.get("include_markup") or not document.markup:
        return data, warnings
    position = {page.page_uid: page.position for page in version.pages}
    legacy, v7 = [], []
    skipped = 0
    for element in document.markup:
        page = position.get(element.get("page_uid"))
        if page is None:
            skipped += 1
            continue
        fields = {k: v for k, v in element.items() if k != "page_uid"}
        model = ANNOTATION_MODELS[fields["kind"]].model_validate({**fields, "page": page})
        if fields["kind"] in V7_KINDS:
            v7.append({**model.model_dump(mode="json"), "page": page})
        else:
            legacy.append(model)
    if skipped:
        warnings.append({"code": "markup_page_missing", "count": skipped})
    if not legacy and not v7:
        return data, warnings
    result = build_final(
        FinalInputs(
            source=data,
            legacy_annotations=_annotation_placements(None, contract.id, legacy),  # type: ignore[arg-type]
            v7=_v7_placements(v7, contract.id, data),
            metadata={"title": f"Contract - {contract.contractor_name or contract.id}"},
            flatten_forms=False,  # done above, when asked
        )
    )
    return result.pdf_bytes, warnings


@router.post("", response_model=ExportJobOut, status_code=202)
def create_export(
    contract_id: UUID,
    body: ExportIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    document = documents.ensure_document(db, contract, user)
    version = (
        documents.version_by_no(db, document, body.version_no)
        if body.version_no is not None
        else db.get(DocumentVersion, document.current_version_id)
    )
    options = {"include_markup": body.include_markup, "flatten_forms": body.flatten_forms}
    digest = options_hash(version.id, options, document.markup_revision or 0)
    reuse = db.scalar(
        select(ExportJob)
        .where(ExportJob.document_id == document.id, ExportJob.version_id == version.id,
               ExportJob.options_hash == digest, ExportJob.status == "done")
        .order_by(ExportJob.created_at.desc())
    )
    if reuse is not None:
        return _out(db, reuse)

    job = ExportJob(
        id=uuid4(), document_id=document.id, version_id=version.id, requested_by=user.id,
        options=options, options_hash=digest, status="running", warnings=[],
    )
    db.add(job)
    db.commit()
    try:
        data, warnings = render_export(contract, document, version, options)
    except (AppError, DocumentError) as exc:
        job.status, job.error = "failed", (getattr(exc, "code", None) or "export_failed")[:64]
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise
    except Exception:  # noqa: BLE001 - recorded, then re-raised as a 500
        job.status, job.error = "failed", "export_failed"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise
    db.refresh(job)
    if job.cancel_requested:  # pragma: no cover - only reachable with a worker
        job.status = "cancelled"
        db.commit()
        return _out(db, job)
    key = export_key(contract.id, job.id.hex)
    storage.put_bytes(key, data)
    job.result_key = key
    job.result_sha256 = hashlib.sha256(data).hexdigest()
    job.result_bytes = len(data)
    job.warnings = warnings
    job.status = "done"
    job.finished_at = datetime.now(timezone.utc)
    audit.log(
        db,
        audit.DOCUMENT_EXPORTED,
        contract_id=contract.id,
        actor=user,
        details={
            "export_id": job.id.hex,
            "version_no": version.version_no,
            "include_markup": body.include_markup,
            "flatten_forms": body.flatten_forms,
            "sha256": job.result_sha256,
            **(documents.markup_stats(document.markup) if body.include_markup else {}),
        },
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return _out(db, job)


@router.get("/{export_id}", response_model=ExportJobOut)
def get_export(
    contract_id: UUID,
    export_id: str = Path(pattern=_JOB_ID),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _contract, _document, job = _job(db, contract_id, export_id, user)
    return _out(db, job)


@router.get("/{export_id}/file")
def get_export_file(
    contract_id: UUID,
    export_id: str = Path(pattern=_JOB_ID),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract, _document, job = _job(db, contract_id, export_id, user)
    if job.status != "done" or not job.result_key:
        raise DocumentError(409, "export_not_ready", "The export is not ready", {"status": job.status})
    version = db.get(DocumentVersion, job.version_id)
    name = f"contract-{str(contract.id)[:8]}-v{version.version_no if version else 0}.pdf"
    return Response(
        content=storage.get_bytes(job.result_key),
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store",
            "X-Content-SHA256": job.result_sha256 or "",
            "Content-Disposition": f'attachment; filename="{name}"',
        },
    )


@router.delete("/{export_id}", status_code=204)
def delete_export(
    contract_id: UUID,
    export_id: str = Path(pattern=_JOB_ID),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Cancel a queued/running job, or discard a finished one's file. The row
    stays (status `cancelled`) -- the audit entry of a done export points at it."""
    _contract, _document, job = _job(db, contract_id, export_id, user)
    key = job.result_key
    job.cancel_requested = True
    job.status = "cancelled"
    job.result_key = None
    job.finished_at = job.finished_at or datetime.now(timezone.utc)
    db.commit()
    if key:
        try:
            storage.delete(key)
        except Exception:  # noqa: BLE001 - the job is cancelled either way
            pass
    return Response(status_code=204)
