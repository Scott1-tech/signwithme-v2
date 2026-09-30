"""Purge gallery images their owner deleted long enough ago (B2 follow-up).

`DELETE /api/assets/{id}` is a soft delete: the bytes go, the row stays (audit
trail, `source_asset_id` of copies). Nothing ever removed those rows, so this
does -- for USER-scope rows whose `deleted_at` is at least
`settings.deleted_asset_retention_days` old.

Safe by construction:

* Only `scope == 'user'` rows with a `deleted_at`. A live gallery image, and
  every CONTRACT-scope copy (the image placed on a contract, which the
  annotation/final pipeline reads), is never selected. A copy's
  `source_asset_id` is provenance only (no FK, never resolved) and is left as
  it is.
* The file removed is the row's own `storage_key`, and only when it is exactly
  `user_asset_key(owner, id)` -- a row pointing anywhere else (e.g. into a
  contract folder) is skipped and reported, never deleted.
* Idempotent: a second run finds nothing; a missing file is not an error
  (the soft delete already removed it in the normal case).
* `dry_run=True` changes nothing and reports what would go.

One `assets_purged` audit entry per real run that removed anything: ids and
counts, never an image, label or hash.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import StorageError
from app.models.asset import Asset
from app.services import audit
from app.services.storage import storage as default_storage, user_asset_key

logger = logging.getLogger(__name__)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def purge_deleted_assets(
    db: Session,
    *,
    older_than_days: int | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    storage=None,
) -> dict:
    days = settings.deleted_asset_retention_days if older_than_days is None else int(older_than_days)
    if days < 0:
        raise ValueError("older_than_days must be >= 0")
    storage = storage or default_storage
    now = _aware(now or datetime.now(timezone.utc))
    cutoff = now - timedelta(days=days)

    candidates = db.scalars(
        select(Asset)
        .where(Asset.scope == "user", Asset.deleted_at.is_not(None))
        .order_by(Asset.deleted_at)
    ).all()
    # Compared in Python: SQLite stores the timestamp as text, so a SQL
    # comparison would depend on its exact formatting.
    due = [row for row in candidates if _aware(row.deleted_at) <= cutoff]

    purged: list[str] = []
    skipped: list[dict] = []
    for row in due:
        try:
            expected = user_asset_key(row.owner_user_id, row.id.hex)
        except StorageError:
            expected = None
        if row.owner_user_id is None or row.storage_key != expected:
            skipped.append({"asset_id": row.id.hex, "reason": "unexpected_storage_key"})
            continue
        purged.append(row.id.hex)
        if dry_run:
            continue
        try:
            storage.delete(row.storage_key)  # missing_ok: normally already gone
        except Exception:  # noqa: BLE001 - the row still goes; a stray file is harmless
            logger.warning("Could not delete the bytes of asset %s", row.id.hex)
        db.delete(row)

    if purged and not dry_run:
        audit.log(
            db,
            audit.ASSETS_PURGED,
            details={"count": len(purged), "asset_ids": purged, "older_than_days": days},
        )
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return {
        "dry_run": dry_run,
        "older_than_days": days,
        "cutoff": cutoff.isoformat(),
        "soft_deleted_user_assets": len(candidates),
        "due": len(due),
        "purged" if not dry_run else "would_purge": len(purged),
        "asset_ids": purged,
        "skipped": skipped,
    }
