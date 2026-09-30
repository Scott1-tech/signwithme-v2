"""Purge of soft-deleted gallery rows (services.asset_purge,
scripts/purge_deleted_assets.py).

Only USER-scope rows deleted at least N days ago go (row + leftover bytes);
live gallery images, recently deleted ones and every CONTRACT-scope copy stay,
and the placed copy still draws in the final. Idempotent; --dry-run changes
nothing.
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.models.asset import Asset
from app.services.asset_purge import purge_deleted_assets
from tests.conftest import auth
from tests.test_api_annotations import _ready, signature_data_url  # noqa: F401 - fixture
from tests.test_api_assets import _add

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def _set_deleted(api, asset_id: str, when: datetime) -> None:
    db = api.db_factory()
    try:
        db.get(Asset, UUID(hex=asset_id)).deleted_at = when
        db.commit()
    finally:
        db.close()


def _row(api, asset_id: str):
    db = api.db_factory()
    try:
        return db.get(Asset, UUID(hex=asset_id))
    finally:
        db.close()


def _purge(api, **kwargs):
    from app.services import storage as storage_module

    db = api.db_factory()
    try:
        return purge_deleted_assets(db, now=NOW, storage=storage_module.storage, **kwargs)
    finally:
        db.close()


def _audit(api, action):
    from app.models.audit import AuditLog

    db = api.db_factory()
    try:
        return [row.details for row in db.scalars(select(AuditLog).where(AuditLog.action == action))]
    finally:
        db.close()


@pytest.fixture
def gallery(api, contractor_token, uploaded_contract, signature_data_url):
    """old (deleted 45 d ago, copied onto the contract first), recent (deleted
    5 d ago), live (never deleted), and the contract copy of `old`."""
    _ready(api, contractor_token, uploaded_contract, signature_data_url)
    old = _add(api, contractor_token, kind="stamp").json()["asset_id"]
    recent = _add(api, contractor_token).json()["asset_id"]
    live = _add(api, contractor_token).json()["asset_id"]
    copy = api.post(f"/api/contracts/{uploaded_contract}/annotation-assets", headers=auth(contractor_token),
                    json={"from_asset_id": old})
    assert copy.status_code == 201, copy.text
    for asset_id in (old, recent):
        assert api.delete(f"/api/assets/{asset_id}", headers=auth(contractor_token)).status_code == 204
    _set_deleted(api, old, NOW - timedelta(days=45))
    _set_deleted(api, recent, NOW - timedelta(days=5))
    return {"old": old, "recent": recent, "live": live, "copy": copy.json()["asset_id"],
            "contract": uploaded_contract}


def test_dry_run_reports_and_changes_nothing(api, gallery):
    objects_before = dict(api.objects)
    report = _purge(api, dry_run=True)
    assert report["dry_run"] is True and report["would_purge"] == 1
    assert report["asset_ids"] == [gallery["old"]] and report["due"] == 1
    assert report["soft_deleted_user_assets"] == 2 and report["older_than_days"] == 30
    assert _row(api, gallery["old"]) is not None
    assert api.objects == objects_before
    assert _audit(api, "assets_purged") == []


def test_only_old_soft_deleted_user_rows_are_purged(api, contractor_token, gallery):
    # Leftover bytes (e.g. a delete that failed at the storage layer) go too.
    old_key = _row(api, gallery["old"]).storage_key
    api.objects[old_key] = b"leftover"
    copy_row = _row(api, gallery["copy"])
    copy_key, copy_bytes = copy_row.storage_key, api.objects[copy_row.storage_key]

    report = _purge(api)
    assert report["purged"] == 1 and report["asset_ids"] == [gallery["old"]] and report["skipped"] == []
    assert _row(api, gallery["old"]) is None and old_key not in api.objects
    assert _row(api, gallery["recent"]) is not None  # 5 days < 30
    assert _row(api, gallery["live"]) is not None
    assert api.get("/api/assets", headers=auth(contractor_token)).json()[0]["asset_id"] == gallery["live"]
    # The contract copy: row, bytes and provenance untouched.
    copy_after = _row(api, gallery["copy"])
    assert copy_after is not None and copy_after.scope == "contract" and copy_after.deleted_at is None
    assert copy_after.source_asset_id == UUID(hex=gallery["old"])
    assert api.objects[copy_key] == copy_bytes
    # Audit: ids and counts only.
    entries = _audit(api, "assets_purged")
    assert entries == [{"count": 1, "asset_ids": [gallery["old"]], "older_than_days": 30}]


def test_purge_is_idempotent(api, gallery):
    assert _purge(api)["purged"] == 1
    again = _purge(api)
    assert again["purged"] == 0 and again["asset_ids"] == []
    assert len(_audit(api, "assets_purged")) == 1, "a run that removes nothing writes no audit entry"


def test_the_days_setting_and_argument(api, gallery, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "deleted_asset_retention_days", 60)
    assert _purge(api, dry_run=True)["would_purge"] == 0  # 45 d < 60
    assert set(_purge(api, older_than_days=3, dry_run=True)["asset_ids"]) == {gallery["old"], gallery["recent"]}
    with pytest.raises(ValueError):
        _purge(api, older_than_days=-1)


def test_the_placed_copy_still_draws_after_the_purge(api, contractor_token, gallery):
    _purge(api)
    image = api.get(f"/api/contracts/{gallery['contract']}/annotation-assets/{gallery['copy']}",
                    headers=auth(contractor_token))
    assert image.status_code == 200
    final = api.post(f"/api/contracts/{gallery['contract']}/finalize", headers=auth(contractor_token), json={
        "contract_date": "2026-09-30",
        "annotations": [{"id": "i", "kind": "image", "page": 1, "asset_id": gallery["copy"],
                         "x_pt": 100, "y_pt": 100, "w_pt": 60, "h_pt": 40}],
    })
    assert final.status_code == 200, final.text


def test_a_row_whose_key_is_not_its_gallery_key_is_skipped(api, gallery):
    """Defence in depth: never delete a file outside `user-assets/<owner>/<id>.png`."""
    db = api.db_factory()
    try:
        row = db.get(Asset, UUID(hex=gallery["old"]))
        row.storage_key = f"contracts/{gallery['contract']}/asset-{uuid.uuid4().hex}.png"
        db.commit()
        foreign = row.storage_key
    finally:
        db.close()
    api.objects[foreign] = b"not mine"
    report = _purge(api)
    assert report["purged"] == 0
    assert report["skipped"] == [{"asset_id": gallery["old"], "reason": "unexpected_storage_key"}]
    assert api.objects[foreign] == b"not mine" and _row(api, gallery["old"]) is not None


def test_the_script_runs_the_service(api, gallery, monkeypatch, capsys):
    """scripts/purge_deleted_assets.py: --dry-run then a real run, against the
    test database (SessionLocal patched to it)."""
    import json

    import app.core.database as database

    monkeypatch.setattr(database, "SessionLocal", api.db_factory)
    monkeypatch.chdir(Path.cwd())
    path = Path(__file__).resolve().parents[1] / "scripts" / "purge_deleted_assets.py"
    spec = importlib.util.spec_from_file_location("purge_deleted_assets", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    # The script uses the real clock.
    real_now = datetime.now(timezone.utc)
    _set_deleted(api, gallery["old"], real_now - timedelta(days=45))
    _set_deleted(api, gallery["recent"], real_now - timedelta(days=5))

    assert script.main(["--dry-run", "--days", "40"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True and dry["would_purge"] == 1 and dry["older_than_days"] == 40
    assert _row(api, gallery["old"]) is not None
    assert script.main([]) == 0
    real = json.loads(capsys.readouterr().out)
    assert real["purged"] == 1 and _row(api, gallery["old"]) is None

    db = api.db_factory()
    try:
        assert db.scalar(select(func.count()).select_from(Asset).where(Asset.scope == "contract")) == 1
    finally:
        db.close()
