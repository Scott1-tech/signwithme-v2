"""Reviewer override of critical missing-required fields: contracts.review_override*

A reviewer may approve a contract whose only outstanding critical issues are
empty required fields, with a written reason. The three columns record that
the approval was an override, why, and which fields were empty at the time.
All additive: existing rows read as "no override".

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-10
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contracts",
        sa.Column("review_override", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("contracts", sa.Column("review_override_reason", sa.Text(), nullable=True))
    op.add_column(
        "contracts",
        sa.Column("review_override_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contracts", "review_override_fields")
    op.drop_column("contracts", "review_override_reason")
    op.drop_column("contracts", "review_override")
