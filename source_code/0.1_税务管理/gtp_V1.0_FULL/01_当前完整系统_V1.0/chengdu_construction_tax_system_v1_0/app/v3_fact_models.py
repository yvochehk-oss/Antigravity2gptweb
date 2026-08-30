"""V3 Task 07a Fact/Invoice ORM models sharing the Tax ``Base`` metadata.

The legacy ``invoices`` table remains untouched and is not shadowed by these
models. A physical invoice is represented once in ``invoice_facts`` and old
IN/OUT rows are mapped later through ``legacy_invoice_map``.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Fact(Base):
    __tablename__ = "facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fact_type: Mapped[str] = mapped_column(String(40), nullable=False)
    business_identity_key: Mapped[str] = mapped_column(String(240), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    supersedes_fact_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("facts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    validation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'DRAFT'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        CheckConstraint(
            "validation_status IN ('DRAFT','VALID','INVALID','SUPERSEDED','NEEDS_REVIEW')",
            name="ck_facts_validation_status",
        ),
        CheckConstraint("version_no >= 1", name="ck_facts_version_positive"),
        CheckConstraint(
            "supersedes_fact_id IS NULL OR supersedes_fact_id <> id",
            name="ck_facts_not_self_supersede",
        ),
        UniqueConstraint(
            "business_identity_key",
            "version_no",
            name="uq_facts_business_identity_version",
        ),
        Index("ix_facts_fact_type", "fact_type"),
        Index("ix_facts_validation_status", "validation_status"),
        Index("ix_facts_supersedes_fact_id", "supersedes_fact_id"),
        Index(
            "uq_facts_current_business_identity",
            "business_identity_key",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )


class InvoiceFact(Base):
    __tablename__ = "invoice_facts"

    fact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("facts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    seller_party_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=True,
    )
    buyer_party_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=True,
    )
    invoice_identity_key: Mapped[str] = mapped_column(String(240), nullable=False)
    invoice_identity_version: Mapped[str] = mapped_column(String(24), nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    invoice_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    invoice_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    invoice_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    document_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    gross_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    net_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    vat_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'CNY'")
    )

    __table_args__ = (
        UniqueConstraint("invoice_identity_key", name="uq_invoice_facts_identity_key"),
        Index("ix_invoice_facts_seller_party_id", "seller_party_id"),
        Index("ix_invoice_facts_buyer_party_id", "buyer_party_id"),
        Index("ix_invoice_facts_invoice_date", "invoice_date"),
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_fact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("invoice_facts.fact_id", ondelete="CASCADE"),
        nullable=False,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    item_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    net_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    vat_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 6), nullable=True)
    tax_classification_code: Mapped[str | None] = mapped_column(String(80), nullable=True)

    __table_args__ = (
        CheckConstraint("line_no >= 1", name="ck_invoice_lines_line_no_positive"),
        UniqueConstraint(
            "invoice_fact_id",
            "line_no",
            name="uq_invoice_lines_fact_line_no",
        ),
        Index("ix_invoice_lines_invoice_fact_id", "invoice_fact_id"),
    )


class FactProvenance(Base):
    __tablename__ = "fact_provenance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("facts.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("source_documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    chunk_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(6, 5), nullable=True)
    extraction_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    extraction_model_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    original_extracted_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    verifier_user_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    verification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_fact_provenance_confidence",
        ),
        CheckConstraint(
            "page_start IS NULL OR page_end IS NULL OR page_end >= page_start",
            name="ck_fact_provenance_page_range",
        ),
        Index("ix_fact_provenance_fact_id", "fact_id"),
        Index("ix_fact_provenance_document_id", "document_id"),
    )


class LegacyInvoiceMap(Base):
    __tablename__ = "legacy_invoice_map"

    legacy_invoice_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("invoices.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    invoice_fact_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("invoice_facts.fact_id", ondelete="RESTRICT"),
        nullable=True,
    )
    legacy_direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    migration_status: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    mapped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        CheckConstraint(
            "migration_status IN ('MIGRATED','MERGED','MIGRATED_SINGLE_PERSPECTIVE','NEEDS_REVIEW','REJECTED')",
            name="ck_legacy_invoice_map_status",
        ),
        Index("ix_legacy_invoice_map_invoice_fact_id", "invoice_fact_id"),
        Index("ix_legacy_invoice_map_status", "migration_status"),
    )
