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
    """Pure period aggregation used by the DB adapter and regression tests."""
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
    """Return actual invoice periods for one active V3 legal entity."""
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

    facts = [
        dict(row)
        for row in db.execute(
            text(
                "SELECT fact_id, payload "
                "FROM analytics_canonical_facts_current "
                "WHERE fact_type = 'invoice' "
                "ORDER BY business_key, fact_version"
            )
        ).mappings().all()
    ]
    return _summarize_fact_periods(facts, entity_code=wanted)


__all__ = [
    "LegalEntityNotFoundError",
    "get_legal_entity_fact_periods",
    "_summarize_fact_periods",
]
