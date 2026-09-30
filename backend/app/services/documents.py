"""PDF editor v2: the document service (EDITOR-V2-PLAN §2, §6).

* `ensure_document` -- the `documents` row is created LAZILY (first editor
  request), with version 1 = the original upload (`contracts.file_path`,
  never copied) and a stable `page_uid` per page.
* `signing_source` -- THE way to find the bytes that are reviewed, compared and
  signed. No `documents` row (a legacy contract) -> the original upload with
  an identity page map, so legacy contracts behave byte for byte as before.
* drafts: one per document, leased (120 s) to one user + browser session,
  revision-checked (CAS) on every write.
* `commit_draft` -- atomic (§2): validate -> idempotency -> engine -> file ->
  ONE transaction (version + pages, CAS on `documents.current_version_id`,
  draft deleted, review reset, superseded signatures, audit) -> on any failure
  the new file is deleted again. `sweep_orphan_versions` is the janitor for a
  crash between the file write and the commit.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.models.contract import Contract
from app.models.document import Document, DocumentDraft, DocumentPage, DocumentVersion
from app.models.signature import Signature
from app.models.user import User
from app.schemas.document import (
    LEASE_SECONDS,
    MARKUP_CONTENT_KINDS,
    op_layer,
)
from app.services import audit
from app.services.storage import PREFIX_CONTRACTS, storage

logger = logging.getLogger(__name__)

PAGE_UID_PREFIX = "pg_"
BUSY_STATUSES = ("uploaded", "parsing")


class DocumentError(AppError):
    """An editor refusal with its own machine code (`{error, message, details}`)."""

    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message, details)
        self.status_code = status
        self.code = code


def from_engine(exc) -> DocumentError:
    return DocumentError(exc.status, exc.code, exc.message, exc.details)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def new_page_uid() -> str:
    return PAGE_UID_PREFIX + secrets.token_hex(6)


def version_key(contract_id, version_no: int, sha256: str) -> str:
    """Flat in the contract's folder, so the admin deletion's
    `delete_prefix("contracts/<id>")` (not recursive) takes it."""
    return f"{PREFIX_CONTRACTS}/{contract_id}/version-{int(version_no)}-{sha256[:8]}.pdf"


_VERSION_KEY_RE = re.compile(r"^contracts/[0-9a-fA-F-]{32,36}/version-\d+-[0-9a-f]{8}\.pdf$")


# -- the signing source -------------------------------------------------------------


@dataclass
class SigningSource:
    """What is signed: storage key, hash, page count and the page map.

    `origin_to_position` maps page N of the ORIGINAL upload (= template page N)
    to its position in the current version (None = that page was removed).
    `None` (legacy, and every B1 document) means identity.
    """

    bytes_key: str
    file_hash: str
    page_count: int | None
    version_no: int | None = None
    version_id: object | None = None
    origin_to_position: dict[int, int | None] | None = None
    uid_by_position: dict[int, str] | None = None
    # B2: /Rotate of every page now, and of each ORIGINAL page in version 1
    # (template coordinates are in the original's displayed space).
    rotation_by_position: dict[int, int] | None = None
    origin_rotation: dict[int, int] | None = None
    size_by_position: dict[int, tuple[float, float]] | None = None

    @property
    def legacy(self) -> bool:
        return self.version_no is None

    def position_for(self, origin_page: int | None) -> int | None:
        """Template page -> position now; None = that page was removed."""
        if origin_page is None or self.origin_to_position is None:
            return origin_page
        return self.origin_to_position.get(int(origin_page))

    def page_uid_at(self, position: int | None) -> str | None:
        if position is None or self.uid_by_position is None:
            return None
        return self.uid_by_position.get(int(position))

    def position_of_uid(self, page_uid: str | None) -> int | None:
        if page_uid is None or self.uid_by_position is None:
            return None
        for position, uid in self.uid_by_position.items():
            if uid == page_uid:
                return position
        return None

    def template_bbox(self, origin_page: int | None, bbox: dict | None) -> dict | None:
        """A template box (original page, displayed as it was) -> the same spot
        on the page as it is displayed NOW (a `page.rotate` since). Identity
        when nothing was rotated (and for a legacy contract)."""
        if bbox is None or origin_page is None or self.rotation_by_position is None:
            return bbox
        position = self.position_for(origin_page)
        if position is None:
            return bbox
        before = (self.origin_rotation or {}).get(int(origin_page), 0)
        now = self.rotation_by_position.get(position, 0)
        if before == now:
            return bbox
        from app.services.geometry import BBox, remap_displayed_box

        width, height = (self.size_by_position or {}).get(position, (612.0, 792.0))
        moved = remap_displayed_box(BBox.from_dict(bbox), width, height, before, now)
        return moved.to_dict()

    def rotated_since_template(self, origin_page: int | None) -> bool:
        if origin_page is None or self.rotation_by_position is None:
            return False
        position = self.position_for(origin_page)
        if position is None:
            return False
        return (self.origin_rotation or {}).get(int(origin_page), 0) != self.rotation_by_position.get(position, 0)

    @property
    def page_map(self) -> dict[int, int | None] | None:
        """For `pdf_parser.extract(..., page_map)`: None when identity.
        A removed template page maps to None (its fields read as missing)."""
        if self.origin_to_position is None:
            return None
        if all(origin == position for origin, position in self.origin_to_position.items()) and (
            self.page_count is None or len(self.origin_to_position) == self.page_count
        ):
            return None
        return dict(self.origin_to_position)


def current_version(contract) -> DocumentVersion | None:
    """The contract's current version, or None for a legacy contract.

    Reads through the ORM relationships, so it needs the contract attached to
    a session; a plain object (as some unit tests use) is legacy."""
    try:
        document = getattr(contract, "document", None)
    except Exception:  # noqa: BLE001 - detached / not an ORM object
        return None
    if document is None or document.current_version_id is None:
        return None
    return document.current_version


def signing_source(contract) -> SigningSource:
    version = current_version(contract)
    if version is None:
        return SigningSource(
            bytes_key=contract.file_path,
            file_hash=contract.file_hash,
            page_count=None,
        )
    return _source_for(version)


# -- lazy creation ------------------------------------------------------------------------


def ensure_document(db: Session, contract: Contract, user: User | None) -> Document:
    """The contract's document, created on first use (version 1 = original).

    Refuses (422 `pdf_encrypted` / `pdf_corrupt`) a file the engine cannot
    open; nothing is created then, and the legacy viewer keeps working.
    """
    if contract.document is not None:
        return contract.document
    from app.services import pdf_engine

    data = storage.get_bytes(contract.file_path)
    try:
        pages, warnings = pdf_engine.inspect_pages(data)
    except pdf_engine.EngineError as exc:
        raise from_engine(exc) from exc
    document = Document(
        contract_id=contract.id,
        markup=[],
        markup_revision=0,
        created_by=user.id if user else None,
    )
    db.add(document)
    try:
        db.flush()
        version = DocumentVersion(
            document_id=document.id,
            version_no=1,
            parent_version_id=None,
            kind="original",
            file_path=contract.file_path,
            file_hash=contract.file_hash,
            file_bytes=len(data),
            page_count=len(pages),
            ops_applied=[],
            op_summary={},
            content_changed=False,
            review_reset=False,
            engine=None,
            engine_ref=None,
            created_by=user.id if user else None,
        )
        version.pages = [
            DocumentPage(
                position=info.position,
                page_uid=new_page_uid(),
                origin_page=info.position,
                rotation=info.rotation,
                width_pt=info.width_pt,
                height_pt=info.height_pt,
                has_text=info.has_text,
                scanned=info.scanned,
            )
            for info in pages
        ]
        db.add(version)
        db.flush()
        document.original_version_id = version.id
        document.current_version_id = version.id
        audit.log(
            db,
            audit.DOCUMENT_CREATED,
            contract_id=contract.id,
            actor=user,
            details={
                "version_no": 1,
                "page_count": len(pages),
                "file_hash": contract.file_hash,
                "repaired": any(w.get("code") == "pdf_repaired" for w in warnings),
            },
        )
        db.commit()
    except IntegrityError:
        # A concurrent request created it first: use theirs.
        db.rollback()
        db.expire_all()
        existing = db.scalar(select(Document).where(Document.contract_id == contract.id))
        if existing is None:
            raise
        return existing
    db.refresh(document)
    return document


def version_by_no(db: Session, document: Document, version_no: int) -> DocumentVersion:
    version = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id, DocumentVersion.version_no == version_no
        )
    )
    if version is None:
        raise DocumentError(404, "version_not_found", f"Version {version_no} does not exist")
    return version


# -- drafts ------------------------------------------------------------------------------


def lease_until() -> datetime:
    return now_utc() + timedelta(seconds=LEASE_SECONDS)


def lease_active(draft: DocumentDraft) -> bool:
    return _aware(draft.lease_expires_at) > now_utc()


def payload_hash(ops: list[dict], markup: list[dict] | None) -> str:
    blob = json.dumps({"ops": ops, "markup": markup}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def assert_not_completed(contract: Contract) -> None:
    if contract.status == "completed":
        raise DocumentError(409, "contract_completed", "The contract is finalised; its document cannot change")


def assert_not_busy(contract: Contract) -> None:
    if contract.status in BUSY_STATUSES:
        raise DocumentError(
            409, "contract_busy", "The contract is being analysed; try again when it has finished",
            {"status": contract.status},
        )


def owner_name(db: Session, user_id) -> str | None:
    owner = db.get(User, user_id)
    return (owner.full_name or owner.email) if owner else None


FILL_KINDS = ("field.value",)


def _fill_problems(ops) -> list[dict]:
    """Ops a fill-only user may not make (§11 Q2: markup and form filling)."""
    return [{"id": op.id, "reason": "fill_only"} for op in ops if getattr(op, "kind", None) not in FILL_KINDS]


def assert_fill_only_ops(ops) -> None:
    problems = _fill_problems(ops)
    if problems:
        raise DocumentError(
            403, "fill_only", "You may fill the form fields of this document, not change it",
            {"ops": problems[:200]},
        )


def assert_fill_access(draft: DocumentDraft, user: User, fill_only: bool) -> None:
    """A fill-only user sees only a draft of their own (an editor's draft
    may hold content edits that are not theirs to read)."""
    if fill_only and draft.owner_user_id != user.id:
        raise DocumentError(403, "fill_only", "Someone else is editing this document", {})


def start_draft(db: Session, contract: Contract, document: Document, user: User, body,
                fill_only: bool = False) -> tuple[DocumentDraft, bool]:
    assert_not_completed(contract)
    assert_not_busy(contract)
    current = db.get(DocumentVersion, document.current_version_id)
    if body.base_version_no != current.version_no:
        raise DocumentError(
            409, "base_outdated", "A newer version exists; reload the document",
            {"current_version_no": current.version_no},
        )
    draft = document.draft
    if draft is not None:
        mine = draft.owner_user_id == user.id and draft.client_session_id == body.client_session_id
        if mine:
            draft.lease_expires_at = lease_until()
            db.commit()
            return draft, False
        if fill_only and draft.owner_user_id != user.id and _fill_problems(
            [_Kind(op.get("kind")) for op in (draft.ops or [])]
        ):
            # An editor's (lapsed) draft with content edits is not a fill-only
            # user's to take over: they could commit edits they may not make.
            raise DocumentError(403, "fill_only", "Someone else is editing this document", {})
        if lease_active(draft) and not body.take_over:
            raise DocumentError(
                409, "draft_locked", "Someone else is editing this document",
                {
                    "owner_name": owner_name(db, draft.owner_user_id),
                    "lease_expires_at": _aware(draft.lease_expires_at).isoformat(),
                },
            )
        # Take over (lease expired, or explicitly): the ops stay, the owner changes.
        previous_owner = draft.owner_user_id
        draft.owner_user_id = user.id
        draft.client_session_id = body.client_session_id
        draft.revision += 1
        draft.lease_expires_at = lease_until()
        audit.log(
            db,
            audit.DOCUMENT_DRAFT_TAKEN_OVER,
            contract_id=contract.id,
            actor=user,
            details={"revision": draft.revision, "previous_owner_id": str(previous_owner)},
        )
        db.commit()
        return draft, False
    draft = DocumentDraft(
        document_id=document.id,
        base_version_id=current.id,
        revision=0,
        ops=[],
        markup=None,
        payload_hash=payload_hash([], None),
        owner_user_id=user.id,
        client_session_id=body.client_session_id,
        lease_expires_at=lease_until(),
    )
    db.add(draft)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DocumentError(409, "draft_locked", "Someone else started editing this document", {})
    db.refresh(draft)
    return draft, True


@dataclass(frozen=True)
class _Kind:
    kind: str | None
    id: str = ""


def _require_draft(document: Document) -> DocumentDraft:
    if document.draft is None:
        raise DocumentError(404, "draft_not_found", "There is no draft for this document")
    return document.draft


def _assert_owner(draft: DocumentDraft, user: User, client_session_id: str | None) -> None:
    if draft.owner_user_id != user.id or (
        client_session_id is not None and draft.client_session_id != client_session_id
    ):
        raise DocumentError(409, "draft_taken_over", "This draft is now being edited elsewhere")


def _assert_revision(draft: DocumentDraft, revision: int) -> None:
    if draft.revision != revision:
        raise DocumentError(
            409, "revision_conflict", "The draft has changed since you loaded it",
            {"current_revision": draft.revision},
        )


def _assert_base_current(draft: DocumentDraft, document: Document) -> None:
    if draft.base_version_id != document.current_version_id:
        raise DocumentError(409, "base_outdated", "A newer version exists; reload the document", {})


def page_refs(version: DocumentVersion):
    from app.services.pdf_engine import PageRef

    return [PageRef(page.page_uid, page.position) for page in version.pages]


def markup_page_problems(elements: list[dict] | None, version: DocumentVersion, ops=()) -> list[dict]:
    """Markup naming a page neither the version nor the draft's own
    `page.insert` has. (Markup on a page the draft DELETES is fine here: it is
    dropped with the page at commit, with a warning.)"""
    if not elements:
        return []
    uids = {page.page_uid for page in version.pages}
    uids |= {op.new_page_uid for op in ops if getattr(op, "kind", None) == "page.insert"}
    return [
        {"id": element["id"], "reason": "unknown_page"}
        for element in elements
        if element.get("page_uid") not in uids
    ]


def save_draft(db: Session, contract: Contract, document: Document, user: User, body,
               fill_only: bool = False) -> dict:
    from app.services import pdf_engine

    assert_not_completed(contract)
    draft = _require_draft(document)
    _assert_owner(draft, user, body.client_session_id)
    if fill_only:
        assert_fill_only_ops(body.ops)
    ops = [op.model_dump(mode="json") for op in body.ops]
    markup = body.markup
    digest = payload_hash(ops, markup)
    if body.revision == draft.revision - 1 and digest == draft.payload_hash:
        # A retry of the save that already landed (the response was lost).
        return {
            "revision": draft.revision,
            "saved_at": _aware(draft.updated_at),
            "lease_expires_at": _aware(draft.lease_expires_at),
            "warnings": [],
        }
    _assert_revision(draft, body.revision)
    _assert_base_current(draft, document)
    base = db.get(DocumentVersion, draft.base_version_id)
    engine_ops = [pdf_engine.from_model(op) for op in body.ops]
    assert_page_deletions_allowed(db, contract, base, user, engine_ops)
    problems = markup_page_problems(markup, base, engine_ops)
    warnings: list[dict] = []
    if body.ops:
        try:
            engine_problems, warnings = pdf_engine.validate_ops(
                storage.get_bytes(base.file_path),
                page_refs(base),
                engine_ops,
            )
        except pdf_engine.EngineError as exc:
            raise from_engine(exc) from exc
        problems = engine_problems + problems
    if problems:
        raise DocumentError(422, "invalid_ops", "Some edits cannot be applied", {"ops": problems[:200]})
    saved_at = now_utc()
    result = db.execute(
        update(DocumentDraft)
        .where(DocumentDraft.id == draft.id, DocumentDraft.revision == body.revision)
        .values(
            revision=body.revision + 1,
            ops=ops,
            markup=markup,
            payload_hash=digest,
            lease_expires_at=lease_until(),
            updated_at=saved_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise DocumentError(409, "revision_conflict", "The draft has changed since you loaded it", {})
    db.commit()
    db.refresh(draft)
    return {
        "revision": draft.revision,
        "saved_at": saved_at,
        "lease_expires_at": _aware(draft.lease_expires_at),
        "warnings": warnings,
    }


def renew_lease(db: Session, contract: Contract, document: Document, user: User, body) -> datetime:
    draft = _require_draft(document)
    _assert_owner(draft, user, body.client_session_id)
    _assert_revision(draft, body.revision)
    draft.lease_expires_at = lease_until()
    db.commit()
    return _aware(draft.lease_expires_at)


def discard_draft(db: Session, contract: Contract, document: Document, user: User, revision: int, is_admin: bool) -> None:
    draft = _require_draft(document)
    if not is_admin:
        _assert_owner(draft, user, None)
    _assert_revision(draft, revision)
    audit.log(
        db,
        audit.DOCUMENT_DRAFT_DISCARDED,
        contract_id=contract.id,
        actor=user,
        details={"revision": draft.revision, "ops": len(draft.ops or [])},
    )
    db.delete(draft)
    db.commit()


# -- commit --------------------------------------------------------------------------------


def op_summary(ops: list[dict]) -> dict:
    by_kind: dict[str, int] = {}
    by_layer: dict[str, int] = {}
    for op in ops:
        by_kind[op["kind"]] = by_kind.get(op["kind"], 0) + 1
        layer = op_layer(op["kind"])
        by_layer[layer] = by_layer.get(layer, 0) + 1
    return {"total": len(ops), "by_kind": dict(sorted(by_kind.items())), "by_layer": dict(sorted(by_layer.items()))}


def markup_content_signature(elements: list[dict] | None) -> list[str]:
    """Canonical form of the review-relevant (markup-content) elements."""
    return sorted(
        json.dumps(element, sort_keys=True, separators=(",", ":"))
        for element in (elements or [])
        if element.get("kind") in MARKUP_CONTENT_KINDS
    )


def markup_content_changed(before: list[dict] | None, after: list[dict] | None) -> bool:
    return markup_content_signature(before) != markup_content_signature(after)


def version_out(db: Session, version: DocumentVersion) -> dict:
    return {
        "version_no": version.version_no,
        "kind": version.kind,
        "file_hash": version.file_hash,
        "page_count": version.page_count,
        "content_changed": bool(version.content_changed),
        "review_reset": bool(version.review_reset),
        "created_by_name": owner_name(db, version.created_by) if version.created_by else None,
        "created_at": _aware(version.created_at),
        "op_summary": version.op_summary or {},
    }


def _commit_replay(db: Session, version: DocumentVersion) -> dict:
    return {
        "version": version_out(db, version),
        "content_changed": bool(version.content_changed),
        "review_reset": bool(version.review_reset),
        "reparse_started": False,
        "remapped": {"signatures_kept": 0, "signatures_removed": 0},
        "warnings": [{"code": "replayed"}],
    }


def commit_draft(db: Session, contract: Contract, document: Document, user: User, body, request=None,
                 fill_only: bool = False) -> dict:
    from app.core.security import client_ip
    from app.services import document_review, pdf_engine

    # (2) idempotency first: the draft is gone after a successful commit.
    replay = db.scalar(
        select(DocumentVersion).where(DocumentVersion.commit_request_id == body.commit_request_id)
    )
    if replay is not None:
        if replay.document_id != document.id:
            raise DocumentError(409, "commit_request_id_reused", "This commit id was used for another document")
        return _commit_replay(db, replay)

    # (1) validation
    assert_not_completed(contract)
    assert_not_busy(contract)
    draft = _require_draft(document)
    assert_fill_access(draft, user, fill_only)
    _assert_owner(draft, user, None)
    _assert_revision(draft, body.revision)
    _assert_base_current(draft, document)
    base = db.get(DocumentVersion, draft.base_version_id)

    stored_ops = list(draft.ops or [])
    try:
        ops = pdf_engine.from_stored(stored_ops)
    except Exception as exc:  # noqa: BLE001 - stored by an older schema
        raise DocumentError(422, "invalid_ops", "The draft holds edits this server cannot read", {}) from exc
    if fill_only:
        assert_fill_only_ops(ops)
        if body.discard_signatures:
            raise DocumentError(403, "fill_only", "Filling a form never removes signatures", {})
    assert_page_deletions_allowed(db, contract, base, user, ops)
    markup_changed = draft.markup is not None and markup_content_changed(document.markup, draft.markup)
    if draft.markup is not None:
        problems = markup_page_problems(draft.markup, base, ops)
        if problems:
            raise DocumentError(422, "invalid_ops", "Some edits cannot be applied", {"ops": problems[:200]})
    # B2: content ops change what is reviewed and signed; structure ops
    # (page.rotate, link.*) make a new version too, but keep review and signatures.
    content_ops = any(op_layer(op.kind) == "content" for op in ops)
    engine_ops = bool(ops)
    content_changed = content_ops or markup_changed

    signatures = list(db.scalars(select(Signature).where(Signature.contract_id == contract.id)))
    if content_ops and signatures and not body.discard_signatures:
        raise DocumentError(
            409, "signatures_present",
            "This document has signatures; saving a content edit removes them",
            {"count": len(signatures)},
        )

    # (3) engine + file, outside the transaction
    result = None
    new_key = None
    if engine_ops:
        try:
            result = pdf_engine.apply_ops(storage.get_bytes(base.file_path), page_refs(base), ops)
        except pdf_engine.EngineError as exc:
            raise from_engine(exc) from exc
        next_no = (db.scalar(select(func.max(DocumentVersion.version_no)).where(
            DocumentVersion.document_id == document.id)) or 0) + 1
        new_key = version_key(contract.id, next_no, result.sha256)
        storage.put_bytes(new_key, result.pdf_bytes)

    removed_keys: list[str] = []
    review_reset = False
    new_version = None
    kept_remapped = 0
    markup_dropped = 0
    ip = client_ip(request) if request is not None else None
    try:
        if engine_ops:
            new_version = DocumentVersion(
                document_id=document.id,
                version_no=next_no,
                parent_version_id=base.id,
                kind="edit",
                file_path=new_key,
                file_hash=result.sha256,
                file_bytes=len(result.pdf_bytes),
                page_count=result.page_count,
                ops_applied=stored_ops,
                op_summary=op_summary(stored_ops),
                content_changed=content_ops,
                review_reset=False,
                engine="pymupdf",
                engine_ref=pdf_engine.engine_ref(),
                commit_request_id=body.commit_request_id,
                created_by=user.id,
            )
            base_by_uid = {page.page_uid: page for page in base.pages}
            new_version.pages = [
                DocumentPage(
                    position=info.position,
                    page_uid=uid,
                    origin_page=base_by_uid[uid].origin_page if uid in base_by_uid else None,
                    rotation=info.rotation,
                    width_pt=info.width_pt,
                    height_pt=info.height_pt,
                    has_text=info.has_text,
                    scanned=info.scanned,
                )
                for info, uid in zip(result.pages, result.page_uids)
            ]
            db.add(new_version)
            db.flush()
            swapped = db.execute(
                update(Document)
                .where(Document.id == document.id, Document.current_version_id == base.id)
                .values(current_version_id=new_version.id, updated_at=now_utc())
                .execution_options(synchronize_session=False)
            )
            if swapped.rowcount != 1:
                raise DocumentError(409, "base_outdated", "A newer version exists; reload the document", {})

        new_markup = draft.markup
        if result is not None:
            # Markup on a page this commit deleted goes with the page.
            source_markup = draft.markup if draft.markup is not None else list(document.markup or [])
            live = set(result.page_uids)
            kept = [element for element in source_markup if element.get("page_uid") in live]
            markup_dropped = len(source_markup) - len(kept)
            if markup_dropped:
                new_markup = kept
        if new_markup is not None:
            document.markup = new_markup
            document.markup_revision = (document.markup_revision or 0) + 1
            document.markup_updated_by = user.id
        db.delete(draft)

        if new_version is not None and not content_ops:
            # Structure commit: signatures stay; a rotated page carries its
            # signature boxes along (they are in the displayed space).
            kept_remapped = remap_signatures(db, signatures, base, new_version)

        if content_ops:
            for signature in signatures:
                removed_keys.append(signature.signature_image_path)
                audit.log(
                    db,
                    audit.SIGNATURE_REMOVED,
                    contract_id=contract.id,
                    actor=user,
                    details={
                        "signature_id": str(signature.id),
                        "reason": "superseded_by_version",
                        "version_no": new_version.version_no,
                    },
                    ip_address=ip,
                )
                db.delete(signature)
            if signatures:
                audit.log(
                    db,
                    audit.SIGNATURES_SUPERSEDED,
                    contract_id=contract.id,
                    actor=user,
                    details={"count": len(signatures), "version_no": new_version.version_no},
                    ip_address=ip,
                )
            review_reset = document_review.on_content_commit(
                db, contract, user,
                version_no=new_version.version_no,
                changed_regions=result.changed_regions,
                source=_source_for(new_version),
            )
            new_version.review_reset = review_reset
        elif markup_changed:
            review_reset = document_review.on_markup_content_change(db, contract, user)

        if new_version is not None:
            base_origins = {page.page_uid: page.origin_page for page in base.pages}
            audit.log(
                db,
                audit.DOCUMENT_VERSION_COMMITTED,
                contract_id=contract.id,
                actor=user,
                details={
                    "version_no": new_version.version_no,
                    "parent_version_no": base.version_no,
                    "file_hash": new_version.file_hash,
                    "by_kind": new_version.op_summary.get("by_kind", {}),
                    "content_changed": content_ops,
                    "pages_before": base.page_count,
                    "pages_after": new_version.page_count,
                    "changed_regions": [
                        {**region, "origin_page": base_origins.get(region["page_uid"])}
                        for region in result.changed_regions[:200]
                    ],
                    # Where content was REALLY removed (no text, a box): lets
                    # anyone look at the same spot of the previous version.
                    "redactions": [
                        {**entry, "origin_page": base_origins.get(entry["page_uid"])}
                        for entry in result.redactions[:200]
                    ],
                    "images_removed": result.images_removed,
                    "pages_removed": sorted(
                        origin for uid, origin in base_origins.items()
                        if uid not in result.page_uids and origin is not None
                    ),
                    "pages_inserted": sum(1 for uid in result.page_uids if uid not in base_origins),
                    "markup_removed_with_page": markup_dropped,
                    # B3: counts of the field ops (never names or values).
                    "fields": result.fields,
                    "review_reset": review_reset,
                },
                ip_address=ip,
            )
        if new_markup is not None:
            audit.log(
                db,
                audit.DOCUMENT_MARKUP_SAVED,
                contract_id=contract.id,
                actor=user,
                details={**markup_stats(document.markup), "revision": document.markup_revision,
                         "via": "commit", "review_reset": bool(markup_changed and review_reset)},
                ip_address=ip,
            )
        db.commit()
    except BaseException:
        db.rollback()
        if new_key is not None:
            storage.delete(new_key)
        raise

    for key in removed_keys:
        try:
            storage.delete(key)
        except Exception:  # noqa: BLE001 - rows are gone; an orphan file is logged only
            logger.warning("Superseded signature image %s could not be deleted", key, exc_info=True)

    reparse_started = False
    if content_ops:
        from app.api import contracts as contracts_api

        contracts_api.process_contract_task.delay(str(contract.id))
        reparse_started = True

    db.expire_all()
    current = db.get(DocumentVersion, db.get(Document, document.id).current_version_id)
    warnings = list(result.warnings) if result is not None else []
    if result is not None:
        warnings.extend({"code": "font_substituted", **entry} for entry in result.substituted_from)
    if markup_dropped:
        warnings.append({"code": "markup_removed_with_page", "count": markup_dropped})
    if kept_remapped:
        warnings.append({"code": "signatures_rotated_with_page", "count": kept_remapped})
    return {
        "version": version_out(db, current),
        "content_changed": content_changed,
        "review_reset": review_reset,
        "reparse_started": reparse_started,
        "remapped": {
            "signatures_kept": 0 if content_ops else len(signatures),
            "signatures_removed": len(removed_keys),
        },
        "warnings": warnings,
    }


def _source_for(version: DocumentVersion) -> SigningSource:
    pages = list(version.pages)
    original = version.document.original_version if version.document is not None else None
    original_pages = list(original.pages) if original is not None else []
    mapping: dict[int, int | None] = {page.position: None for page in original_pages}
    mapping.update({p.origin_page: p.position for p in pages if p.origin_page is not None})
    return SigningSource(
        bytes_key=version.file_path,
        file_hash=version.file_hash,
        page_count=version.page_count,
        version_no=version.version_no,
        version_id=version.id,
        origin_to_position=mapping,
        uid_by_position={p.position: p.page_uid for p in pages},
        rotation_by_position={p.position: int(p.rotation or 0) for p in pages},
        origin_rotation={page.position: int(page.rotation or 0) for page in original_pages},
        size_by_position={p.position: (float(p.width_pt), float(p.height_pt)) for p in pages},
    )


def _origin_of(version: DocumentVersion, page_uid: str) -> int | None:
    for page in version.pages:
        if page.page_uid == page_uid:
            return page.origin_page
    return None


def remap_signatures(db: Session, signatures: list, base: DocumentVersion, new_version: DocumentVersion) -> int:
    """After a STRUCTURE commit: every kept signature row follows its page by
    `page_uid` (position recomputed) and, when that page's rotation changed,
    its box is moved to the same spot of the page as now displayed. Legacy
    rows (no `page_uid`) are matched by position. Returns the rows moved."""
    from app.services.geometry import BBox, remap_displayed_box

    base_by_uid = {page.page_uid: page for page in base.pages}
    base_by_position = {page.position: page for page in base.pages}
    new_by_uid = {page.page_uid: page for page in new_version.pages}
    moved = 0
    for signature in signatures:
        before = base_by_uid.get(signature.page_uid) if signature.page_uid else None
        if before is None:
            before = base_by_position.get(int(signature.page_number))
        if before is None:
            continue
        after = new_by_uid.get(before.page_uid)
        if after is None:  # pragma: no cover - structure ops never delete a page
            continue
        signature.page_uid = after.page_uid
        signature.page_number = after.position
        if int(before.rotation or 0) != int(after.rotation or 0) and signature.bbox:
            box = remap_displayed_box(
                BBox.from_dict(signature.bbox), float(before.width_pt), float(before.height_pt),
                int(before.rotation or 0), int(after.rotation or 0),
            )
            signature.bbox = box.to_dict()
            moved += 1
    return moved


def slot_origin_pages(db: Session, contract: Contract) -> set[int]:
    """Template pages that carry a signature or date slot."""
    from app.models.template import Template
    from app.services.comparison_engine import _index_schema, _is_slot_field, _paired_date_ids

    template = db.get(Template, contract.template_id)
    if template is None:
        return set()
    index = _index_schema(template.field_schema or {})
    paired = _paired_date_ids(index)
    return {
        int(field["page"])
        for field in index.values()
        if field.get("page") is not None and _is_slot_field(field, paired)
    }


def assert_page_deletions_allowed(db: Session, contract: Contract, base: DocumentVersion, user: User, ops) -> None:
    """§11-7: deleting a page that holds a signature/date slot is for a
    reviewer or an admin only (it re-opens review with a CRITICAL
    `page_removed_with_slots`, which an override may accept -- never FMCSA)."""
    from app.core.security import ROLE_ADMIN, ROLE_REVIEWER

    deleting = {op.page_uid for op in ops if getattr(op, "kind", None) == "page.delete"}
    if not deleting or user.role in (ROLE_ADMIN, ROLE_REVIEWER):
        return
    slot_pages = slot_origin_pages(db, contract)
    hit = sorted(
        page.origin_page for page in base.pages
        if page.page_uid in deleting and page.origin_page is not None and page.origin_page in slot_pages
    )
    if hit:
        raise DocumentError(
            403, "page_has_slots",
            "A page with signature or date slots can only be deleted by a reviewer or an admin",
            {"pages": hit},
        )


def markup_stats(elements: list[dict] | None) -> dict:
    """Counts only (like `_annotation_stats`): never text, never ids."""
    by_kind: dict[str, int] = {}
    for element in elements or []:
        by_kind[element.get("kind", "?")] = by_kind.get(element.get("kind", "?"), 0) + 1
    return {"total": len(elements or []), "by_kind": dict(sorted(by_kind.items()))}


# -- janitor -------------------------------------------------------------------------------


def sweep_orphan_versions(db: Session, contract_ids=None) -> list[str]:
    """Delete version files no `document_versions` row points at.

    Only keys shaped exactly like `version_key` are considered, and only for
    local storage (the only backend that can list a folder here). Returns the
    deleted keys. Safe to run any time: a commit's file is referenced by its
    row before the transaction commits, and a failed commit deletes its own.
    """
    import os

    root = getattr(storage, "root", None)
    if root is None:
        return []
    referenced = set(db.scalars(select(DocumentVersion.file_path)))
    ids = contract_ids
    if ids is None:
        ids = [str(cid) for cid in db.scalars(select(Contract.id))]
    deleted: list[str] = []
    for cid in ids:
        folder = os.path.join(str(root), PREFIX_CONTRACTS, str(cid))
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            key = f"{PREFIX_CONTRACTS}/{cid}/{name}"
            if _VERSION_KEY_RE.match(key) and key not in referenced:
                storage.delete(key)
                deleted.append(key)
    return deleted
