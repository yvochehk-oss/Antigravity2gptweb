"""V3 Contract/Fulfillment Fact projections.

Project attribution is intentionally absent. ``internal_trade`` is derived from
resolved Party membership and is never stored as a second source of truth.
Fulfillment may be extracted independently and linked to a ContractFact later.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class ContractFact(Base):
    __tablename__ = "contract_facts"

    fact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("facts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    buyer_party_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=True,
    )
    seller_party_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=True,
    )
    contract_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contract_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    contract_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    contract_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'CNY'")
    )

    __table_args__ = (
        CheckConstraint(
            "buyer_party_id IS NULL OR seller_party_id IS NULL OR buyer_party_id <> seller_party_id",
            name="ck_contract_facts_distinct_parties",
        ),
        CheckConstraint(
            "contract_amount IS NULL OR contract_amount >= 0",
            name="ck_contract_facts_amount_nonnegative",
        ),
        Index("ix_contract_facts_buyer_party_id", "buyer_party_id"),
        Index("ix_contract_facts_seller_party_id", "seller_party_id"),
        Index("ix_contract_facts_contract_number", "contract_number"),
        Index("ix_contract_facts_contract_date", "contract_date"),
    )


class FulfillmentFact(Base):
    __tablename__ = "fulfillment_facts"

    fact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("facts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    contract_fact_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("contract_facts.fact_id", ondelete="RESTRICT"),
        nullable=True,
    )
    performing_party_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=True,
    )
    receiving_party_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=True,
    )
    fulfillment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fulfillment_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'CNY'")
    )

    __table_args__ = (
        CheckConstraint(
            "performing_party_id IS NULL OR receiving_party_id IS NULL OR performing_party_id <> receiving_party_id",
            name="ck_fulfillment_facts_distinct_parties",
        ),
        CheckConstraint(
            "quantity IS NULL OR quantity >= 0",
            name="ck_fulfillment_facts_quantity_nonnegative",
        ),
        CheckConstraint(
            "amount IS NULL OR amount >= 0",
            name="ck_fulfillment_facts_amount_nonnegative",
        ),
        Index("ix_fulfillment_facts_contract_fact_id", "contract_fact_id"),
        Index("ix_fulfillment_facts_performing_party_id", "performing_party_id"),
        Index("ix_fulfillment_facts_receiving_party_id", "receiving_party_id"),
        Index("ix_fulfillment_facts_fulfillment_date", "fulfillment_date"),
    )
