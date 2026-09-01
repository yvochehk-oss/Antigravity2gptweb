"""Task20 canonical Business Transaction Graph ORM models."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class BusinessTransaction(Base):
    __tablename__ = "business_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_identity_key: Mapped[str] = mapped_column(String(240), nullable=False, unique=True)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class TransactionFactLink(Base):
    __tablename__ = "transaction_fact_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(Integer, ForeignKey("business_transactions.id", ondelete="CASCADE"), nullable=False)
    fact_id: Mapped[int] = mapped_column(Integer, ForeignKey("facts.id", ondelete="RESTRICT"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    allocated_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    allocation_method: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'EXPLICIT'"))
    link_source: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(6, 5), nullable=True)
    match_score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'CANDIDATE'"))
    confirmed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("relation_type IN ('CONTRACT','FULFILLMENT','INVOICE','PAYMENT')", name="ck_transaction_fact_links_relation_type"),
        CheckConstraint("allocation_method IN ('EXPLICIT','SOURCE_DOCUMENT','MANUAL','PROPORTIONAL','RULE_BASED')", name="ck_transaction_fact_links_allocation_method"),
        CheckConstraint("status IN ('CANDIDATE','NEEDS_REVIEW','CONFIRMED','REJECTED')", name="ck_transaction_fact_links_status"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_transaction_fact_links_confidence"),
        CheckConstraint("(status='CONFIRMED' AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL) OR (status<>'CONFIRMED' AND confirmed_by IS NULL AND confirmed_at IS NULL)", name="ck_transaction_fact_links_confirmation_semantics"),
        UniqueConstraint("transaction_id", "fact_id", "relation_type", name="uq_transaction_fact_link"),
        Index("ix_transaction_fact_links_transaction", "transaction_id"),
        Index("ix_transaction_fact_links_fact", "fact_id"),
        Index("ix_transaction_fact_links_status", "status"),
    )


class TransactionParticipant(Base):
    __tablename__ = "transaction_participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(Integer, ForeignKey("business_transactions.id", ondelete="CASCADE"), nullable=False)
    party_id: Mapped[int] = mapped_column(Integer, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False)
    source_fact_id: Mapped[int] = mapped_column(Integer, ForeignKey("facts.id", ondelete="RESTRICT"), nullable=False)
    participant_role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint(
            "participant_role IN ('CONTRACT_BUYER','CONTRACT_SELLER','FULFILLMENT_PROVIDER','FULFILLMENT_RECEIVER','INVOICE_SELLER','INVOICE_BUYER','PAYMENT_PAYER','PAYMENT_PAYEE')",
            name="ck_transaction_participants_role",
        ),
        UniqueConstraint("transaction_id", "source_fact_id", "participant_role", name="uq_transaction_participant_source_role"),
        Index("ix_transaction_participants_transaction", "transaction_id"),
        Index("ix_transaction_participants_party", "party_id"),
    )


class ReviewTask(Base):
    __tablename__ = "review_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'P2'"))
    assigned_to: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'OPEN'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("object_type='TRANSACTION_FACT_LINK'", name="ck_review_tasks_object_type"),
        CheckConstraint("priority IN ('P0','P1','P2','P3')", name="ck_review_tasks_priority"),
        CheckConstraint("status IN ('OPEN','IN_REVIEW','RESOLVED','CANCELLED')", name="ck_review_tasks_status"),
        CheckConstraint("(status IN ('OPEN','IN_REVIEW') AND resolved_at IS NULL) OR (status IN ('RESOLVED','CANCELLED') AND resolved_at IS NOT NULL)", name="ck_review_tasks_resolution_semantics"),
        Index("ix_review_tasks_object", "object_type", "object_id"),
        Index("ix_review_tasks_status", "status"),
        Index("uq_review_tasks_open_object", "object_type", "object_id", unique=True, postgresql_where=text("status IN ('OPEN','IN_REVIEW')")),
    )
