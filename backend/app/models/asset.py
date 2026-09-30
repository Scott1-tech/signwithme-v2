"""Image/stamp gallery (EDITOR-V2-PLAN §2 `assets`, B2).

Two scopes, exactly one owner each (CHECK):

* `user`     -- the caller's private gallery (`owner_user_id`), bytes under
  `user-assets/<user>/<asset>.png`. Served only to its owner (404 otherwise).
* `contract` -- a copy placed on one contract (`contract_id`), bytes at the
  contract's flat `contracts/<cid>/asset-<hex>.png` key -- the SAME key an
  `image`/`image2` annotation resolves, so a placed gallery image is an
  ordinary annotation asset. Placing from the gallery COPIES (source kept in
  `source_asset_id`): deleting from the gallery never breaks a placement.

Deletion is soft (`deleted_at`): the row stays for the audit trail, the user
bytes are removed; `scripts/purge_deleted_assets.py` removes such USER rows
after `deleted_asset_retention_days` (contract copies never). The live SQLite database runs without `PRAGMA
foreign_keys`: the contract -> assets relationship carries `cascade="all,
delete-orphan"` so the admin contract deletion removes the rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, uuid_pk
from app.models.types import UTCDateTime, UUIDType

ASSET_SCOPES = ("user", "contract")
ASSET_KINDS = ("image", "stamp")


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'user' AND owner_user_id IS NOT NULL AND contract_id IS NULL) OR "
            "(scope = 'contract' AND contract_id IS NOT NULL AND owner_user_id IS NULL)",
            name="ck_assets_scope_owner",
        ),
        CheckConstraint("kind IN ('image','stamp')", name="ck_assets_kind"),
        Index("idx_assets_owner", "owner_user_id"),
        Index("idx_assets_contract", "contract_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("contracts.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="image")
    label: Mapped[str | None] = mapped_column(String(120))
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    width_px: Mapped[int] = mapped_column(Integer, nullable=False)
    height_px: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUIDType)
    created_at: Mapped[datetime] = created_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(timezone=True))

    contract: Mapped["Contract | None"] = relationship(back_populates="assets")  # noqa: F821
