"""Share-for-fill links and their submissions (EDITOR-V2-PLAN §8, F3).

§11 Q4: LAN only, fill only. A link lets someone without an account fill the
named form fields of ONE pinned version; nothing they send changes the document
until an editor accepts the submission (-> a draft of `field.value` ops the
editor saves like any other edit).

* `share_links.token_hash` = sha256 of the token. The token itself is shown
  once, in the create response, and stored nowhere (not here, not in the
  audit log, not in a log line).
* `form_submissions.values_encrypted` = the submitted `{name: value}` JSON,
  encrypted with `app.core.pii` (a form carries SSNs, dates of birth...).

The live SQLite database runs without `PRAGMA foreign_keys`: the document ->
links -> submissions relationships carry `cascade="all, delete-orphan"`, so the
admin contract deletion removes the rows (ORM cascade from `Contract.document`).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, uuid_pk
from app.models.types import JSONType, UTCDateTime, UUIDType

SHARE_PURPOSES = ("fill",)
SUBMISSION_STATUSES = ("received", "accepted", "rejected")


class ShareLink(Base):
    __tablename__ = "share_links"
    __table_args__ = (
        CheckConstraint("purpose IN ('fill')", name="ck_share_links_purpose"),
        Index("idx_share_links_document", "document_id"),
        Index("idx_share_links_contract", "contract_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False
    )
    # The version the recipient fills -- pinned at creation.
    version_id: Mapped[uuid.UUID] = mapped_column(UUIDType, nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="fill")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Field NAMES the recipient may fill (never values).
    allowed_fields: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    recipient_label: Mapped[str | None] = mapped_column(String(120))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), nullable=False)
    max_submissions: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    submission_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType)
    created_at: Mapped[datetime] = created_at_col()

    document: Mapped["Document"] = relationship(back_populates="share_links")  # noqa: F821
    submissions: Mapped[list["FormSubmission"]] = relationship(
        back_populates="share_link", cascade="all, delete-orphan", order_by="FormSubmission.created_at"
    )


class FormSubmission(Base):
    __tablename__ = "form_submissions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('received','accepted','rejected')", name="ck_form_submissions_status"
        ),
        Index("idx_form_submissions_document", "document_id", "status"),
        Index("idx_form_submissions_link", "share_link_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    share_link_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("share_links.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUIDType, nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(UUIDType, nullable=False)
    # `pii.encrypt(json.dumps({name: value}))` -- never plaintext at rest.
    values_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    field_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submitter_name: Mapped[str | None] = mapped_column(String(120))
    submitter_email: Mapped[str | None] = mapped_column(String(254))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="received")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()

    share_link: Mapped[ShareLink] = relationship(back_populates="submissions")


EDITOR_V2_F3_TABLES = ("share_links", "form_submissions")
