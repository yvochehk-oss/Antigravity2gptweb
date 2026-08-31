"""Task19 canonical PaymentFact and legacy CashFlow bridge models."""
from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class PaymentFact(Base):
    __tablename__ = "payment_facts"
    fact_id: Mapped[int] = mapped_column(Integer, ForeignKey("facts.id", ondelete="CASCADE"), primary_key=True)
    payer_party_id: Mapped[int] = mapped_column(Integer, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False)
    payee_party_id: Mapped[int] = mapped_column(Integer, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'CNY'"))
    payer_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payee_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bank_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    settlement_method: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'UNKNOWN'"))
    payment_nature: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'OTHER'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    __table_args__ = (
        CheckConstraint("payer_party_id <> payee_party_id", name="ck_payment_facts_distinct_parties"),
        CheckConstraint("amount > 0", name="ck_payment_facts_amount_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_payment_facts_currency"),
        CheckConstraint("settlement_method IN ('BANK_TRANSFER','CASH','BILL','OFFSET','OTHER','UNKNOWN')", name="ck_payment_facts_settlement_method"),
        CheckConstraint("payment_nature IN ('NORMAL','ADVANCE','DEPOSIT','GUARANTEE','REFUND','TAX','PAYROLL','OTHER')", name="ck_payment_facts_nature"),
        Index("ix_payment_facts_payer_date", "payer_party_id", "transaction_date"),
        Index("ix_payment_facts_payee_date", "payee_party_id", "transaction_date"),
        Index("ix_payment_facts_bank_reference", "bank_reference"),
    )


class LegacyCashflowMap(Base):
    __tablename__ = "legacy_cashflow_map"
    legacy_cashflow_id: Mapped[int] = mapped_column(Integer, ForeignKey("cashflows.id", ondelete="RESTRICT"), primary_key=True)
    payment_fact_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("payment_facts.fact_id", ondelete="RESTRICT"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    mapped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    __table_args__ = (
        CheckConstraint("status IN ('MIGRATED','NEEDS_REVIEW','REJECTED')", name="ck_legacy_cashflow_map_status"),
        CheckConstraint("(status='MIGRATED' AND payment_fact_id IS NOT NULL) OR (status<>'MIGRATED' AND payment_fact_id IS NULL)", name="ck_legacy_cashflow_map_link_semantics"),
        Index("ix_legacy_cashflow_map_payment_fact", "payment_fact_id"),
        Index("ix_legacy_cashflow_map_status", "status"),
    )
