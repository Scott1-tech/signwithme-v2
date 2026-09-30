import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk
from app.models.types import UUIDType


class UserSignature(Base):
    """A reusable signature that belongs to one user.

    Distinct from ``Signature``, which is a signing *event* keyed to a contract.
    This is the user's private library: a stored PNG they can pick when signing
    any contract, instead of drawing a fresh one each time. Rows are strictly
    per-user; every read and delete is scoped to ``user_id == current user`` to
    prevent one user reading or deleting another's signature (IDOR).
    """

    __tablename__ = "user_signatures"

    id: Mapped[uuid.UUID] = uuid_pk()
    # The owner. CASCADE: deleting a user removes their private signature library.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    label: Mapped[str | None] = mapped_column(String(255))
    # Storage key for the PNG bytes, served only through the authenticated API.
    image_path: Mapped[str] = mapped_column(String(500), nullable=False)
    signature_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # drawn | uploaded | typed (editor v2; the typed text itself is never stored).
    # NULL for rows saved before the column existed.
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)

    created_at: Mapped[datetime] = created_at_col()
