"""PDF editor v2 F3: `share_links` and `form_submissions` (share-for-fill).

Additive only. The local SQLite deployment uses
backend/scripts/migrate_local_editor_v2.py (extended for these two tables).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-30
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "share_links",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", UUID, sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", UUID, nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False, server_default="fill"),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("allowed_fields", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("recipient_label", sa.String(120), nullable=True),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("max_submissions", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("submission_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("revoked_by", UUID, nullable=True),
        sa.Column("last_used_at", TS, nullable=True),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("purpose IN ('fill')", name="ck_share_links_purpose"),
    )
    op.create_index("idx_share_links_document", "share_links", ["document_id"])
    op.create_index("idx_share_links_contract", "share_links", ["contract_id"])
    op.create_table(
        "form_submissions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("share_link_id", UUID, sa.ForeignKey("share_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("version_id", UUID, nullable=False),
        sa.Column("values_encrypted", sa.Text(), nullable=False),
        sa.Column("field_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("submitter_name", sa.String(120), nullable=True),
        sa.Column("submitter_email", sa.String(254), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="received"),
        sa.Column("reviewed_by", UUID, nullable=True),
        sa.Column("reviewed_at", TS, nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('received','accepted','rejected')", name="ck_form_submissions_status"
        ),
    )
    op.create_index("idx_form_submissions_document", "form_submissions", ["document_id", "status"])
    op.create_index("idx_form_submissions_link", "form_submissions", ["share_link_id"])


def downgrade() -> None:
    op.drop_index("idx_form_submissions_link", table_name="form_submissions")
    op.drop_index("idx_form_submissions_document", table_name="form_submissions")
    op.drop_table("form_submissions")
    op.drop_index("idx_share_links_contract", table_name="share_links")
    op.drop_index("idx_share_links_document", table_name="share_links")
    op.drop_table("share_links")
