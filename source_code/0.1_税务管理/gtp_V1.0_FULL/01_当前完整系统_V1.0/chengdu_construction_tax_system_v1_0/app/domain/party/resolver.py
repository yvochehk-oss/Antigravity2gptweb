"""Pure Party/Taxpayer resolution rules for V3.

These functions perform no I/O and never guess identity.  Database-backed code
must load reviewed profile rows first, then call these deterministic helpers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping


class PartyResolutionError(ValueError):
    """Raised when Party/Taxpayer master data is ambiguous or cyclic."""


@dataclass(frozen=True)
class TaxProfileWindow:
    party_id: int
    tax_type: str
    reporting_party_id: int
    effective_from: date
    effective_to: date | None = None
    reviewed: bool = False

    def contains(self, as_of_date: date) -> bool:
        return self.effective_from <= as_of_date and (
            self.effective_to is None or as_of_date <= self.effective_to
        )


def assert_acyclic_parent_map(parent_by_party: Mapping[int, int | None]) -> None:
    """Reject self/indirect parent cycles in an internal-entity projection."""
    for start in parent_by_party:
        seen: set[int] = set()
        current: int | None = start
        while current is not None:
            if current in seen:
                raise PartyResolutionError(f"parent cycle detected from party {start}")
            seen.add(current)
            current = parent_by_party.get(current)


def _active_profile(
    party_id: int,
    tax_type: str,
    as_of_date: date,
    profiles: Iterable[TaxProfileWindow],
    *,
    require_reviewed: bool,
) -> TaxProfileWindow | None:
    matches = [
        profile
        for profile in profiles
        if profile.party_id == party_id
        and profile.tax_type == tax_type
        and profile.contains(as_of_date)
        and (profile.reviewed or not require_reviewed)
    ]
    if len(matches) > 1:
        raise PartyResolutionError(
            f"multiple active {tax_type} profiles for party {party_id} on {as_of_date}"
        )
    return matches[0] if matches else None


def resolve_reporting_party(
    party_id: int,
    as_of_date: date,
    profiles: Iterable[TaxProfileWindow],
    *,
    tax_type: str = "VAT",
    require_reviewed: bool = True,
) -> int:
    """Resolve a business party to its final reporting taxpayer deterministically.

    A party with no effective profile reports as itself.  A reviewed A04→A03
    profile therefore resolves to A03; if A03 itself has another effective
    profile, the chain is followed.  Any cycle is a hard error.
    """
    materialized = tuple(profiles)
    current = int(party_id)
    seen: set[int] = set()

    while True:
        if current in seen:
            raise PartyResolutionError(
                f"reporting-party cycle detected for party {party_id}, tax_type={tax_type}"
            )
        seen.add(current)
        profile = _active_profile(
            current,
            tax_type,
            as_of_date,
            materialized,
            require_reviewed=require_reviewed,
        )
        if profile is None or profile.reporting_party_id == current:
            return current
        current = int(profile.reporting_party_id)
