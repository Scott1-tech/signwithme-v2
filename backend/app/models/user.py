import uuid
from datetime import datetime

from sqlalchemy import Boolean, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    # Auth: email + password. `users` in the original schema had no password column;
    # storing the hash here keeps JWT issuance self-contained (no external IdP).
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="contractor", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # True once someone other than the account holder has vouched for the
    # address: the seed script, or an admin. Self-registration proves nothing
    # about who owns an e-mail, so access granted BY e-mail (a contract's
    # `contractor_email`) requires this flag.
    email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=false()
    )
    created_at: Mapped[datetime] = created_at_col()
