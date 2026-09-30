"""PDF editor v2 B2: `assets` (image/stamp gallery) and `export_jobs`.

Additive only. The local SQLite deployment uses
backend/scripts/migrate_local_editor_v2.py (extended for these two tables).

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-29
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("owner_user_id", UUID, nullable=True),
        sa.Column("contract_id", UUID, sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("label", sa.String(120), nullable=True),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("width_px", sa.Integer(), nullable=False),
        sa.Column("height_px", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("source_asset_id", UUID, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", TS, nullable=True),
        sa.CheckConstraint(
            "(scope = 'user' AND owner_user_id IS NOT NULL AND contract_id IS NULL) OR "
            "(scope = 'contract' AND contract_id IS NOT NULL AND owner_user_id IS NULL)",
            name="ck_assets_scope_owner",
        ),
        sa.CheckConstraint("kind IN ('image','stamp')", name="ck_assets_kind"),
    )
    op.create_index("idx_assets_owner", "assets", ["owner_user_id"])
    op.create_index("idx_assets_contract", "assets", ["contract_id"])
    op.create_table(
        "export_jobs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", UUID, nullable=False),
        sa.Column("requested_by", UUID, nullable=True),
        sa.Column("options", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("options_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result_key", sa.String(500), nullable=True),
        sa.Column("result_sha256", sa.String(64), nullable=True),
        sa.Column("result_bytes", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(64), nullable=True),
        sa.Column("warnings", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", TS, nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','done','failed','cancelled')", name="ck_export_jobs_status"
        ),
    )
    op.create_index("idx_export_jobs_reuse", "export_jobs", ["document_id", "version_id", "options_hash"])


def downgrade() -> None:
    op.drop_index("idx_export_jobs_reuse", table_name="export_jobs")
    op.drop_table("export_jobs")
    op.drop_index("idx_assets_contract", table_name="assets")
    op.drop_index("idx_assets_owner", table_name="assets")
    op.drop_table("assets")
