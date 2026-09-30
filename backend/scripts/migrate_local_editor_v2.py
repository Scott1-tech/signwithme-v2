"""Local SQLite migration for the PDF editor v2 (EDITOR-V2-PLAN §2, alembic 0005 + 0006 + 0007).

B2 (2026-09-29) extends it with the `assets` and `export_jobs` tables; run it
again on a B1 database and only those two are created. F3 (2026-09-30) adds
`share_links` and `form_submissions` (alembic 0007) the same way: on a B2
database only those two are created.

The local deployment has no alembic (0001 is Postgres-only), and
`local_run.py`'s `create_all` creates missing TABLES but never adds a COLUMN to
an existing one -- so `signatures.page_uid` and `user_signatures.source` must be
added here before the new code starts, or every signature query fails.

Idempotent. Steps:
  1. refuse if the API still answers on the port (stop the server first);
  2. ONLINE backup (sqlite3 backup API) to <db>.bak-<stamp>-editorv2;
  3. PRAGMA integrity_check == ok;
  4. row counts of every existing table;
  5. ALTER TABLE ... ADD COLUMN for the two nullable columns (only if missing);
  6. create_all(tables=[documents, document_versions, document_pages,
     document_drafts]) -- only missing tables;
  7. integrity_check again, counts of the old tables unchanged, new tables and
     columns present. Any mismatch -> exit 2 (the backup is kept either way).

Usage (from backend/):
  .venv\\Scripts\\python.exe scripts\\migrate_local_editor_v2.py --db C:\\...\\local.db
  (--dry-run: backup + checks only, nothing changed)
"""
from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

NEW_COLUMNS = (
    ("signatures", "page_uid", "VARCHAR(24)"),
    ("user_signatures", "source", "VARCHAR(16)"),
)
# B1 (alembic 0005) + B2 (alembic 0006: `assets`, `export_jobs`) + F3 (alembic
# 0007: `share_links`, `form_submissions`). A database already migrated for an
# earlier stage gets only the later tables (every step is "if missing"). Order
# matters for create_all only through FKs, which it resolves itself.
NEW_TABLES = (
    "documents", "document_versions", "document_pages", "document_drafts",
    "assets", "export_jobs",
    "share_links", "form_submissions",
)


def server_answers(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def online_backup(db_path: Path, target: Path) -> None:
    source = sqlite3.connect(str(db_path))
    try:
        destination = sqlite3.connect(str(target))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def integrity(conn: sqlite3.Connection) -> str:
    return conn.execute("PRAGMA integrity_check").fetchone()[0]


def tables(conn: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def counts(conn: sqlite3.Connection, names: list[str]) -> dict[str, int]:
    return {name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def migrate(db_path: Path, *, dry_run: bool = False, backup_dir: Path | None = None,
            check_port: int | None = 8000, stamp: str | None = None) -> dict:
    if not db_path.is_file():
        raise SystemExit(f"database not found: {db_path}")
    if check_port and server_answers("127.0.0.1", check_port):
        raise SystemExit(
            f"something answers on 127.0.0.1:{check_port} -- stop the API server first "
            "(or pass --no-port-check if that is not this app)"
        )
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = (backup_dir or db_path.parent) / f"{db_path.name}.bak-{stamp}-editorv2"
    online_backup(db_path, backup)

    report: dict = {"db": str(db_path), "backup": str(backup), "dry_run": dry_run, "actions": []}
    conn = sqlite3.connect(str(db_path))
    try:
        report["integrity_before"] = integrity(conn)
        if report["integrity_before"] != "ok":
            raise SystemExit(f"integrity_check failed before migrating: {report['integrity_before']}")
        old_tables = tables(conn)
        report["counts_before"] = counts(conn, old_tables)
        if dry_run:
            report["pending_columns"] = [
                f"{t}.{c}" for t, c, _ in NEW_COLUMNS if t in old_tables and c not in columns(conn, t)
            ]
            report["pending_tables"] = [t for t in NEW_TABLES if t not in old_tables]
            return report
        for table, column, ddl in NEW_COLUMNS:
            if table in old_tables and column not in columns(conn, table):
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}')
                report["actions"].append(f"add column {table}.{column}")
        conn.commit()
    finally:
        conn.close()

    missing = [name for name in NEW_TABLES if name not in old_tables]
    if missing:
        from sqlalchemy import create_engine

        from app.models import Base

        engine = create_engine(f"sqlite+pysqlite:///{db_path.as_posix()}", future=True)
        try:
            Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in missing])
        finally:
            engine.dispose()
        report["actions"].extend(f"create table {name}" for name in missing)

    conn = sqlite3.connect(str(db_path))
    try:
        report["integrity_after"] = integrity(conn)
        after_tables = tables(conn)
        report["counts_after"] = counts(conn, old_tables)
        report["new_tables_present"] = all(name in after_tables for name in NEW_TABLES)
        report["new_columns_present"] = all(
            column in columns(conn, table) for table, column, _ in NEW_COLUMNS if table in after_tables
        )
    finally:
        conn.close()
    report["ok"] = (
        report["integrity_after"] == "ok"
        and report["counts_after"] == report["counts_before"]
        and report["new_tables_present"]
        and report["new_columns_present"]
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, type=Path, help="path to the SQLite database file")
    parser.add_argument("--backup-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--port", type=int, default=8000, help="refuse while this local port answers")
    parser.add_argument("--no-port-check", action="store_true")
    args = parser.parse_args(argv)
    report = migrate(
        args.db.resolve(),
        dry_run=args.dry_run,
        backup_dir=args.backup_dir,
        check_port=None if args.no_port_check else args.port,
    )
    print(json.dumps(report, indent=2, default=str))
    if args.dry_run:
        return 0
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
