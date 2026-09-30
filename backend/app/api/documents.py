"""PDF editor v2 API (EDITOR-V2-PLAN §6), B1 endpoints.

Prefix `/api/contracts/{contract_id}/document`. Every route: the contract
exists (404) and the caller may view it (`assert_can_view_contract`); every
route that changes the DOCUMENT additionally needs `assert_can_edit_document`
(§11 Q2: admin, reviewer, uploader). Markup only needs viewing. A finalised
contract answers 409 `contract_completed` to every write.

`revision` always travels in the body (or `?revision=` for DELETE/preview);
`ETag: "rev-N"` on GETs is informational only.

B3 (§11 Q2): a user who may VIEW the contract but not edit its document (the
contractor who sees it through `contractor_email`) may still run a draft that
only FILLS form fields -- every op `field.value`; anything else is 403
`fill_only`. Such a draft never discards signatures.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import (
    assert_can_edit_document,
    assert_can_view_contract,
    can_edit_document,
    get_contract_or_404,
)
from app.core.database import get_db
from app.core.exceptions import NotFoundError, PayloadTooLargeError
from app.core.security import ROLE_ADMIN, client_ip, get_current_user
from app.models.contract import Contract
from app.models.document import Document, DocumentVersion
from app.models.user import User
from app.schemas.document import (
    BLOCK_ID_PATTERN,
    MAX_BODY_BYTES,
    PAGE_UID_PATTERN,
    CommitIn,
    CommitOut,
    DetectFieldsIn,
    DetectFieldsOut,
    DocumentOut,
    DraftLeaseIn,
    DraftOut,
    DraftPutIn,
    DraftSavedOut,
    DraftStartIn,
    FieldOut,
    LeaseOut,
    MarkupOut,
    MarkupPutIn,
    MarkupSavedOut,
    PageOut,
    TextModelOut,
    VersionOut,
)
from app.services import audit, document_review, documents
from app.services.documents import DocumentError
from app.services.storage import storage

router = APIRouter(prefix="/api/contracts/{contract_id}/document", tags=["document"])


def body_limit(request: Request) -> None:
    """413 before the body is parsed (§3: JSON <= 2 MB)."""
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) > MAX_BODY_BYTES:
                raise PayloadTooLargeError("Request body too large", {"limit": MAX_BODY_BYTES})
        except ValueError:
            pass


def _viewable(db: Session, contract_id: UUID, user: User) -> Contract:
    contract = get_contract_or_404(db, contract_id)
    assert_can_view_contract(contract, user)
    return contract


def _editable(db: Session, contract_id: UUID, user: User) -> Contract:
    contract = _viewable(db, contract_id, user)
    assert_can_edit_document(contract, user)
    return contract


def _drafter(db: Session, contract_id: UUID, user: User) -> tuple[Contract, bool]:
    """Draft routes: anyone who may view the contract; the second value says
    whether they are limited to filling forms (§11 Q2)."""
    contract = _viewable(db, contract_id, user)
    return contract, not can_edit_document(contract, user)


def _document(db: Session, contract: Contract, user: User) -> Document:
    return documents.ensure_document(db, contract, user)


def _capabilities(contract: Contract, user: User) -> dict:
    if contract.status == "completed":
        return {"can_edit": False, "can_edit_markup": False, "reason": "contract_completed", "can_fill": False}
    content = can_edit_document(contract, user)
    reason = None
    can_fill = contract.status not in documents.BUSY_STATUSES
    if not content:
        reason = "not_permitted"
    elif contract.status in documents.BUSY_STATUSES:
        content, reason = False, "contract_busy"
    return {"can_edit": content, "can_edit_markup": True, "reason": reason, "can_fill": can_fill}


def _draft_summary(db: Session, document: Document, user: User) -> dict | None:
    draft = document.draft
    if draft is None:
        return None
    return {
        "owner_name": documents.owner_name(db, draft.owner_user_id),
        "is_mine": draft.owner_user_id == user.id,
        "lease_expires_at": documents._aware(draft.lease_expires_at),
        "revision": draft.revision,
    }


def _draft_out(db: Session, document: Document, user: User) -> dict:
    draft = document.draft
    base = db.get(DocumentVersion, draft.base_version_id)
    return {
        "revision": draft.revision,
        "base_version_no": base.version_no if base else 0,
        "ops": list(draft.ops or []),
        "markup": draft.markup,
        "lease_expires_at": documents._aware(draft.lease_expires_at),
        "is_mine": draft.owner_user_id == user.id,
        "owner_name": documents.owner_name(db, draft.owner_user_id),
    }


# -- document, versions, pages, text ----------------------------------------------


@router.get("", response_model=DocumentOut)
def get_document(
    contract_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = _viewable(db, contract_id, user)
    document = _document(db, contract, user)
    current = db.get(DocumentVersion, document.current_version_id)
    count = db.scalar(
        select(func.count()).select_from(DocumentVersion).where(DocumentVersion.document_id == document.id)
    )
    response.headers["ETag"] = f'"rev-{document.markup_revision}"'
    from app.services import pdf_engine

    try:
        warnings = pdf_engine.open_warnings(storage.get_bytes(current.file_path), current.file_hash)
    except pdf_engine.EngineError as exc:
        raise documents.from_engine(exc) from exc
    return {
        "warnings": warnings,
        "document_id": str(document.id),
        "current_version": documents.version_out(db, current),
        "versions_count": int(count or 0),
        "markup_revision": document.markup_revision,
        "draft": _draft_summary(db, document, user),
        "capabilities": _capabilities(contract, user),
    }


@router.get("/versions", response_model=list[VersionOut])
def list_versions(
    contract_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = _viewable(db, contract_id, user)
    document = _document(db, contract, user)
    return [
        documents.version_out(db, version)
        for version in db.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version_no)
        )
    ]


@router.get("/versions/{version_no}/file")
def get_version_file(
    contract_id: UUID,
    version_no: int = Path(ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = _viewable(db, contract_id, user)
    document = _document(db, contract, user)
    version = documents.version_by_no(db, document, version_no)
    return Response(
        content=storage.get_bytes(version.file_path),
        media_type="application/pdf",
        headers={"Cache-Control": "no-store", "X-Content-SHA256": version.file_hash},
    )


@router.get("/versions/{version_no}/pages", response_model=list[PageOut])
def list_pages(
    contract_id: UUID,
    version_no: int = Path(ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = _viewable(db, contract_id, user)
    document = _document(db, contract, user)
    version = documents.version_by_no(db, document, version_no)
    return [
        {
            "page_uid": page.page_uid,
            "position": page.position,
            "origin_page": page.origin_page,
            "rotation": page.rotation,
            "width_pt": page.width_pt,
            "height_pt": page.height_pt,
            "has_text": page.has_text,
            "scanned": page.scanned,
        }
        for page in version.pages
    ]


@router.get("/versions/{version_no}/pages/{page_uid}/text", response_model=TextModelOut)
def get_page_text(
    contract_id: UUID,
    version_no: int = Path(ge=1),
    page_uid: str = Path(pattern=PAGE_UID_PATTERN),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services import pdf_engine

    contract = _viewable(db, contract_id, user)
    document = _document(db, contract, user)
    version = documents.version_by_no(db, document, version_no)
    page = next((p for p in version.pages if p.page_uid == page_uid), None)
    if page is None:
        raise NotFoundError("No such page in this version")
    try:
        model = pdf_engine.page_text_model(
            storage.get_bytes(version.file_path), page.position - 1, version.file_hash
        )
    except pdf_engine.EngineError as exc:
        raise documents.from_engine(exc) from exc
    return {
        "page_uid": page_uid,
        "version_no": version.version_no,
        "file_hash": version.file_hash,
        **pdf_engine.text_model_dict(model),
    }


def _version_page(db: Session, contract_id: UUID, version_no: int, page_uid: str, user: User):
    contract = _viewable(db, contract_id, user)
    document = _document(db, contract, user)
    version = documents.version_by_no(db, document, version_no)
    page = next((p for p in version.pages if p.page_uid == page_uid), None)
    if page is None:
        raise NotFoundError("No such page in this version")
    return version, page


@router.get("/versions/{version_no}/pages/{page_uid}/images")
def get_page_images(
    contract_id: UUID,
    version_no: int = Path(ge=1),
    page_uid: str = Path(pattern=PAGE_UID_PATTERN),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """B2: the page's images, `[{xref, bbox}]` -- targets of `image.remove`."""
    from app.services import pdf_engine

    version, page = _version_page(db, contract_id, version_no, page_uid, user)
    try:
        return pdf_engine.page_images(storage.get_bytes(version.file_path), page.position - 1)
    except pdf_engine.EngineError as exc:
        raise documents.from_engine(exc) from exc


