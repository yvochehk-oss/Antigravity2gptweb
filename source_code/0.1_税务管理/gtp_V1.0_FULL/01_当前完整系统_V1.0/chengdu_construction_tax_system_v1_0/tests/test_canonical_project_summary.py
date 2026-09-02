from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.services.canonical_project_summary import resolve_project_transaction_price


def _fake_db(codes):
    class FakeDB:
        def execute(self, *_args, **_kwargs):
            class Result:
                def scalars(self):
                    class Scalars:
                        def all(self):
                            return codes
                    return Scalars()
            return Result()
    return FakeDB()


def test_resolver_prefers_known_main_contract(monkeypatch):
    facts = [
        {
            "fact_id": 1,
            "fact_version": 1,
            "business_key": "contract:CDTF-MAIN-2026-01",
            "payload": {
                "contract_no": "CDTF-MAIN-2026-01",
                "total_amount": "1450000000.00",
                "party_a_entity_code": "E0",
                "party_b_entity_code": "A08",
            },
        },
        {
            "fact_id": 2,
            "fact_version": 1,
            "business_key": "contract:internal",
            "payload": {
                "contract_no": "INTERNAL-01",
                "total_amount": "3083055938.94",
                "party_a_entity_code": "A08",
                "party_b_entity_code": "B03",
            },
        },
    ]
    monkeypatch.setattr("app.services.canonical_project_summary.load_current_facts", lambda _db, _pid, _type=None: facts)
    result = resolve_project_transaction_price(_fake_db(["A08", "B03"]), 15)
    assert result["amount"] == Decimal("1450000000.00")
    assert result["contract_no"] == "CDTF-MAIN-2026-01"


def test_resolver_accepts_equivalent_known_main_contract_aliases(monkeypatch):
    facts = [
        {
            "fact_id": 10,
            "fact_version": 1,
            "business_key": "contract:CDTF-MAIN-2023-01",
            "payload": {
                "contract_no": "CDTF-MAIN-2023-01",
                "total_amount": "1450000000.00",
                "party_a_entity_code": "E0",
                "party_b_entity_code": "A08",
            },
        },
        {
            "fact_id": 11,
            "fact_version": 2,
            "business_key": "contract:ZB-CD-TF",
            "payload": {
                "contract_no": "ZB-CD-TF",
                "total_amount": "1450000000.00",
                "party_a_entity_code": "E0",
                "party_b_entity_code": "A08",
            },
        },
        {
            "fact_id": 12,
            "fact_version": 3,
            "business_key": "contract:CDTF-MAIN-2026-01",
            "payload": {
                "contract_no": "CDTF-MAIN-2026-01",
                "total_amount": "1450000000.00",
                "party_a_entity_code": "E0",
                "party_b_entity_code": "A08",
            },
        },
    ]
    monkeypatch.setattr("app.services.canonical_project_summary.load_current_facts", lambda _db, _pid, _type=None: facts)
    result = resolve_project_transaction_price(_fake_db(["A08"]), 15)
    assert result["amount"] == Decimal("1450000000.00")
    assert result["fact_version"] == 3
    assert result["contract_no"] == "CDTF-MAIN-2026-01"


def test_resolver_fails_closed_when_boundary_contracts_are_ambiguous(monkeypatch):
    facts = [
        {"fact_id": 1, "fact_version": 1, "business_key": "c1", "payload": {"contract_no": "X-1", "total_amount": "100", "party_a_entity_code": "E0", "party_b_entity_code": "A08"}},
        {"fact_id": 2, "fact_version": 1, "business_key": "c2", "payload": {"contract_no": "X-2", "total_amount": "200", "party_a_entity_code": "EA", "party_b_entity_code": "A08"}},
    ]
    monkeypatch.setattr("app.services.canonical_project_summary.load_current_facts", lambda _db, _pid, _type=None: facts)
    with pytest.raises(ValueError, match="ambiguous canonical project transaction price"):
        resolve_project_transaction_price(_fake_db(["A08"]), 15)


def test_api_project_source_has_no_legacy_project_summary_call():
    source = Path("app/routers/api.py").read_text(encoding="utf-8")
    api_block = source[source.index('@router.get("/api/projects/{pid}")'):source.index('@router.get("/api/projects/{pid}/matching")')]
    assert "canonical_project_summary(db, pid)" in api_block
    assert "calc.project_summary(" not in api_block
    assert "project.contract_total" not in api_block
    assert "RealCost" not in api_block
    assert "Invoice" not in api_block
    assert "Progress" not in api_block


def test_phase4_transaction_price_is_canonical():
    source = Path("app/services/phase4_accounting.py").read_text(encoding="utf-8")
    assert "resolve_project_transaction_price" in source
    assert 'getattr(project, "contract_total"' not in source
    assert 'getattr(project, "contract_amount"' not in source
