"""users.email_verified: access by e-mail needs a vouched-for address.

A contract addressed to `contractor_email` used to open for ANY account whose
e-mail matched -- and registration never checked that the person owned the
address. From here on that match also needs `email_verified`.

Existing rows: admins and reviewers are marked verified (they see every
contract anyway, and an operator created them). Existing contractors are NOT:
each one has to be verified by an admin (POST /api/auth/users/{id}/verify-email)
before contracts addressed to their e-mail open again. Contracts they uploaded
themselves are unaffected.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    users = sa.table("users", sa.column("role", sa.String), sa.column("email_verified", sa.Boolean))
    op.execute(
        users.update().where(users.c.role.in_(("admin", "reviewer"))).values(email_verified=True)
    )


def downgrade() -> None:
    op.drop_column("users", "email_verified")
