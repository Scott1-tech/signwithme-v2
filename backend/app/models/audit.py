import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk
from app.models.types import INETType, JSONType, UTCDateTime, UUIDType


class AuditLog(Base):
    """Append-only legal record. actor_id is deliberately NOT a foreign key so
    log rows survive user deletion."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_contract", "contract_id"),
        Index("idx_audit_timestamp", "timestamp"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("contracts.id", ondelete="SET NULL")
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("templates.id", ondelete="SET NULL")
    )

    action: Mapped[str] = mapped_column(String(100), nullable=False)

    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType)
    actor_email: Mapped[str | None] = mapped_column(String(255))
    actor_role: Mapped[str | None] = mapped_column(String(50))

    details: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(INETType)
    user_agent: Mapped[str | None] = mapped_column(String(500))

    timestamp: Mapped[datetime] = mapped_column(
        UTCDateTime(timezone=True), server_default=func.now(), nullable=False
    )
