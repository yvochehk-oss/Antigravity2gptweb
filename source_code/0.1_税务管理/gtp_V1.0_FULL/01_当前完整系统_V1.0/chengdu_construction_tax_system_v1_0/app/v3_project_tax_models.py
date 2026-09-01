"""V3 project-tax treatment and tax-prepayment ORM models.

These models are TAX-axis only. They do not represent cash settlement or
accrual recognition. Numeric prepayment rates are intentionally absent; rule
selection belongs to reviewed/versioned treatment evidence.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
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


class ProjectTaxTreatment(Base):
    __tablename__ = "project_tax_treatments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tax_type: Mapped[str] = mapped_column(String(32), nullable=False)
    treatment_code: Mapped[str] = mapped_column(String(64), nullable=False)
    reporting_party_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("internal_entities.party_id", ondelete="RESTRICT"),
        nullable=False,
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(240), nullable=False)
    reviewed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_project_tax_treatments_effective_range",
        ),
        Index(
            "ix_project_tax_treatments_project_tax_from",
            "project_id",
            "tax_type",
            "effective_from",
        ),
        Index(
            "ix_project_tax_treatments_reporting_party",
            "reporting_party_id",
        ),
    )


class TaxPrepaymentFact(Base):
    __tablename__ = "tax_prepayment_facts"

    fact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("facts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reporting_party_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("internal_entities.party_id", ondelete="RESTRICT"),
        nullable=False,
    )
    tax_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tax_period: Mapped[date] = mapped_column(Date, nullable=False)
    tax_event_date: Mapped[date] = mapped_column(Date, nullable=False)
    taxable_base: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    event_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'PREPAYMENT'"),
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        server_default=text("'CNY'"),
    )
    treatment_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("project_tax_treatments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_system: Mapped[str | None] = mapped_column(String(40), nullable=True)
    external_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "EXTRACT(DAY FROM tax_period) = 1",
            name="ck_tax_prepayment_facts_period_month_start",
        ),
        CheckConstraint(
            "taxable_base IS NULL OR taxable_base >= 0",
            name="ck_tax_prepayment_facts_taxable_base_nonnegative",
        ),
        CheckConstraint(
            "event_type IN ('PREPAYMENT','REVERSAL','ADJUSTMENT')",
            name="ck_tax_prepayment_facts_event_type",
        ),
        CheckConstraint(
            "(event_type <> 'PREPAYMENT' OR tax_amount > 0) AND "
            "(event_type <> 'REVERSAL' OR tax_amount < 0) AND "
            "(event_type <> 'ADJUSTMENT' OR tax_amount <> 0)",
            name="ck_tax_prepayment_facts_event_sign",
        ),
        UniqueConstraint(
            "source_system",
            "external_reference",
            name="uq_tax_prepayment_facts_source_reference",
        ),
        Index(
            "ix_tax_prepayment_facts_project_period",
            "project_id",
            "tax_type",
            "tax_period",
        ),
        Index(
            "ix_tax_prepayment_facts_reporting_period",
            "reporting_party_id",
            "tax_type",
            "tax_period",
        ),
        Index("ix_tax_prepayment_facts_treatment_id", "treatment_id"),
    )
