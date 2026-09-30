"""PDF editor v2: document, versions, pages, draft (EDITOR-V2-PLAN §2).

`contracts.file_path` stays the IMMUTABLE original upload. The document that
is reviewed, compared and signed is `documents.current_version_id`; read it
through `app.services.documents.signing_source(contract)` -- never through
`contract.file_path` directly. A contract without a `documents` row is a
legacy contract and behaves exactly as before (byte for byte).

The live SQLite database runs without `PRAGMA foreign_keys`, so `ON DELETE
CASCADE` never fires there: every parent -> child relationship carries
`cascade="all, delete-orphan"` and the ORM does the deleting (the admin
contract deletion in `api/contract_admin.py` relies on it).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk
from app.models.types import JSONType, UTCDateTime, UUIDType

VERSION_KINDS = ("original", "edit", "submission")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = uuid_pk()
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    original_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType,
        ForeignKey("document_versions.id", use_alter=True, name="fk_documents_original_version"),
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType,
        ForeignKey("document_versions.id", use_alter=True, name="fk_documents_current_version"),
    )
    # Server-side markup (legacy annotation wire with `page_uid`); never baked
    # into a version -- drawn at export/final time.
    markup: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    markup_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    markup_updated_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    contract: Mapped["Contract"] = relationship(back_populates="document")  # noqa: F821
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        foreign_keys="DocumentVersion.document_id",
        order_by="DocumentVersion.version_no",
    )
    current_version: Mapped["DocumentVersion | None"] = relationship(
        foreign_keys=[current_version_id], viewonly=True
    )
    original_version: Mapped["DocumentVersion | None"] = relationship(
        foreign_keys=[original_version_id], viewonly=True
    )
    draft: Mapped["DocumentDraft | None"] = relationship(
        back_populates="document", cascade="all, delete-orphan", uselist=False
    )
    exports: Mapped[list["ExportJob"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    # F3: share-for-fill links (models/share.py); their submissions go with them.
    share_links: Mapped[list["ShareLink"]] = relationship(  # noqa: F821
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_no", name="uq_document_versions_no"),
        Index("idx_document_versions_doc_no", "document_id", "version_no"),
        CheckConstraint("kind IN ('original','edit','submission')", name="ck_document_versions_kind"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # v1 = contracts.file_path; later: contracts/<cid>/version-<no>-<sha8>.pdf
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # The ops that made this version. Carries user text: NEVER copied to audit.
    ops_applied: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    # Counts only (by kind / layer) -- safe to show and to audit.
    op_summary: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    content_changed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_reset: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    engine: Mapped[str | None] = mapped_column(String(32))
    engine_ref: Mapped[str | None] = mapped_column(String(64))
    commit_request_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType)
    created_at: Mapped[datetime] = created_at_col()

    document: Mapped[Document] = relationship(back_populates="versions", foreign_keys=[document_id])
    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="DocumentPage.position"
    )


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("version_id", "position", name="uq_document_pages_position"),
        UniqueConstraint("version_id", "page_uid", name="uq_document_pages_uid"),
        Index("idx_document_pages_uid", "page_uid"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based
    page_uid: Mapped[str] = mapped_column(String(24), nullable=False)
    # Page N of the original upload (= template page N). NULL for an inserted page.
    origin_page: Mapped[int | None] = mapped_column(Integer)
    rotation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    width_pt: Mapped[float] = mapped_column(Float, nullable=False)
    height_pt: Mapped[float] = mapped_column(Float, nullable=False)
    has_text: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    scanned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    version: Mapped[DocumentVersion] = relationship(back_populates="pages")


class DocumentDraft(Base):
    __tablename__ = "document_drafts"

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    base_version_id: Mapped[uuid.UUID] = mapped_column(UUIDType, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ops: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    markup: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUIDType, nullable=False)
    client_session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    document: Mapped[Document] = relationship(back_populates="draft")


EXPORT_STATUSES = ("queued", "running", "done", "failed", "cancelled")


class ExportJob(Base):
    """One "Download PDF" of a version (EDITOR-V2-PLAN §2 `export_jobs`, B2).

    Reused while `(version_id, options_hash)` matches and the file is there;
    `options_hash` covers the options AND the markup revision, so a markup
    change makes a new export. Result bytes: `contracts/<cid>/export-<id>.pdf`
    (flat, taken by the admin deletion's `delete_prefix`)."""

    __tablename__ = "export_jobs"
    __table_args__ = (
        Index("idx_export_jobs_reuse", "document_id", "version_id", "options_hash"),
        CheckConstraint(
            "status IN ('queued','running','done','failed','cancelled')", name="ck_export_jobs_status"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[uuid.UUID] = mapped_column(UUIDType, nullable=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType)
    options: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    options_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    result_key: Mapped[str | None] = mapped_column(String(500))
    result_sha256: Mapped[str | None] = mapped_column(String(64))
    result_bytes: Mapped[int | None] = mapped_column(Integer)
    # A closed error code, never document text.
    error: Mapped[str | None] = mapped_column(String(64))
    warnings: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(timezone=True))

    document: Mapped[Document] = relationship(back_populates="exports")


EDITOR_V2_TABLES = ("documents", "document_versions", "document_pages", "document_drafts")
EDITOR_V2_B2_TABLES = ("assets", "export_jobs")
