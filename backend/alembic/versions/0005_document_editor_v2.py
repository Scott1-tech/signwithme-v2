"""PDF editor v2: documents, document_versions, document_pages, document_drafts,
plus signatures.page_uid and user_signatures.source (EDITOR-V2-PLAN §2).

All additive: `contracts` is not altered (the version pointer lives on
`documents`), existing rows read as "legacy contract" (no `documents` row) and
behave exactly as before. The documents <-> document_versions cycle is closed
with ALTER-added foreign keys after both tables exist.

The local SQLite deployment does not run alembic (0001 is Postgres-only); it
uses backend/scripts/migrate_local_editor_v2.py instead.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-29
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("contract_id", UUID, sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("original_version_id", UUID, nullable=True),
        sa.Column("current_version_id", UUID, nullable=True),
        sa.Column("markup", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("markup_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("markup_updated_by", UUID, nullable=True),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "document_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", UUID, nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("file_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("ops_applied", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("op_summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("content_changed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_reset", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("engine", sa.String(32), nullable=True),
        sa.Column("engine_ref", sa.String(64), nullable=True),
        sa.Column("commit_request_id", sa.String(64), nullable=True, unique=True),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("document_id", "version_no", name="uq_document_versions_no"),
        sa.CheckConstraint("kind IN ('original','edit','submission')", name="ck_document_versions_kind"),
    )
    op.create_index("idx_document_versions_doc_no", "document_versions", ["document_id", "version_no"])
    op.create_foreign_key(
        "fk_documents_original_version", "documents", "document_versions", ["original_version_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_documents_current_version", "documents", "document_versions", ["current_version_id"], ["id"]
    )
    op.create_table(
        "document_pages",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("version_id", UUID, sa.ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("page_uid", sa.String(24), nullable=False),
        sa.Column("origin_page", sa.Integer(), nullable=True),
        sa.Column("rotation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("width_pt", sa.Float(), nullable=False),
        sa.Column("height_pt", sa.Float(), nullable=False),
        sa.Column("has_text", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("scanned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("version_id", "position", name="uq_document_pages_position"),
        sa.UniqueConstraint("version_id", "page_uid", name="uq_document_pages_uid"),
    )
    op.create_index("idx_document_pages_uid", "document_pages", ["page_uid"])
    op.create_table(
        "document_drafts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("document_id", UUID, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("base_version_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ops", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("markup", JSONB, nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=True),
        sa.Column("owner_user_id", UUID, nullable=False),
        sa.Column("client_session_id", sa.String(64), nullable=False),
        sa.Column("lease_expires_at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.add_column("signatures", sa.Column("page_uid", sa.String(24), nullable=True))
    op.add_column("user_signatures", sa.Column("source", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("user_signatures", "source")
    op.drop_column("signatures", "page_uid")
    op.drop_table("document_drafts")
    op.drop_index("idx_document_pages_uid", table_name="document_pages")
    op.drop_table("document_pages")
    op.drop_constraint("fk_documents_current_version", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_original_version", "documents", type_="foreignkey")
    op.drop_index("idx_document_versions_doc_no", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_table("documents")
