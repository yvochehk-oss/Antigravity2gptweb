"""Deterministic Task13 rules for calculation runs and tax-period states."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


class TaxPeriodStateError(ValueError):
    pass


def month_start(value: date) -> date:
    return value.replace(day=1)


@dataclass(frozen=True)
class PeriodStateView:
    state: str
    current_run_id: int | None = None
    closed_run_id: int | None = None


@dataclass(frozen=True)
class RunIntent:
    run_kind: str
    supersedes_run_id: int | None


def next_run_intent(period: PeriodStateView | None) -> RunIntent:
    """Return the only allowed run shape for a period's next official result."""
    if period is None or period.state == "OPEN":
        return RunIntent("STANDARD", None)
    if period.state == "CLOSED":
        if period.current_run_id is None:
            raise TaxPeriodStateError("CLOSED period has no current run")
        return RunIntent("RESTATEMENT", period.current_run_id)
    raise TaxPeriodStateError(f"unknown period state: {period.state}")


def assert_close_allowed(period: PeriodStateView, *, succeeded_standard_run_id: int) -> None:
    if period.state != "OPEN":
        raise TaxPeriodStateError("only OPEN period may be closed")
    if succeeded_standard_run_id <= 0:
        raise TaxPeriodStateError("closing run id is required")


def assert_restatement_target(period: PeriodStateView, supersedes_run_id: int) -> None:
    if period.state != "CLOSED":
        raise TaxPeriodStateError("restatement is reserved for CLOSED periods")
    if period.current_run_id != supersedes_run_id:
        raise TaxPeriodStateError("restatement must supersede the current official run")
