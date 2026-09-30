"""Seed the first admin without Docker.

`entrypoint.sh:11-13` works because compose injects SEED_ADMIN_* as real
environment variables, and `app/seed.py:20-21` reads `os.getenv`. Loading
`.env` through pydantic only fills the `Settings` object -- and `Settings` has
no seed_admin_* fields anyway (`config.py`, extra="ignore"). So parse the file
here and put the three keys into the process environment before calling seed.

Keeps the password off the command line and out of shell history.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

WANTED = ("SEED_ADMIN_EMAIL", "SEED_ADMIN_PASSWORD", "SEED_ADMIN_NAME")


def _load_seed_env() -> None:
    env_file = BACKEND_DIR / ".env"
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in WANTED:
            os.environ.setdefault(key, value.strip())


def main() -> int:
    _load_seed_env()
    missing = [k for k in WANTED[:2] if not os.environ.get(k)]
    if missing:
        print(f"missing in .env: {', '.join(missing)}", file=sys.stderr)
        return 1

    import local_run

    local_run._create_schema()

    from app.seed import main as seed_main

    return seed_main()


if __name__ == "__main__":
    raise SystemExit(main())
