"""Create the initial admin account.

Run once after migrations: SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD env vars.
Self-registration only ever produces contractors, so the first admin has to
come from somewhere.
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.user import User


def main() -> int:
    email = os.getenv("SEED_ADMIN_EMAIL")
    password = os.getenv("SEED_ADMIN_PASSWORD")
    if not email or not password:
        print("SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD are required.", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        existing = db.scalar(select(User).where(User.email == email.lower()))
        if existing is not None:
            print(f"Admin {email} already exists.")
            return 0
        db.add(
            User(
                email=email.lower(),
                full_name=os.getenv("SEED_ADMIN_NAME", "Administrator"),
                password_hash=hash_password(password),
                role="admin",
            )
        )
        db.commit()
        print(f"Created admin {email}.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
