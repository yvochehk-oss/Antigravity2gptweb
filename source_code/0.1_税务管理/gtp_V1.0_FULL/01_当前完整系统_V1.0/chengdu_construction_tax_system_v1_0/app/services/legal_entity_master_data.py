"""Read-only legal-entity master data sourced from V3 Party SSOT."""
from __future__ import annotations

from typing import Any

from sqlalchemy import text


def list_legal_entities(
    db,
    *,
    active: bool = True,
    legal_entity: bool = True,
) -> list[dict[str, Any]]:
    """Return internal legal entities from parties + internal_entities only.

    ``parties`` owns canonical party identity/name while ``internal_entities``
    owns the internal canonical code and legal-entity classification.  Legacy
    ``entities`` is intentionally not consulted by this V3 read model.
    """
    predicates = ["p.party_type = 'internal'"]
    if active:
        predicates.extend(["p.active = TRUE", "ie.active = TRUE"])
    if legal_entity:
        predicates.append("ie.legal_entity = TRUE")

    rows = db.execute(
        text(
            "SELECT p.id AS party_id, ie.canonical_code, p.name AS legal_name "
            "FROM parties p "
            "JOIN internal_entities ie ON ie.party_id = p.id "
            f"WHERE {' AND '.join(predicates)} "
            "ORDER BY ie.canonical_code, p.id"
        )
    ).mappings().all()

    return [
        {
            "party_id": int(row["party_id"]),
            "canonical_code": str(row["canonical_code"] or "").strip().upper(),
            "legal_name": str(row["legal_name"] or "").strip(),
        }
        for row in rows
        if row["party_id"] is not None and row["canonical_code"] and row["legal_name"]
    ]


__all__ = ["list_legal_entities"]
