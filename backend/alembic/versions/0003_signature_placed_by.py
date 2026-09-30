"""Who placed a contract signature: signatures.placed_by_user_id

Removing a signature is restricted to the account that placed it or an admin.
The column is nullable so existing rows survive untouched; a NULL placer means
"unknown", which the API treats as admin-only to remove.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-10
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "signatures",
        sa.Column("placed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # The signature row (and its audit trail) outlives the account that placed it.
    op.create_foreign_key(
        "fk_signatures_placed_by_user",
        "signatures",
        "users",
        ["placed_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_signatures_placed_by", "signatures", ["placed_by_user_id"])


def downgrade() -> None:
    op.drop_index("idx_signatures_placed_by", table_name="signatures")
    op.drop_constraint("fk_signatures_placed_by_user", "signatures", type_="foreignkey")
    op.drop_column("signatures", "placed_by_user_id")
