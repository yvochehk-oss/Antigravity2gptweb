"""Add V3 tax-reporting profiles with non-overlapping effective periods.

Revision ID: 75_v3_taxpayer_profiles
Revises: 74_v3_party_source_documents
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "75_v3_taxpayer_profiles"
down_revision = "74_v3_party_source_documents"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "party_tax_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "party_id",
            sa.Integer(),
            sa.ForeignKey("parties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tax_type", sa.String(32), nullable=False),
        sa.Column(
            "reporting_party_id",
            sa.Integer(),
            sa.ForeignKey("parties.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("taxpayer_category", sa.String(40), nullable=True),
        sa.Column("tax_registration_id", sa.String(64), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("rule_version", sa.String(40), nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_party_tax_profiles_effective_range",
        ),
    )
    op.create_index("ix_party_tax_profiles_party_id", "party_tax_profiles", ["party_id"])
    op.create_index(
        "ix_party_tax_profiles_reporting_party_id",
        "party_tax_profiles",
        ["reporting_party_id"],
    )
    op.create_index(
        "ix_party_tax_profiles_lookup",
        "party_tax_profiles",
        ["party_id", "tax_type", "effective_from"],
    )

    op.execute(
        """
        ALTER TABLE party_tax_profiles
        ADD CONSTRAINT ex_party_tax_profiles_no_overlap
        EXCLUDE USING gist (
            party_id WITH =,
            tax_type WITH =,
            daterange(effective_from, effective_to, '[]') WITH &&
        )
        """
    )


def downgrade() -> None:
    _require_postgresql()
    op.execute(
        "ALTER TABLE party_tax_profiles "
        "DROP CONSTRAINT IF EXISTS ex_party_tax_profiles_no_overlap"
    )
    op.drop_index("ix_party_tax_profiles_lookup", table_name="party_tax_profiles")
    op.drop_index(
        "ix_party_tax_profiles_reporting_party_id",
        table_name="party_tax_profiles",
    )
    op.drop_index("ix_party_tax_profiles_party_id", table_name="party_tax_profiles")
    op.drop_table("party_tax_profiles")
    # btree_gist may be shared by RAG or future migrations; never drop it here.
