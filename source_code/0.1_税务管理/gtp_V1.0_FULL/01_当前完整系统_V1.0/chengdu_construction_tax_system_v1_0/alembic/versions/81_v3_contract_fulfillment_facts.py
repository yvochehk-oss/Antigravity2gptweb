"""Add ContractFact and FulfillmentFact projections.

Revision ID: 81_v3_contract_fulfillment_facts
Revises: 80_v3_input_vat_claims

Contract and fulfillment data become Fact subtypes. Project allocation and
legacy ``internal_trade`` flags deliberately remain outside these tables.
Fulfillment facts may exist independently of a known contract so RAG/document
extraction does not require an artificial contract link.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "81_v3_contract_fulfillment_facts"
down_revision = "80_v3_input_vat_claims"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()

    op.create_table(
        "contract_facts",
        sa.Column(
            "fact_id",
            sa.Integer(),
            sa.ForeignKey("facts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "buyer_party_id",
            sa.Integer(),
            sa.ForeignKey("parties.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "seller_party_id",
            sa.Integer(),
            sa.ForeignKey("parties.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("contract_number", sa.String(100), nullable=True),
        sa.Column("contract_date", sa.Date(), nullable=True),
        sa.Column("contract_category", sa.String(40), nullable=True),
        sa.Column("contract_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"),
        sa.CheckConstraint(
            "buyer_party_id IS NULL OR seller_party_id IS NULL OR buyer_party_id <> seller_party_id",
            name="ck_contract_facts_distinct_parties",
        ),
        sa.CheckConstraint(
            "contract_amount IS NULL OR contract_amount >= 0",
            name="ck_contract_facts_amount_nonnegative",
        ),
    )
    op.create_index("ix_contract_facts_buyer_party_id", "contract_facts", ["buyer_party_id"])
    op.create_index("ix_contract_facts_seller_party_id", "contract_facts", ["seller_party_id"])
    op.create_index("ix_contract_facts_contract_number", "contract_facts", ["contract_number"])
    op.create_index("ix_contract_facts_contract_date", "contract_facts", ["contract_date"])

    op.create_table(
        "fulfillment_facts",
        sa.Column(
            "fact_id",
            sa.Integer(),
            sa.ForeignKey("facts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "contract_fact_id",
            sa.Integer(),
            sa.ForeignKey("contract_facts.fact_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "performing_party_id",
            sa.Integer(),
            sa.ForeignKey("parties.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "receiving_party_id",
            sa.Integer(),
            sa.ForeignKey("parties.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("fulfillment_date", sa.Date(), nullable=True),
        sa.Column("fulfillment_kind", sa.String(40), nullable=True),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"),
        sa.CheckConstraint(
            "performing_party_id IS NULL OR receiving_party_id IS NULL OR performing_party_id <> receiving_party_id",
            name="ck_fulfillment_facts_distinct_parties",
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity >= 0",
            name="ck_fulfillment_facts_quantity_nonnegative",
        ),
        sa.CheckConstraint(
            "amount IS NULL OR amount >= 0",
            name="ck_fulfillment_facts_amount_nonnegative",
        ),
    )
    op.create_index(
        "ix_fulfillment_facts_contract_fact_id",
        "fulfillment_facts",
        ["contract_fact_id"],
    )
    op.create_index(
        "ix_fulfillment_facts_performing_party_id",
        "fulfillment_facts",
        ["performing_party_id"],
    )
    op.create_index(
        "ix_fulfillment_facts_receiving_party_id",
        "fulfillment_facts",
        ["receiving_party_id"],
    )
    op.create_index(
        "ix_fulfillment_facts_fulfillment_date",
        "fulfillment_facts",
        ["fulfillment_date"],
    )


def downgrade() -> None:
    _require_postgresql()
    op.drop_index("ix_fulfillment_facts_fulfillment_date", table_name="fulfillment_facts")
    op.drop_index("ix_fulfillment_facts_receiving_party_id", table_name="fulfillment_facts")
    op.drop_index("ix_fulfillment_facts_performing_party_id", table_name="fulfillment_facts")
    op.drop_index("ix_fulfillment_facts_contract_fact_id", table_name="fulfillment_facts")
    op.drop_table("fulfillment_facts")

    op.drop_index("ix_contract_facts_contract_date", table_name="contract_facts")
    op.drop_index("ix_contract_facts_contract_number", table_name="contract_facts")
    op.drop_index("ix_contract_facts_seller_party_id", table_name="contract_facts")
    op.drop_index("ix_contract_facts_buyer_party_id", table_name="contract_facts")
    op.drop_table("contract_facts")
