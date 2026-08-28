"""P1 regression guards for canonical metric identity and EAC ownership."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from facts_provider.facts_provider import MetricValue, _stable_facts_version

ROOT = Path(__file__).resolve().parents[1]


def _mapping(*, calculated_at: str, eac_method: str = "canonical_management_revenue_progress_v1") -> dict:
    return {
        "entity_code": "A01",
        "entity_mapping_status": "VALID",
        "entity_mapping_valid": True,
        "entity_mapping_reason": "",
        "eac_method": eac_method,
        "calculated_at": calculated_at,
    }


def _metrics(revenue: float = 100.0) -> dict[str, MetricValue]:
    return {
        "recognized_revenue": MetricValue(revenue, "1.3", "CNY"),
        "real_profit": MetricValue(20.0, "2.1", "CNY"),
        "collection_rate": MetricValue(0.8, "2.0", "ratio"),
        "eac_profit": MetricValue(15.0, "2.0", "CNY"),
        "eac_margin": MetricValue(0.15, "2.0", "ratio"),
    }


def test_facts_version_ignores_volatile_calculated_at():
    first = _stable_facts_version(
        "P001",
        metrics=_metrics(),
        mapping=_mapping(calculated_at="2026-08-25T10:00:00+00:00"),
        facts_available=True,
    )
    second = _stable_facts_version(
        "P001",
        metrics=_metrics(),
        mapping=_mapping(calculated_at="2026-08-25T10:05:00+00:00"),
        facts_available=True,
    )
    assert first == second


def test_facts_version_changes_when_fact_or_method_changes():
    baseline = _stable_facts_version(
        "P001",
        metrics=_metrics(),
        mapping=_mapping(calculated_at=datetime.now(timezone.utc).isoformat()),
        facts_available=True,
    )
    changed_value = _stable_facts_version(
        "P001",
        metrics=_metrics(revenue=101.0),
        mapping=_mapping(calculated_at=datetime.now(timezone.utc).isoformat()),
        facts_available=True,
    )
    changed_method = _stable_facts_version(
        "P001",
        metrics=_metrics(),
        mapping=_mapping(
            calculated_at=datetime.now(timezone.utc).isoformat(),
            eac_method="future_method_v2",
        ),
        facts_available=True,
    )
    assert changed_value != baseline
    assert changed_method != baseline


def test_analytics_eac_calls_shared_postgresql_function():
    source = (ROOT / "sql" / "views" / "analytics_eac.sql").read_text(encoding="utf-8")
    assert "canonical_management_eac_cost(" in source
    assert "canonical_management_revenue_progress_v1" in source
    assert "actual_cost/(recognized_revenue/contract_amount)" not in source
