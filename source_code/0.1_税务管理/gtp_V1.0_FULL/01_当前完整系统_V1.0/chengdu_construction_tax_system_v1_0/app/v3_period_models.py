"""V3 calculation-run and tax-period state ORM models."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class CalculationRun(Base):
    __tablename__ = "calculation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reporting_party_id: Mapped[int] = mapped_column(Integer, ForeignKey("internal_entities.party_id", ondelete="RESTRICT"), nullable=False)
    tax_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tax_period: Mapped[date] = mapped_column(Date, nullable=False)
    run_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    run_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'DRAFT'"))
    ruleset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supersedes_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("calculation_runs.id", ondelete="RESTRICT"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("EXTRACT(DAY FROM tax_period) = 1", name="ck_calculation_runs_period_month_start"),
        CheckConstraint("run_kind IN ('STANDARD','RESTATEMENT')", name="ck_calculation_runs_kind"),
        CheckConstraint("run_status IN ('DRAFT','SUCCEEDED','FAILED')", name="ck_calculation_runs_status"),
        CheckConstraint("(run_kind='STANDARD' AND supersedes_run_id IS NULL) OR (run_kind='RESTATEMENT' AND supersedes_run_id IS NOT NULL)", name="ck_calculation_runs_restatement_link"),
        CheckConstraint("supersedes_run_id IS NULL OR supersedes_run_id <> id", name="ck_calculation_runs_not_self_supersede"),
        CheckConstraint("run_status='DRAFT' OR completed_at IS NOT NULL", name="ck_calculation_runs_terminal_completed_at"),
        CheckConstraint("run_status<>'SUCCEEDED' OR (result_sha256 IS NOT NULL AND length(result_sha256)=64)", name="ck_calculation_runs_success_result_hash"),
        CheckConstraint("length(input_snapshot_sha256)=64", name="ck_calculation_runs_input_hash_length"),
        Index("ix_calculation_runs_scope", "reporting_party_id", "tax_type", "tax_period"),
        Index("ix_calculation_runs_supersedes", "supersedes_run_id"),
        Index("ix_calculation_runs_status", "run_status"),
    )


class TaxPeriodState(Base):
    __tablename__ = "tax_period_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reporting_party_id: Mapped[int] = mapped_column(Integer, ForeignKey("internal_entities.party_id", ondelete="RESTRICT"), nullable=False)
    tax_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tax_period: Mapped[date] = mapped_column(Date, nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False, server_default=text("'OPEN'"))
    current_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("calculation_runs.id", ondelete="RESTRICT"), nullable=True)
    closed_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("calculation_runs.id", ondelete="RESTRICT"), nullable=True)
    closed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("EXTRACT(DAY FROM tax_period) = 1", name="ck_tax_period_states_period_month_start"),
        CheckConstraint("state IN ('OPEN','CLOSED')", name="ck_tax_period_states_state"),
        CheckConstraint("state_version >= 1", name="ck_tax_period_states_version_positive"),
        CheckConstraint("(state='OPEN' AND closed_run_id IS NULL AND closed_by IS NULL AND closed_at IS NULL) OR (state='CLOSED' AND current_run_id IS NOT NULL AND closed_run_id IS NOT NULL AND closed_by IS NOT NULL AND btrim(closed_by)<>'' AND closed_at IS NOT NULL)", name="ck_tax_period_states_close_fields"),
        UniqueConstraint("reporting_party_id", "tax_type", "tax_period", name="uq_tax_period_states_scope"),
        Index("ix_tax_period_states_current_run", "current_run_id"),
        Index("ix_tax_period_states_closed_run", "closed_run_id"),
        Index("ix_tax_period_states_state", "state"),
    )
