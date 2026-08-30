"""Pure tests for Task 06 Party backfill conflict rules."""
from __future__ import annotations

import json
from datetime import date

import pytest

from app.domain.party.resolver import TaxProfileWindow, resolve_reporting_party
from scripts.v3.party_backfill import (
    ExistingParty,
    LegacyParty,
    TaxProfileOverride,
    cycle_nodes,
    detect_static_conflicts,
    load_tax_profile_overrides,
)


def _internal(
    code: str,
    *,
    name: str | None = None,
    tax_id: str | None = None,
    active: bool = True,
    parent_code: str | None = None,
) -> LegacyParty:
    return LegacyParty(
        source_table="entities",
        source_id=1,
        code=code,
        name=name or code,
        short_name="",
        tax_id=tax_id,
        party_type="internal",
        active=active,
        business_role=code[0],
        legal_entity=True,
        parent_code=parent_code,
    )


def _external(
    code: str,
    *,
    name: str | None = None,
    tax_id: str | None = None,
    party_id: int | None = None,
) -> LegacyParty:
    return LegacyParty(
        source_table="external_parties",
        source_id=2,
        code=code,
        name=name or code,
        short_name="",
        tax_id=tax_id,
        party_type="external",
        active=True,
        party_id=party_id,
    )


def _types(conflicts):
    return {item.conflict_type for item in conflicts}


def test_code_collision_blocks_identity_mapping():
    conflicts = detect_static_conflicts(
        [_internal("A01", name="内部公司")],
        [_external("A01", name="外部公司")],
        {},
        {},
        {},
    )
    collision = next(item for item in conflicts if item.conflict_type == "CODE_COLLISION")
    assert collision.blocking is True
    assert collision.blocks_party_mapping is True


def test_shared_tax_id_is_review_item_not_identity_merge():
    conflicts = detect_static_conflicts(
        [
            _internal("A01", tax_id="91510000SHARED"),
            _internal("A02", tax_id="91510000SHARED"),
        ],
        [],
        {},
        {},
        {},
    )
    shared = next(item for item in conflicts if item.conflict_type == "SHARED_TAX_ID")
    assert shared.blocking is False
    assert shared.blocks_party_mapping is False


def test_same_name_multiple_tax_ids_requires_review():
    conflicts = detect_static_conflicts(
        [_internal("A01", name="同名公司", tax_id="TAX-A")],
        [_external("EXT-01", name="同名公司", tax_id="TAX-B")],
        {},
        {},
        {},
    )
    assert "NAME_MULTIPLE_TAX_IDS" in _types(conflicts)
    item = next(x for x in conflicts if x.conflict_type == "NAME_MULTIPLE_TAX_IDS")
    assert item.blocks_party_mapping is True


def test_parent_cycle_nodes_are_detected():
    assert cycle_nodes({"A01": "A02", "A02": "A03", "A03": "A01"}) == {
        "A01",
        "A02",
        "A03",
    }
    assert cycle_nodes({"A01": None, "A02": "A01"}) == set()


def test_nonself_tax_profile_requires_review():
    override = TaxProfileOverride(
        party_code="A04",
        tax_type="VAT",
        reporting_party_code="A03",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        rule_version="manual-1",
        source="review-sheet",
        reviewed=False,
    )
    conflicts = detect_static_conflicts(
        [_internal("A03"), _internal("A04")],
        [],
        {},
        {},
        {("A04", "VAT"): override},
    )
    assert "TAX_PROFILE_REVIEW_REQUIRED" in _types(conflicts)


def test_reviewed_a04_to_a03_resolves_deterministically():
    override = TaxProfileOverride(
        party_code="A04",
        tax_type="VAT",
        reporting_party_code="A03",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        rule_version="manual-1",
        source="review-sheet",
        reviewed=True,
    )
    conflicts = detect_static_conflicts(
        [_internal("A03"), _internal("A04")],
        [],
        {},
        {},
        {("A04", "VAT"): override},
    )
    assert "TAX_PROFILE_REVIEW_REQUIRED" not in _types(conflicts)

    profiles = (
        TaxProfileWindow(
            party_id=4,
            tax_type="VAT",
            reporting_party_id=3,
            effective_from=date(2026, 1, 1),
            reviewed=True,
        ),
    )
    assert resolve_reporting_party(4, date(2026, 8, 30), profiles) == 3


def test_existing_party_name_change_is_not_silently_overwritten():
    existing = ExistingParty(
        party_id=10,
        code="A01",
        name="旧名称",
        party_type="internal",
        active=True,
    )
    conflicts = detect_static_conflicts(
        [_internal("A01", name="新名称")],
        [],
        {"A01": existing},
        {10: existing},
        {},
    )
    item = next(x for x in conflicts if x.conflict_type == "NAME_CHANGED")
    assert item.blocking is True
    assert item.blocks_party_mapping is True


def test_tax_profile_manifest_rejects_duplicate_key(tmp_path):
    payload = {
        "profiles": [
            {
                "party_code": "A04",
                "tax_type": "VAT",
                "reporting_party_code": "A03",
                "effective_from": "2026-01-01",
                "reviewed": True,
            },
            {
                "party_code": "A04",
                "tax_type": "VAT",
                "reporting_party_code": "A03",
                "effective_from": "2026-01-01",
                "reviewed": True,
            },
        ]
    }
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate tax profile override"):
        load_tax_profile_overrides(path)
