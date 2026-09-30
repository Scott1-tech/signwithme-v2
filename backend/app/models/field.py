import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, uuid_pk
from app.models.types import JSONType, UUIDType

FIELD_STATUSES = ("empty", "filled", "invalid", "verified")


class ContractField(Base):
    """Per-field extraction state for one contract. Authoritative over
    Contract.extracted_data, which is only a read cache of these rows."""

    __tablename__ = "contract_fields"
    __table_args__ = (
        # Without this, re-running the parser duplicates every field row.
        UniqueConstraint("contract_id", "template_field_id", name="uq_contract_field"),
        Index("idx_fields_contract", "contract_id"),
        Index("idx_fields_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False
    )
    template_field_id: Mapped[str] = mapped_column(String(100), nullable=False)

    field_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="empty", nullable=False)

    raw_value: Mapped[str | None] = mapped_column(Text)
    normalized_value: Mapped[str | None] = mapped_column(Text)
    confidence_score: Mapped[float | None] = mapped_column(Numeric(3, 2))

    is_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_valid: Mapped[bool | None] = mapped_column(Boolean)
    validation_error: Mapped[str | None] = mapped_column(Text)

    # Canonical bbox: {"x", "y", "width", "height"} in PDF points, top-left origin.
    bbox: Mapped[dict | None] = mapped_column(JSONType)
    page_number: Mapped[int | None] = mapped_column(Integer)

    # Type-specific payload: checkbox selections, table rows, signature detection.
    extra: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    created_at: Mapped[datetime] = created_at_col()

    contract: Mapped["Contract"] = relationship(back_populates="fields")  # noqa: F821
