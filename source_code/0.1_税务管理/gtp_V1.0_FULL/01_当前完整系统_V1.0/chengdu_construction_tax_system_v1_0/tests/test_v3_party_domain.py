"""Unit tests for deterministic V3 Party/Taxpayer resolution."""
from __future__ import annotations

from datetime import date

import pytest

from app.domain.party.resolver import (
    PartyResolutionError,
    TaxProfileWindow,
    assert_acyclic_parent_map,
    resolve_reporting_party,
)


def _profile(
    party_id: int,
    reporting_party_id: int,
    *,
    start: str = "2026-01-01",
    end: str | None = None,
    reviewed: bool = True,
    tax_type: str = "VAT",
) -> TaxProfileWindow:
    return TaxProfileWindow(
        party_id=party_id,
        tax_type=tax_type,
        reporting_party_id=reporting_party_id,
        effective_from=date.fromisoformat(start),
        effective_to=date.fromisoformat(end) if end else None,
        reviewed=reviewed,
    )


def test_resolve_reporting_party_a04_to_a03():
    profiles = [_profile(4, 3)]
    assert resolve_reporting_party(4, date(2026, 8, 1), profiles) == 3


def test_party_without_profile_reports_as_self():
    assert resolve_reporting_party(8, date(2026, 8, 1), ()) == 8


def test_unreviewed_profile_is_not_used_by_default():
    profiles = [_profile(4, 3, reviewed=False)]
    assert resolve_reporting_party(4, date(2026, 8, 1), profiles) == 4
    assert (
        resolve_reporting_party(
            4,
            date(2026, 8, 1),
            profiles,
            require_reviewed=False,
        )
        == 3
    )


def test_effective_period_controls_resolution():
    profiles = [
        _profile(4, 3, start="2026-01-01", end="2026-06-30"),
        _profile(4, 4, start="2026-07-01"),
    ]
    assert resolve_reporting_party(4, date(2026, 6, 30), profiles) == 3
    assert resolve_reporting_party(4, date(2026, 7, 1), profiles) == 4


def test_multiple_active_profiles_fail_closed():
    profiles = [
        _profile(4, 3, start="2026-01-01"),
        _profile(4, 2, start="2026-06-01"),
    ]
    with pytest.raises(PartyResolutionError, match="multiple active"):
        resolve_reporting_party(4, date(2026, 8, 1), profiles)


def test_reporting_party_cycle_fails_closed():
    profiles = [_profile(4, 3), _profile(3, 4)]
    with pytest.raises(PartyResolutionError, match="cycle"):
        resolve_reporting_party(4, date(2026, 8, 1), profiles)


def test_parent_chain_cycle_detection():
    assert_acyclic_parent_map({1: None, 2: 1, 3: 2})
    with pytest.raises(PartyResolutionError, match="parent cycle"):
        assert_acyclic_parent_map({1: 2, 2: 3, 3: 1})
