"""Legal-entity invoice fact-period discovery from Canonical Facts SSOT."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from sqlalchemy import text

from .canonical_ssot import _payload
from .legal_entity_scope import _code, _invoice_period

_SOURCE = "analytics_canonical_facts_current"


class LegalEntityNotFoundError(LookupError):
    """Raised when a canonical code is not an active V3 internal legal entity."""


def _summarize_fact_periods(
    facts: Iterable[dict[str, Any]],
    *,
    entity_code: str,
) -> dict[str, Any]:
    """Pure period aggregation used by regression tests and compatibility callers."""
    wanted = _code(entity_code)
    counts: Counter[str] = Counter()
    unperiodized_fact_count = 0

    for fact in facts:
        payload = _payload(fact.get("payload"))
        seller = _code(payload.get("seller_entity_code") or payload.get("seller_code"))
        buyer = _code(payload.get("buyer_entity_code") or payload.get("buyer_code"))
        if wanted not in {seller, buyer}:
            continue
        fact_period = _invoice_period(payload)
        if not fact_period:
            unperiodized_fact_count += 1
            continue
        counts[fact_period] += 1

    ordered = sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True)
    periods = [
        {
            "period": period,
            "fact_count": fact_count,
            "is_primary": index == 0,
        }
        for index, (period, fact_count) in enumerate(ordered)
    ]
    return {
        "status": "READY" if periods else "EMPTY",
        "entity_code": wanted,
        "source_of_truth": _SOURCE,
        "fact_type": "invoice",
        "total_fact_count": sum(counts.values()),
        "unperiodized_fact_count": unperiodized_fact_count,
        "periods": periods,
    }


def get_legal_entity_fact_periods(db, entity_code: str) -> dict[str, Any]:
    """Return actual invoice periods for one active V3 legal entity.

    Entity filtering and period aggregation stay inside PostgreSQL so response
    cost scales with the selected legal entity's period cardinality instead of
    transferring every canonical invoice payload into the application process.
    """
    wanted = _code(entity_code)
    exists = db.execute(
        text(
            "SELECT p.id "
            "FROM parties p "
            "JOIN internal_entities ie ON ie.party_id = p.id "
            "WHERE p.party_type = 'internal' "
            "AND p.active = TRUE "
            "AND ie.active = TRUE "
            "AND ie.legal_entity = TRUE "
            "AND UPPER(ie.canonical_code) = :entity_code "
            "LIMIT 1"
        ),
        {"entity_code": wanted},
    ).scalar_one_or_none()
    if exists is None:
        raise LegalEntityNotFoundError(wanted)

    rows = db.execute(
        text(
            "WITH scoped AS ("
            "SELECT CASE "
            "WHEN length(entity_facts.raw_period) >= 7 "
            "AND substr(entity_facts.raw_period, 5, 1) = '-' "
            "THEN left(entity_facts.raw_period, 7) "
            "ELSE NULL END AS fact_period "
            "FROM ("
            "SELECT btrim(COALESCE("
            "NULLIF(payload::jsonb ->> 'invoice_date', ''), "
            "payload::jsonb ->> 'period', ''"
            ")) AS raw_period "
            "FROM analytics_canonical_facts_current "
            "WHERE fact_type = 'invoice' "
            "AND ("
            "UPPER(btrim(COALESCE("
            "NULLIF(payload::jsonb ->> 'seller_entity_code', ''), "
            "payload::jsonb ->> 'seller_code', ''"
            "))) = :entity_code "
            "OR UPPER(btrim(COALESCE("
            "NULLIF(payload::jsonb ->> 'buyer_entity_code', ''), "
            "payload::jsonb ->> 'buyer_code', ''"
            "))) = :entity_code"
            ")"
            ") AS entity_facts"
            ") "
            "SELECT fact_period AS period, COUNT(*) AS fact_count "
            "FROM scoped "
            "GROUP BY fact_period "
            "ORDER BY (fact_period IS NULL), COUNT(*) DESC, fact_period DESC"
        ),
        {"entity_code": wanted},
    ).mappings().all()

    periods: list[dict[str, Any]] = []
    total_fact_count = 0
    unperiodized_fact_count = 0
    for row in rows:
        fact_count = int(row["fact_count"] or 0)
        period = str(row["period"] or "").strip()
        if not period:
            unperiodized_fact_count += fact_count
            continue
        total_fact_count += fact_count
        periods.append(
            {
                "period": period,
                "fact_count": fact_count,
                "is_primary": len(periods) == 0,
            }
        )

    return {
        "status": "READY" if periods else "EMPTY",
        "entity_code": wanted,
        "source_of_truth": _SOURCE,
        "fact_type": "invoice",
        "total_fact_count": total_fact_count,
        "unperiodized_fact_count": unperiodized_fact_count,
        "periods": periods,
    }


__all__ = [
    "LegalEntityNotFoundError",
    "get_legal_entity_fact_periods",
    "_summarize_fact_periods",
]
