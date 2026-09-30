import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk
from app.models.types import JSONType, UUIDType

TEMPLATE_STATUSES = ("processing", "ready", "failed", "archived")


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(String(500))
    file_hash: Mapped[str | None] = mapped_column(String(64))
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Full detected structure: {"pages": [{"page_number": 1, "fields": [...]}]}
    field_schema: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    status: Mapped[str] = mapped_column(String(50), default="processing", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    contracts: Mapped[list["Contract"]] = relationship(back_populates="template")  # noqa: F821
