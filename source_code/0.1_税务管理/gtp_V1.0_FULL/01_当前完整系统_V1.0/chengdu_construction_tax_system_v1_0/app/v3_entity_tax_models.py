"""V3 Entity Tax Ledger ORM models."""
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


class EntityTaxManagementInput(Base):
    __tablename__ = "entity_tax_management_inputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reporting_party_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("internal_entities.party_id", ondelete="RESTRICT"), nullable=False
    )
    tax_period: Mapped[date] = mapped_column(Date, nullable=False)
    input_type: Mapped[str] = mapped_column(String(16), nullable=False)
    input_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    source_document_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(160), nullable=False)
    reviewed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        CheckConstraint(
            "EXTRACT(DAY FROM tax_period) = 1",
            name="ck_entity_tax_management_inputs_period_month_start",
        ),
        CheckConstraint(
            "input_type IN ('REVENUE','REAL_COST')",
            name="ck_entity_tax_management_inputs_type",
        ),
        CheckConstraint(
            "input_version >= 1",
            name="ck_entity_tax_management_inputs_version_positive",
        ),
        CheckConstraint(
            "amount >= 0",
            name="ck_entity_tax_management_inputs_amount_nonnegative",
        ),
        CheckConstraint(
            "btrim(source) <> ''",
            name="ck_entity_tax_management_inputs_source_nonblank",
        ),
        CheckConstraint(
            "reviewed AND reviewed_by IS NOT NULL AND btrim(reviewed_by)<>'' AND reviewed_at IS NOT NULL",
            name="ck_entity_tax_management_inputs_reviewed",
        ),
        UniqueConstraint(
            "reporting_party_id",
            "tax_period",
            "input_type",
            "input_version",
            name="uq_entity_tax_management_inputs_scope_type_version",
        ),
        Index("ix_entity_tax_management_inputs_scope", "reporting_party_id", "tax_period"),
        Index("ix_entity_tax_management_inputs_source_document", "source_document_id"),
    )


class EntityTaxLedger(Base):
    __tablename__ = "entity_tax_ledgers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calculation_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("calculation_runs.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    reporting_party_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("internal_entities.party_id", ondelete="RESTRICT"), nullable=False
    )
    tax_period: Mapped[date] = mapped_column(Date, nullable=False)
    entity_vat_ledger_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("entity_vat_ledgers.id", ondelete="RESTRICT"), nullable=False
    )
    revenue: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    real_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    estimated_profit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    estimated_cit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        CheckConstraint(
            "EXTRACT(DAY FROM tax_period) = 1",
            name="ck_entity_tax_ledgers_period_month_start",
        ),
        CheckConstraint(
            "revenue >= 0 AND real_cost >= 0",
            name="ck_entity_tax_ledgers_amounts_nonnegative",
        ),
        CheckConstraint(
            "estimated_profit = revenue - real_cost",
            name="ck_entity_tax_ledgers_profit_formula",
        ),
        CheckConstraint(
            "estimated_cit >= 0",
            name="ck_entity_tax_ledgers_cit_nonnegative",
        ),
        CheckConstraint(
            "estimated_profit > 0 OR estimated_cit = 0",
            name="ck_entity_tax_ledgers_loss_zero_cit",
        ),
        CheckConstraint(
            "btrim(rule_version) <> ''",
            name="ck_entity_tax_ledgers_rule_version_nonblank",
        ),
        CheckConstraint(
            "length(input_snapshot_sha256) = 64",
            name="ck_entity_tax_ledgers_input_hash_length",
        ),
        CheckConstraint(
            "length(result_sha256) = 64",
            name="ck_entity_tax_ledgers_result_hash_length",
        ),
        UniqueConstraint(
            "reporting_party_id",
            "tax_period",
            "calculation_run_id",
            name="uq_entity_tax_ledgers_scope_run",
        ),
        Index("ix_entity_tax_ledgers_scope", "reporting_party_id", "tax_period"),
    )


class EntityTaxLedgerComponent(Base):
    __tablename__ = "entity_tax_ledger_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ledger_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("entity_tax_ledgers.id", ondelete="RESTRICT"), nullable=False
    )
    component_type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    management_input_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("entity_tax_management_inputs.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        CheckConstraint(
            "component_type IN ('REVENUE','REAL_COST','ESTIMATED_CIT')",
            name="ck_entity_tax_ledger_components_type",
        ),
        CheckConstraint(
            "amount >= 0",
            name="ck_entity_tax_ledger_components_amount_nonnegative",
        ),
        CheckConstraint(
            "(component_type IN ('REVENUE','REAL_COST') AND management_input_id IS NOT NULL) OR "
            "(component_type='ESTIMATED_CIT' AND management_input_id IS NULL)",
            name="ck_entity_tax_ledger_components_typed_source",
        ),
        UniqueConstraint(
            "ledger_id",
            "component_type",
            name="uq_entity_tax_ledger_components_type",
        ),
        UniqueConstraint(
            "ledger_id",
            "management_input_id",
            name="uq_entity_tax_ledger_components_input",
        ),
        Index("ix_entity_tax_ledger_components_ledger", "ledger_id"),
        Index("ix_entity_tax_ledger_components_input", "management_input_id"),
    )