@router.get("/versions/{version_no}/pages/{page_uid}/links")
def get_page_links(
    contract_id: UUID,
    version_no: int = Path(ge=1),
    page_uid: str = Path(pattern=PAGE_UID_PATTERN),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """B2: the page's links, `[{link_id, rect, target}]` -- targets of
    `link.update` / `link.remove`. A page target is named by its `page_uid`."""
    from app.services import pdf_engine

    version, page = _version_page(db, contract_id, version_no, page_uid, user)
    uid_by_index = {p.position - 1: p.page_uid for p in version.pages}
    try:
        return pdf_engine.page_links(storage.get_bytes(version.file_path), page.position - 1, uid_by_index)
    except pdf_engine.EngineError as exc:
        raise documents.from_engine(exc) from exc


# -- markup -------------------------------------------------------------------------------


@router.get("/markup", response_model=MarkupOut)
def get_markup(
    contract_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract = _viewable(db, contract_id, user)
    document = _document(db, contract, user)
    response.headers["ETag"] = f'"rev-{document.markup_revision}"'
    return {"revision": document.markup_revision, "elements": list(document.markup or [])}


@router.put("/markup", response_model=MarkupSavedOut, dependencies=[Depends(body_limit)])
def put_markup(
    contract_id: UUID,
    body: MarkupPutIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Save the whole markup layer (§11 Q1: when the contract is past review
    and the review-relevant elements -- `markup-content` kinds -- changed, the
    review is re-opened HERE, at save: markup is never baked into a version,
    so this PUT is the moment such a change becomes the document's; the same
    rule is applied when a draft carrying markup is committed)."""
    contract = _viewable(db, contract_id, user)
    documents.assert_not_completed(contract)
    document = _document(db, contract, user)
    if document.draft is not None and documents.lease_active(document.draft):
        raise DocumentError(409, "draft_active", "The document is being edited; markup is saved with the draft")
    if body.revision != document.markup_revision:
        raise DocumentError(
            409, "revision_conflict", "The markup has changed since you loaded it",
            {"current_revision": document.markup_revision},
        )
    current = db.get(DocumentVersion, document.current_version_id)
    problems = documents.markup_page_problems(body.elements, current)
    if problems:
        raise DocumentError(422, "invalid_markup", "Some elements name a page this document does not have",
                            {"elements": problems[:200]})
    before = list(document.markup or [])
    swapped = db.execute(
        update(Document)
        .where(Document.id == document.id, Document.markup_revision == body.revision)
        .values(
            markup=body.elements,
            markup_revision=body.revision + 1,
            markup_updated_by=user.id,
            updated_at=documents.now_utc(),
        )
        .execution_options(synchronize_session=False)
    )
    if swapped.rowcount != 1:
        db.rollback()
        raise DocumentError(409, "revision_conflict", "The markup has changed since you loaded it", {})
    review_reset = False
    if documents.markup_content_changed(before, body.elements):
        review_reset = document_review.on_markup_content_change(db, contract, user)
    audit.log(
        db,
        audit.DOCUMENT_MARKUP_SAVED,
        contract_id=contract.id,
        actor=user,
        details={**documents.markup_stats(body.elements), "revision": body.revision + 1,
                 "via": "put", "review_reset": review_reset},
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return {"revision": body.revision + 1, "review_reset": review_reset}


# -- drafts ----------------------------------------------------------------------------------


@router.post("/draft", response_model=DraftOut, status_code=201)
def start_draft(
    contract_id: UUID,
    body: DraftStartIn,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract, fill_only = _drafter(db, contract_id, user)
    document = _document(db, contract, user)
    _draft, created = documents.start_draft(db, contract, document, user, body, fill_only=fill_only)
    if not created:
        response.status_code = 200
    db.refresh(document)
    return _draft_out(db, document, user)


@router.get("/draft", response_model=DraftOut)
def get_draft(
    contract_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract, fill_only = _drafter(db, contract_id, user)
    document = _document(db, contract, user)
    if document.draft is None:
        raise DocumentError(404, "draft_not_found", "There is no draft for this document")
    documents.assert_fill_access(document.draft, user, fill_only)
    return _draft_out(db, document, user)


@router.put("/draft", response_model=DraftSavedOut, dependencies=[Depends(body_limit)])
def put_draft(
    contract_id: UUID,
    body: DraftPutIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract, fill_only = _drafter(db, contract_id, user)
    document = _document(db, contract, user)
    return documents.save_draft(db, contract, document, user, body, fill_only=fill_only)


@router.post("/draft/lease", response_model=LeaseOut)
def renew_lease(
    contract_id: UUID,
    body: DraftLeaseIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract, _fill_only = _drafter(db, contract_id, user)
    document = _document(db, contract, user)
    return {"lease_expires_at": documents.renew_lease(db, contract, document, user, body)}


@router.delete("/draft", status_code=204)
def discard_draft(
    contract_id: UUID,
    revision: int = Query(ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract, _fill_only = _drafter(db, contract_id, user)
    document = _document(db, contract, user)
    documents.discard_draft(db, contract, document, user, revision, is_admin=user.role == ROLE_ADMIN)
    return Response(status_code=204)


@router.get("/draft/pages/{page_uid}/preview")
def preview_page(
    contract_id: UUID,
    page_uid: str = Path(pattern=PAGE_UID_PATTERN),
    revision: int = Query(ge=0),
    hide: str | None = Query(default=None, pattern=BLOCK_ID_PATTERN),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services import pdf_engine

    contract, fill_only = _drafter(db, contract_id, user)
    document = _document(db, contract, user)
    draft = document.draft
    if draft is None:
        raise DocumentError(404, "draft_not_found", "There is no draft for this document")
    documents.assert_fill_access(draft, user, fill_only)
    if draft.revision != revision:
        raise DocumentError(409, "revision_conflict", "The draft has changed", {"current_revision": draft.revision})
    base = db.get(DocumentVersion, draft.base_version_id)
    if not any(page.page_uid == page_uid for page in base.pages):
        raise NotFoundError("No such page in this draft")
    try:
        data = pdf_engine.render_page_preview(
            storage.get_bytes(base.file_path),
            base.file_hash,
            documents.page_refs(base),
            page_uid,
            pdf_engine.from_stored(draft.ops or []),
            pdf_engine.ops_hash(draft.ops or []),
            hide_block=hide,
        )
    except pdf_engine.EngineError as exc:
        raise documents.from_engine(exc) from exc
    return Response(content=data, media_type="application/pdf", headers={"Cache-Control": "no-store"})


_DRAFT_PDF_CACHE: dict[tuple, bytes] = {}
_DRAFT_PDF_CACHE_MAX = 8


@router.get("/draft/preview.pdf")
def preview_draft_pdf(
    contract_id: UUID,
    revision: int = Query(ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """B2: the WHOLE draft as a PDF (every op applied, exactly as a commit
    would), for the pages panel after `page.*` ops. `X-Page-Uids` lists the
    page uid of every page, in order. Nothing is stored."""
    from app.services import pdf_engine

    contract, fill_only = _drafter(db, contract_id, user)
    document = _document(db, contract, user)
    draft = document.draft
    if draft is None:
        raise DocumentError(404, "draft_not_found", "There is no draft for this document")
    documents.assert_fill_access(draft, user, fill_only)
    if draft.revision != revision:
        raise DocumentError(409, "revision_conflict", "The draft has changed", {"current_revision": draft.revision})
    base = db.get(DocumentVersion, draft.base_version_id)
    key = (base.file_hash, pdf_engine.ops_hash(draft.ops or []))
    cached = _DRAFT_PDF_CACHE.get(key)
    if cached is None:
        try:
            result = pdf_engine.apply_ops(
                storage.get_bytes(base.file_path), documents.page_refs(base), pdf_engine.from_stored(draft.ops or [])
            )
        except pdf_engine.EngineError as exc:
            raise documents.from_engine(exc) from exc
        cached = (result.pdf_bytes, ",".join(result.page_uids))
        _DRAFT_PDF_CACHE[key] = cached
        while len(_DRAFT_PDF_CACHE) > _DRAFT_PDF_CACHE_MAX:
            _DRAFT_PDF_CACHE.pop(next(iter(_DRAFT_PDF_CACHE)))
    data, uids = cached
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Cache-Control": "no-store", "X-Page-Uids": uids},
    )


@router.post("/draft/commit", response_model=CommitOut, status_code=201)
def commit_draft(
    contract_id: UUID,
    body: CommitIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    contract, fill_only = _drafter(db, contract_id, user)
    document = _document(db, contract, user)
    return documents.commit_draft(db, contract, document, user, body, request, fill_only=fill_only)


# -- B3: form fields --------------------------------------------------------------------------

_FIELDS_CACHE: dict[tuple, list] = {}
_FIELDS_CACHE_MAX = 32


@router.get("/versions/{version_no}/fields", response_model=list[FieldOut])
def list_fields(
    contract_id: UUID,
    version_no: int = Path(ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every form field widget of a version -- the editor's and the uploaded
    PDF's own (AcroForm) -- with its current value, in tab order."""
    from app.services import pdf_engine

    contract = _viewable(db, contract_id, user)
    document = _document(db, contract, user)
    version = documents.version_by_no(db, document, version_no)
    uid_by_index = {page.position - 1: page.page_uid for page in version.pages}
    key = (version.file_hash, tuple(sorted(uid_by_index.items())))
    cached = _FIELDS_CACHE.get(key)
    if cached is None:
        try:
            cached = pdf_engine.field_dicts(storage.get_bytes(version.file_path), uid_by_index)
        except pdf_engine.EngineError as exc:
            raise documents.from_engine(exc) from exc
        _FIELDS_CACHE[key] = cached
        while len(_FIELDS_CACHE) > _FIELDS_CACHE_MAX:
            _FIELDS_CACHE.pop(next(iter(_FIELDS_CACHE)))
    return cached


@router.post("/detect-fields", response_model=DetectFieldsOut, dependencies=[Depends(body_limit)])
def detect_fields(
    contract_id: UUID,
    body: DetectFieldsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """«Detect form fields» on pages of the CURRENT version: suggestions only,
    nothing is stored (an accepted one becomes a `field.add` in the draft)."""
    from app.services import form_suggestions, pdf_engine

    contract = _editable(db, contract_id, user)
    document = _document(db, contract, user)
    version = db.get(DocumentVersion, document.current_version_id)
    by_uid = {page.page_uid: page.position for page in version.pages}
    wanted = body.page_uids or [page.page_uid for page in version.pages]
    unknown = [uid for uid in wanted if uid not in by_uid]
    if unknown:
        raise DocumentError(422, "unknown_page", "Some pages are not in the current version",
                            {"page_uids": unknown[:100]})
    pages = sorted({(by_uid[uid], uid) for uid in wanted})
    try:
        suggestions = form_suggestions.suggest(storage.get_bytes(version.file_path), pages)
    except pdf_engine.EngineError as exc:
        raise documents.from_engine(exc) from exc
    return {"version_no": version.version_no, "suggestions": suggestions}
