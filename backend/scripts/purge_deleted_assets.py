"""Admin clean-up: purge gallery images soft-deleted more than N days ago.

Removes USER-scope `assets` rows whose `deleted_at` is at least N days old
(default `DELETED_ASSET_RETENTION_DAYS`, 30) and any leftover file of theirs.
Contract-scope copies (images placed on contracts) are never touched. Safe to
run any number of times (app.services.asset_purge).

Usage (from backend/, the server may keep running -- one short transaction):
  .venv\\Scripts\\python.exe scripts\\purge_deleted_assets.py --dry-run
  .venv\\Scripts\\python.exe scripts\\purge_deleted_assets.py [--days 30]

Prints a JSON report (ids and counts only). Exit 0.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    parser.add_argument("--days", type=int, default=None,
                        help="minimum age of the soft delete in days (default: the setting, 30)")
    args = parser.parse_args(argv)
    if args.days is not None and args.days < 0:
        parser.error("--days must be >= 0")

    # Settings read backend/.env relative to the CWD at import time (local_run.py).
    os.chdir(BACKEND_DIR)
    sys.path.insert(0, str(BACKEND_DIR))
    from app.core.database import SessionLocal
    from app.services.asset_purge import purge_deleted_assets

    db = SessionLocal()
    try:
        report = purge_deleted_assets(db, older_than_days=args.days, dry_run=args.dry_run)
    finally:
        db.close()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
