"""User signature library: user_signatures

A per-user library of reusable signatures, keyed by the owning user rather than
by a contract. Distinct from the contract-scoped ``signatures`` table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-07
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_signatures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(255)),
        sa.Column("image_path", sa.String(500), nullable=False),
        sa.Column("signature_hash", sa.String(64), nullable=False),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # A user's signatures die with the user account.
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_user_signatures_user", "user_signatures", ["user_id"])


def downgrade() -> None:
    op.drop_table("user_signatures")
