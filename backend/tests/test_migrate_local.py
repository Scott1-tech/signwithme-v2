"""backend/scripts/migrate_local_editor_v2.py on a COPY of a pre-v2 database."""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_local_editor_v2.py"


def _script():
    spec = importlib.util.spec_from_file_location("migrate_local_editor_v2", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def old_db(tmp_path) -> Path:
    """A database with the schema as it was before v2, and some rows."""
    from sqlalchemy import create_engine

    from app.models import Base

    path = tmp_path / "local.db"
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}", future=True)
    old_tables = [
        t for name, t in Base.metadata.tables.items()
        if not name.startswith("document")
        and name not in ("assets", "export_jobs", "share_links", "form_submissions")
    ]
    Base.metadata.create_all(engine, tables=old_tables)
    engine.dispose()
    conn = sqlite3.connect(path)
    conn.execute('ALTER TABLE "signatures" DROP COLUMN "page_uid"')
    conn.execute('ALTER TABLE "user_signatures" DROP COLUMN "source"')
    conn.execute(
        "INSERT INTO users (id, email, full_name, password_hash, role, is_active, created_at) "
        "VALUES ('0123456789abcdef0123456789abcdef', 'a@example.com', 'A', 'x', 'admin', 1, CURRENT_TIMESTAMP)"
    )
    conn.commit()
    conn.close()
    return path


def _columns(path, table):
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    finally:
        conn.close()


def test_migration_is_idempotent_and_keeps_every_row(old_db):
    module = _script()
    assert "page_uid" not in _columns(old_db, "signatures")

    first = module.migrate(old_db, check_port=None, stamp="t1")
    assert first["ok"] is True
    assert first["integrity_before"] == first["integrity_after"] == "ok"
    assert first["counts_before"] == first["counts_after"]
    assert first["counts_before"]["users"] == 1
    assert "add column signatures.page_uid" in first["actions"]
    assert "add column user_signatures.source" in first["actions"]
    assert {f"create table {t}" for t in module.NEW_TABLES} <= set(first["actions"])
    assert Path(first["backup"]).is_file()
    assert "page_uid" in _columns(old_db, "signatures") and "source" in _columns(old_db, "user_signatures")

    second = module.migrate(old_db, check_port=None, stamp="t2")
    assert second["ok"] is True and second["actions"] == []
    assert second["counts_before"]["users"] == 1


def test_dry_run_changes_nothing(old_db):
    module = _script()
    report = module.migrate(old_db, dry_run=True, check_port=None, stamp="dry")
    assert set(report["pending_columns"]) == {"signatures.page_uid", "user_signatures.source"}
    assert set(report["pending_tables"]) == set(module.NEW_TABLES)
    assert "page_uid" not in _columns(old_db, "signatures")


def test_the_backup_is_a_consistent_copy(old_db):
    module = _script()
    report = module.migrate(old_db, check_port=None, stamp="bk")
    conn = sqlite3.connect(report["backup"])
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        # The backup is the PRE-migration state.
        assert "page_uid" not in {row[1] for row in conn.execute('PRAGMA table_info("signatures")')}
    finally:
        conn.close()


def test_refuses_while_the_port_answers(old_db):
    import socket

    module = _script()
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        with pytest.raises(SystemExit, match="stop the API server"):
            module.migrate(old_db, check_port=server.getsockname()[1], stamp="port")
    finally:
        server.close()
    assert "page_uid" not in _columns(old_db, "signatures")


def test_a_b1_database_gets_only_the_b2_tables(old_db):
    """The live database was migrated for B1 on 2026-09-29: running the script
    again adds `assets` and `export_jobs` and nothing else, rows unchanged."""
    module = _script()
    first = module.migrate(old_db, check_port=None, stamp="b1")
    conn = sqlite3.connect(old_db)
    try:
        conn.execute('DROP TABLE "form_submissions"')
        conn.execute('DROP TABLE "share_links"')
        conn.execute('DROP TABLE "export_jobs"')
        conn.execute('DROP TABLE "assets"')
        conn.commit()
    finally:
        conn.close()
    report = module.migrate(old_db, check_port=None, stamp="b2")
    assert first["ok"] and report["ok"] is True
    assert sorted(report["actions"]) == [
        "create table assets", "create table export_jobs", "create table form_submissions", "create table share_links"]
    assert report["counts_before"] == report["counts_after"]
    dry = module.migrate(old_db, dry_run=True, check_port=None, stamp="b2dry")
    assert dry["pending_tables"] == [] and dry["pending_columns"] == []


def test_a_b2_database_gets_only_the_share_tables(old_db):
    """The live database was migrated for B2 on 2026-09-29: running the script
    again (F3) adds `share_links` and `form_submissions` and nothing else;
    a third run has nothing to do."""
    module = _script()
    assert module.migrate(old_db, check_port=None, stamp="b2")["ok"]
    conn = sqlite3.connect(old_db)
    try:
        conn.execute('DROP TABLE "form_submissions"')
        conn.execute('DROP TABLE "share_links"')
        conn.commit()
    finally:
        conn.close()
    dry = module.migrate(old_db, dry_run=True, check_port=None, stamp="f3dry")
    assert dry["pending_tables"] == ["share_links", "form_submissions"] and dry["pending_columns"] == []
    report = module.migrate(old_db, check_port=None, stamp="f3")
    assert report["ok"] is True
    assert sorted(report["actions"]) == ["create table form_submissions", "create table share_links"]
    assert report["counts_before"] == report["counts_after"]
    again = module.migrate(old_db, check_port=None, stamp="f3again")
    assert again["ok"] is True and again["actions"] == []
    conn = sqlite3.connect(old_db)
    try:
        cols = {row[1] for row in conn.execute('PRAGMA table_info("share_links")')}
        assert {"token_hash", "allowed_fields", "version_id", "revoked_at"} <= cols
        assert "token" not in cols
    finally:
        conn.close()


def test_migrated_database_works_with_the_models(old_db):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.models import Document, FormSubmission, ShareLink, Signature

    _script().migrate(old_db, check_port=None, stamp="orm")
    engine = create_engine(f"sqlite+pysqlite:///{old_db.as_posix()}", future=True)
    session = sessionmaker(bind=engine, future=True)()
    try:
        assert list(session.scalars(select(Signature))) == []
        assert list(session.scalars(select(Document))) == []
        assert list(session.scalars(select(ShareLink))) == []
        assert list(session.scalars(select(FormSubmission))) == []
    finally:
        session.close()
        engine.dispose()
