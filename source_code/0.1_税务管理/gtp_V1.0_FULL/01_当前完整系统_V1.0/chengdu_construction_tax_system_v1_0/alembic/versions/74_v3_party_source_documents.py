"""Add V3 source-document and Party foundation without touching legacy facts.

Revision ID: 74_v3_party_source_documents
Revises: 73_v3_boundary_entity_refs

Task 05 is additive.  The existing ``external_parties`` table is retained for
legacy readers; it receives a nullable, unique ``party_id`` bridge that will be
backfilled and verified in Task 06 before any legacy key is retired.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "74_v3_party_source_documents"
down_revision = "73_v3_boundary_entity_refs"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()

    op.create_table(
        "source_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_system", sa.String(40), nullable=False),
        sa.Column("external_document_id", sa.String(160), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=True),
        sa.Column("file_sha256", sa.String(64), nullable=True),
        sa.Column("document_type", sa.String(40), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_uri", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="RECEIVED"),
        sa.CheckConstraint(
            "status IN ('RECEIVED','PARSED','EXTRACTED','VALIDATED','FAILED')",
            name="ck_source_documents_status",
        ),
        sa.UniqueConstraint(
            "source_system",
            "external_document_id",
            name="uq_source_documents_external_identity",
        ),
    )
    op.create_index("ix_source_documents_status", "source_documents", ["status"])
    op.create_index(
        "ix_source_documents_source_sha",
        "source_documents",
        ["source_system", "file_sha256"],
    )
    # When the upstream system has no document id, the file hash is an import
    # idempotency key only.  It is deliberately not a Fact business identity.
    op.create_index(
        "uq_source_documents_source_sha_without_external_id",
        "source_documents",
        ["source_system", "file_sha256"],
        unique=True,
        postgresql_where=sa.text(
            "external_document_id IS NULL AND file_sha256 IS NOT NULL"
        ),
    )

    op.create_table(
        "parties",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("short_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("party_type", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint(
            "party_type IN ('internal','external')",
            name="ck_parties_party_type",
        ),
        sa.UniqueConstraint("code", name="uq_parties_code"),
    )
    op.create_index("ix_parties_party_type", "parties", ["party_type"])
    op.create_index("ix_parties_active", "parties", ["active"])

    op.create_table(
        "internal_entities",
        sa.Column(
            "party_id",
            sa.Integer(),
            sa.ForeignKey("parties.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("canonical_code", sa.String(16), nullable=False),
        sa.Column("business_role", sa.String(32), nullable=False),
        sa.Column("legal_entity", sa.Boolean(), nullable=False),
        sa.Column(
            "parent_party_id",
            sa.Integer(),
            sa.ForeignKey("parties.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint(
            "parent_party_id IS NULL OR parent_party_id <> party_id",
            name="ck_internal_entities_not_own_parent",
        ),
        sa.UniqueConstraint("canonical_code", name="uq_internal_entities_canonical_code"),
    )
    op.create_index(
        "ix_internal_entities_parent_party_id",
        "internal_entities",
        ["parent_party_id"],
    )

    op.create_table(
        "party_identifiers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "party_id",
            sa.Integer(),
            sa.ForeignKey("parties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("identifier_type", sa.String(32), nullable=False),
        sa.Column("identifier_value", sa.String(160), nullable=False),
        sa.Column("source_system", sa.String(40), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint(
            "party_id",
            "identifier_type",
            "identifier_value",
            name="uq_party_identifiers_party_type_value",
        ),
    )
    op.create_index("ix_party_identifiers_party_id", "party_identifiers", ["party_id"])
    op.create_index(
        "ix_party_identifiers_lookup",
        "party_identifiers",
        ["identifier_type", "identifier_value"],
    )

    # Compatibility bridge: keep legacy external_parties.id/code/name/tax_id
    # untouched.  party_id stays nullable until Task 06 resolves every row.
    op.add_column("external_parties", sa.Column("party_id", sa.Integer(), nullable=True))
    op.add_column("external_parties", sa.Column("industry", sa.String(80), nullable=True))
    op.create_foreign_key(
        "fk_external_parties_party_id_parties",
        "external_parties",
        "parties",
        ["party_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_external_parties_party_id",
        "external_parties",
        ["party_id"],
    )


def downgrade() -> None:
    _require_postgresql()

    op.drop_constraint("uq_external_parties_party_id", "external_parties", type_="unique")
    op.drop_constraint(
        "fk_external_parties_party_id_parties",
        "external_parties",
        type_="foreignkey",
    )
    op.drop_column("external_parties", "industry")
    op.drop_column("external_parties", "party_id")

    op.drop_index("ix_party_identifiers_lookup", table_name="party_identifiers")
    op.drop_index("ix_party_identifiers_party_id", table_name="party_identifiers")
    op.drop_table("party_identifiers")

    op.drop_index("ix_internal_entities_parent_party_id", table_name="internal_entities")
    op.drop_table("internal_entities")

    op.drop_index("ix_parties_active", table_name="parties")
    op.drop_index("ix_parties_party_type", table_name="parties")
    op.drop_table("parties")

    op.drop_index(
        "uq_source_documents_source_sha_without_external_id",
        table_name="source_documents",
    )
    op.drop_index("ix_source_documents_source_sha", table_name="source_documents")
    op.drop_index("ix_source_documents_status", table_name="source_documents")
    op.drop_table("source_documents")
