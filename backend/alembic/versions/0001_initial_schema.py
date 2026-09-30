"""Initial schema: users, templates, contracts, contract_fields, signatures, audit_logs

Revision ID: 0001
Revises:
Create Date: 2026-08-29
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("full_name", sa.String(255)),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="contractor"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_users_email", "users", ["email"])

    op.create_table(
        "templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("file_path", sa.String(500)),
        sa.Column("file_hash", sa.String(64)),
        sa.Column("page_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("field_schema", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(50), nullable=False, server_default="processing"),
        sa.Column("error_message", sa.Text),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
    )

    op.create_table(
        "contracts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contractor_name", sa.String(255)),
        sa.Column("contractor_email", sa.String(255)),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("final_file_path", sa.String(500)),
        sa.Column("final_file_hash", sa.String(64)),
        sa.Column("status", sa.String(50), nullable=False, server_default="uploaded"),
        sa.Column("error_message", sa.Text),
        sa.Column("extracted_data", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("comparison_result", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("review_status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_notes", sa.Text),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # RESTRICT: a signed contract must never lose the template it was judged against.
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("idx_contracts_template", "contracts", ["template_id"])
    op.create_index("idx_contracts_status", "contracts", ["status"])

    op.create_table(
        "contract_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_field_id", sa.String(100), nullable=False),
        sa.Column("field_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="empty"),
        sa.Column("raw_value", sa.Text),
        sa.Column("normalized_value", sa.Text),
        sa.Column("confidence_score", sa.Numeric(3, 2)),
        sa.Column("is_required", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_valid", sa.Boolean),
        sa.Column("validation_error", sa.Text),
        sa.Column("bbox", postgresql.JSONB),
        sa.Column("page_number", sa.Integer),
        sa.Column("extra", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"], ondelete="CASCADE"),
        # Re-parsing a contract must update rows, not duplicate them.
        sa.UniqueConstraint("contract_id", "template_field_id", name="uq_contract_field"),
    )
    op.create_index("idx_fields_contract", "contract_fields", ["contract_id"])
    op.create_index("idx_fields_status", "contract_fields", ["status"])

    op.create_table(
        "signatures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signer_name", sa.String(255)),
        sa.Column("signer_email", sa.String(255)),
        sa.Column("signer_role", sa.String(50), nullable=False, server_default="contractor"),
        sa.Column("signature_image_path", sa.String(500), nullable=False),
        sa.Column("signature_hash", sa.String(64), nullable=False),
        sa.Column("page_number", sa.Integer, nullable=False),
        sa.Column("bbox", postgresql.JSONB, nullable=False),
        sa.Column("field_id", sa.String(100)),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ip_address", postgresql.INET),
        sa.Column("user_agent", sa.Text),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_signatures_contract", "signatures", ["contract_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True)),
        sa.Column("template_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", sa.String(100), nullable=False),
        # Deliberately not a foreign key: audit rows outlive the users they name.
        sa.Column("actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("actor_email", sa.String(255)),
        sa.Column("actor_role", sa.String(50)),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("ip_address", postgresql.INET),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_audit_contract", "audit_logs", ["contract_id"])
    op.create_index("idx_audit_timestamp", "audit_logs", ["timestamp"])

    # updated_at is maintained by the ORM on writes through the app, and by this
    # trigger for anything that touches the database directly.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in ("templates", "contracts"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
            """
        )


def downgrade() -> None:
    for table in ("contracts", "templates"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
    op.drop_table("audit_logs")
    op.drop_table("signatures")
    op.drop_table("contract_fields")
    op.drop_table("contracts")
    op.drop_table("templates")
    op.drop_table("users")
