"""Run the API locally without Docker, Postgres, Redis or MinIO.

The repo's normal boot path is `entrypoint.sh` (alembic upgrade head -> seed ->
uvicorn) against Postgres. The initial migration is Postgres-only (pgcrypto,
UUID/JSONB/INET columns, plpgsql triggers), so on this machine the schema comes
from `Base.metadata.create_all` instead -- exactly what `tests/conftest.py:121`
does, and the reason the models use dialect-portable column types.

Nothing in `app/` is modified. This wrapper only:
  1. loads backend/.env regardless of the process CWD,
  2. creates the SQLite schema,
  3. runs the two Celery tasks inline (no broker on this box),
  4. serves on 0.0.0.0 so the LAN can reach it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent

# Settings() reads env_file=".env" relative to the process CWD, and app.config
# instantiates `settings` at import time. Pin the CWD before any app import.
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))


def _create_schema() -> None:
    from app.core.database import engine
    from app.models import Base

    # Import every model module so the metadata is complete before create_all.
    from app.models import audit, contract, field, signature, template, user  # noqa: F401

    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine) -> None:
    """`create_all` never alters a table that already exists, so a column added
    to a model after this machine's database was first created has to be added
    here (the Postgres deployment gets it from alembic revision 0008)."""
    from sqlalchemy import inspect, text

    columns = {column["name"] for column in inspect(engine).get_columns("users")}
    if "email_verified" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT 0")
            )
            # Same rule as the alembic revision: staff are vouched for,
            # self-registered contractors are verified by an admin.
            connection.execute(
                text("UPDATE users SET email_verified = 1 WHERE role IN ('admin', 'reviewer')")
            )


class _Inline:
    """Celery's .delay() replaced by a synchronous call.

    `__wrapped__` is the undecorated function of a bind=True task, so the
    arguments pass straight through -- same trick as tests/conftest.py:169.
    """

    def __init__(self, func):
        self._func = func

    def delay(self, *args, **kwargs):
        self._func(*args, **kwargs)
        return type("Result", (), {"id": "inline"})()


def _run_tasks_inline() -> None:
    import app.api.contracts as contracts_api
    import app.api.templates as templates_api
    from app.workers import tasks as task_module

    templates_api.analyze_template_task = _Inline(task_module.analyze_template_task.__wrapped__)
    contracts_api.process_contract_task = _Inline(task_module.process_contract_task.__wrapped__)


def main() -> int:
    import uvicorn

    from app.config import settings

    _create_schema()
    _run_tasks_inline()

    from app.services.storage import storage

    storage.ensure_bucket()

    host = os.getenv("API_BIND", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))

    from app.main import app

    print(f"db      : {settings.database_url}")
    print(f"storage : {settings.storage_backend} -> {settings.storage_dir}")
    print(f"cors    : {settings.cors_origin_list}")
    print(f"serving : http://{host}:{port}  (tasks run inline, no Celery worker needed)")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
