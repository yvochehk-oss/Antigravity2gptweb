from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.routers import collections
from app.services import canonical_ledger, legal_entity_scope
from app.services.canonical_ssot import consolidate_invoice_facts
from app.services.canonical_v3_bridge import CanonicalV3Bridge


def _fact(fid: int, project_id: int | None = 1, **payload):
    return {
        "fact_id": fid,
        "source_document_id": fid,
        "project_id": project_id,
        "fact_version": 1,
        "payload": payload,
    }


class _ScalarOneResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _MappingsResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _StatutoryDb:
    def __init__(self, ledger_row: dict):
        self.ledger_row = ledger_row
        self.calls: list[tuple[str, dict]] = []
        self.lineage_rows = [
            {
                "ledger_id": 501,
                "component_id": 1,
                "component_type": "OPENING_INPUT_CREDIT",
                "amount": Decimal("20.00"),
                "output_vat_event_id": None,
                "input_vat_claim_id": None,
                "tax_prepayment_fact_id": None,
                "prior_ledger_id": 400,
                "opening_balance_seed_id": None,
                "output_invoice_fact_id": None,
                "output_source_document_id": None,
                "input_invoice_fact_id": None,
                "input_source_document_id": None,
                "opening_source_document_id": None,
            },
            {
                "ledger_id": 501,
                "component_id": 2,
                "component_type": "OUTPUT_VAT",
                "amount": Decimal("100.00"),
                "output_vat_event_id": 601,
                "input_vat_claim_id": None,
                "tax_prepayment_fact_id": None,
                "prior_ledger_id": None,
                "opening_balance_seed_id": None,
                "output_invoice_fact_id": 701,
                "output_source_document_id": 801,
                "input_invoice_fact_id": None,
                "input_source_document_id": None,
                "opening_source_document_id": None,
            },
            {
                "ledger_id": 501,
                "component_id": 3,
                "component_type": "INPUT_VAT",
                "amount": Decimal("30.00"),
                "output_vat_event_id": None,
                "input_vat_claim_id": 602,
                "tax_prepayment_fact_id": None,
                "prior_ledger_id": None,
                "opening_balance_seed_id": None,
                "output_invoice_fact_id": None,
                "output_source_document_id": None,
                "input_invoice_fact_id": 702,
                "input_source_document_id": 802,
                "opening_source_document_id": None,
            },
            {
                "ledger_id": 501,
                "component_id": 4,
                "component_type": "TAX_PREPAYMENT",
                "amount": Decimal("10.00"),
                "output_vat_event_id": None,
                "input_vat_claim_id": None,
                "tax_prepayment_fact_id": 603,
                "prior_ledger_id": None,
                "opening_balance_seed_id": None,
                "output_invoice_fact_id": None,
                "output_source_document_id": None,
                "input_invoice_fact_id": None,
                "input_source_document_id": None,
                "opening_source_document_id": None,
            },
        ]

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        bound = dict(params or {})
        self.calls.append((sql, bound))
        if sql.startswith("SELECT count(*)"):
            return _ScalarOneResult(1)
        if "FROM entity_vat_ledger_components AS c" in sql:
            return _MappingsResult(self.lineage_rows)
        if "FROM entity_vat_ledgers AS l" in sql:
            return _MappingsResult([self.ledger_row])
        raise AssertionError(f"unexpected SQL in statutory VAT reader: {sql}")


def _official_ledger_row(**overrides):
    row = {
        "id": 501,
        "calculation_run_id": 9001,
        "reporting_party_id": 101,
        "tax_period": date(2026, 8, 1),
        "opening_input_credit": Decimal("20.00"),
        "output_vat": Decimal("100.00"),
        "input_vat": Decimal("30.00"),
        "tax_prepayment": Decimal("10.00"),
        "vat_payable_before_prepayment": Decimal("50.00"),
        "closing_input_credit": Decimal("0.00"),
        "vat_payable_after_prepayment": Decimal("40.00"),
        "unapplied_tax_prepayment": Decimal("0.00"),
        "run_kind": "ORIGINAL",
        "run_status": "SUCCEEDED",
        "ruleset_version": "vat-2026",
        "input_snapshot_sha256": "a" * 64,
        "result_sha256": "b" * 64,
        "period_state": "OPEN",
        "entity_id": 101,
        "entity_code": "A08",
        "business_role": "施工",
        "legal_entity": True,
        "entity_name": "A08 测试法人",
    }
    row.update(overrides)
    return row


def test_statutory_vat_reader_uses_current_succeeded_party_ledger_and_typed_lineage():
    db = _StatutoryDb(_official_ledger_row())

    items, total = CanonicalV3Bridge.list_official_entity_vat_ledgers(
        db,
        entity_code="a08",
        tax_period=date(2026, 8, 1),
        page=1,
        page_size=50,
    )

    assert total == 1
    assert len(items) == 1
    row = items[0]
    assert row["scope"] == "LEGAL_ENTITY_STATUTORY"
    assert row["is_filing_basis"] is True
    assert row["source_of_truth"] == "entity_vat_ledgers"
    assert row["entity_id"] == row["reporting_party_id"] == 101
    assert row["entity_code"] == "A08"
    assert row["run_status"] == "SUCCEEDED"
    assert row["period"] == "2026-08"
    assert row["vat_payable_before_prepayment"] == Decimal("50.00")
    assert row["vat_payable_before_prepayment"] == max(
        row["output_vat"] - row["input_vat"] - row["opening_input_credit"],
        Decimal("0"),
    )
    assert not (
        row["vat_payable_before_prepayment"] > 0
        and row["closing_input_credit"] > 0
    )
    assert row["legal_entity_vat_identity_ok"] is True
    assert row["data_status"] == "READY"
    assert row["trusted"] is True

    components = {item["component_type"]: item for item in row["lineage_components"]}
    assert set(components) == {
        "OPENING_INPUT_CREDIT",
        "OUTPUT_VAT",
        "INPUT_VAT",
        "TAX_PREPAYMENT",
    }
    assert components["OPENING_INPUT_CREDIT"]["prior_ledger_id"] == 400
    assert components["OUTPUT_VAT"]["output_vat_event_id"] == 601
    assert components["OUTPUT_VAT"]["output_invoice_fact_id"] == 701
    assert components["OUTPUT_VAT"]["output_source_document_id"] == 801
    assert components["INPUT_VAT"]["input_vat_claim_id"] == 602
    assert components["INPUT_VAT"]["input_invoice_fact_id"] == 702
    assert components["INPUT_VAT"]["input_source_document_id"] == 802
    assert components["TAX_PREPAYMENT"]["tax_prepayment_fact_id"] == 603

    ledger_sql = "\n".join(sql for sql, _params in db.calls if "entity_vat_ledgers AS l" in sql)
    assert "r.run_status = 'SUCCEEDED'" in ledger_sql
    assert "s.current_run_id = l.calculation_run_id" in ledger_sql
    assert "JOIN internal_entities AS ie ON ie.party_id = l.reporting_party_id" in ledger_sql
    assert "JOIN parties AS p ON p.id = ie.party_id" in ledger_sql
    assert db.calls[0][1]["entity_code"] == "A08"
    assert db.calls[0][1]["tax_period"] == date(2026, 8, 1)


def test_statutory_vat_reader_degrades_impossible_vat_identity():
    db = _StatutoryDb(
        _official_ledger_row(
            vat_payable_before_prepayment=Decimal("55.00"),
            closing_input_credit=Decimal("5.00"),
        )
    )

    items, _ = CanonicalV3Bridge.list_official_entity_vat_ledgers(db)

    row = items[0]
    assert row["legal_entity_vat_identity_ok"] is False
    assert row["data_status"] == "DEGRADED"
    assert row["trusted"] is False
    assert row["data_gaps"] == ["LEGAL_ENTITY_VAT_IDENTITY_FAILED"]


def test_legal_entity_projection_aggregates_across_projects_and_keeps_internal_trade():
    facts = [
        _fact(
            1,
            1,
            seller_entity_code="A08",
            buyer_entity_code="B01",
            net_amount="100.00",
            vat_amount="9.00",
            deductible=True,
        ),
        _fact(
            2,
            2,
            seller_entity_code="B01",
            buyer_entity_code="A08",
            net_amount="50.00",
            vat_amount="4.50",
            deductible=True,
        ),
        _fact(
            3,
            2,
            seller_entity_code="A08",
            buyer_entity_code="EXT-CUSTOMER",
            net_amount="200.00",
            vat_amount="18.00",
            deductible=True,
        ),
        _fact(
            4,
            3,
            seller_entity_code="C01",
            buyer_entity_code="EXT-OTHER",
            net_amount="999.00",
            vat_amount="89.91",
            deductible=True,
        ),
    ]

    result = legal_entity_scope._aggregate_facts_for_entity(
        facts,
        entity_code="A08",
        internal_codes={"A08", "B01", "C01"},
        project_meta={
            1: {"code": "P1", "name": "项目一"},
            2: {"code": "P2", "name": "项目二"},
            3: {"code": "P3", "name": "无关项目"},
        },
    )

    assert result["scope"] == "LEGAL_ENTITY_PROJECTION"
    assert result["is_filing_basis"] is False
    assert result["official_vat_ledger"] == "entity_vat_ledgers"
    assert result["revenue"] == Decimal("300.00")
    assert result["book_cost_projection"] == Decimal("50.00")
    assert result["output_vat"] == Decimal("27.00")
    assert result["input_vat"] == Decimal("4.50")
    assert result["deductible_input_vat"] == Decimal("4.50")
    assert result["internal_trade_net"] == Decimal("150.00")
    assert result["internal_trade_vat"] == Decimal("13.50")
    assert result["fact_ids"] == [1, 2, 3]
    assert [item["project_id"] for item in result["project_contributions"]] == [1, 2]
    assert all(item["project_id"] != 3 for item in result["project_contributions"])


def test_project_boundary_exposes_tri_state_vat_identity_and_never_becomes_filing_basis(monkeypatch):
    facts = [
        _fact(1, 1, seller_entity_code="A08", buyer_entity_code="B01", net_amount="300.00", vat_amount="27.00", deductible=True),
        _fact(2, 1, seller_entity_code="EXT-MAT", buyer_entity_code="A08", net_amount="100.00", vat_amount="13.00", deductible=True),
        _fact(3, 1, seller_entity_code="EXT-SVC", buyer_entity_code="A08", net_amount="40.00", vat_amount="6.00", deductible=False),
        _fact(4, 1, seller_entity_code="EXT-PENDING", buyer_entity_code="A08", net_amount="20.00", vat_amount="3.00", deductible=None),
        _fact(5, 1, seller_entity_code="A08", buyer_entity_code="EXT-CUSTOMER", net_amount="80.00", vat_amount="8.00", deductible=True),
    ]

    monkeypatch.setattr(
        canonical_ledger,
        "_party_master",
        lambda _db: ({}, {"A08", "B01"}, {"EXT-MAT", "EXT-SVC", "EXT-PENDING", "EXT-CUSTOMER"}),
    )
    monkeypatch.setattr(canonical_ledger, "load_current_facts", lambda _db, _project_id, _fact_type: facts)

    result = canonical_ledger.project_tax_analysis_summary(object(), 1)

    assert result["scope"] == "PROJECT_BOUNDARY"
    assert result["is_filing_basis"] is False
    assert result["in_invoice_vat"] == 22.0
    assert result["deductible_input_vat"] == 13.0
    assert result["nondeductible_input_vat"] == 6.0
    assert result["pending_input_vat"] == 3.0
    assert result["input_vat_accounted"] == 22.0
    assert result["input_vat_unaccounted"] == 0.0
    assert result["in_invoice_vat"] == result["input_vat_accounted"] + result["input_vat_unaccounted"]
    assert result["input_vat_identity_ok"] is True
    assert result["internal_eliminated_net"] == 300.0
    assert result["internal_eliminated_vat"] == 27.0
    assert result["signed_vat_position"] == -5.0
    assert "INPUT_VAT_DEDUCTIBILITY_NEEDS_REVIEW" in result["data_gaps"]


def test_project_boundary_is_invariant_to_internal_transfer_price_and_extra_layers():
    external_input = _fact(
        1,
        1,
        seller_entity_code="EXT-MAT",
        buyer_entity_code="A08",
        net_amount="100.00",
        vat_amount="13.00",
        deductible=True,
    )
    external_output_b01 = _fact(
        3,
        1,
        seller_entity_code="B01",
        buyer_entity_code="EXT-CUSTOMER",
        net_amount="200.00",
        vat_amount="18.00",
        deductible=True,
    )
    base = consolidate_invoice_facts(
        [
            external_input,
            _fact(2, 1, seller_entity_code="A08", buyer_entity_code="B01", net_amount="120.00", vat_amount="10.80", deductible=True),
            external_output_b01,
        ],
        {"A08", "B01"},
    )
    repriced = consolidate_invoice_facts(
        [
            external_input,
            _fact(2, 1, seller_entity_code="A08", buyer_entity_code="B01", net_amount="999.00", vat_amount="89.91", deductible=True),
            external_output_b01,
        ],
        {"A08", "B01"},
    )
    layered = consolidate_invoice_facts(
        [
            external_input,
            _fact(2, 1, seller_entity_code="A08", buyer_entity_code="B01", net_amount="999.00", vat_amount="89.91", deductible=True),
            _fact(4, 1, seller_entity_code="B01", buyer_entity_code="C01", net_amount="888.00", vat_amount="79.92", deductible=True),
            _fact(5, 1, seller_entity_code="C01", buyer_entity_code="EXT-CUSTOMER", net_amount="200.00", vat_amount="18.00", deductible=True),
        ],
        {"A08", "B01", "C01"},
    )

    invariant_fields = (
        "external_revenue",
        "external_cost",
        "boundary_margin",
        "boundary_output_net",
        "boundary_output_vat",
        "boundary_input_net",
        "boundary_input_vat",
        "deductible_input_vat",
        "signed_vat_position",
    )
    for field in invariant_fields:
        assert repriced[field] == base[field]
        assert layered[field] == base[field]
    assert repriced["internal_eliminated"] != base["internal_eliminated"]
    assert layered["internal_eliminated"] > repriced["internal_eliminated"]


class _ProjectRouteDb:
    def get(self, _model, project_id):
        return SimpleNamespace(id=project_id, project_code="P-007", code="P-007", name="测试项目")

    def rollback(self):
        return None

    def close(self):
        return None


def test_dual_scope_api_contracts_do_not_cross_filing_boundary(monkeypatch):
    official_item = {
        "id": "501",
        "scope": "LEGAL_ENTITY_STATUTORY",
        "is_filing_basis": True,
        "source_of_truth": "entity_vat_ledgers",
        "legal_entity_vat_identity_ok": True,
    }
    monkeypatch.setattr(
        CanonicalV3Bridge,
        "list_official_entity_vat_ledgers",
        staticmethod(lambda _db, **_kwargs: ([official_item], 1)),
    )
    entity_envelope = collections._build_entity_tax_ledger_envelope(
        object(),
        period="2026-08",
        entity="A08",
        page=1,
        page_size=50,
    )
    assert entity_envelope["scope"] == "LEGAL_ENTITY_STATUTORY"
    assert entity_envelope["is_filing_basis"] is True
    assert entity_envelope["source_of_truth"] == "entity_vat_ledgers"

    project_summary = {
        "status": "READY",
        "scope": "PROJECT_BOUNDARY",
        "is_filing_basis": False,
        "out_invoice_net": 80.0,
        "out_invoice_vat": 8.0,
        "in_invoice_net": 160.0,
        "in_invoice_vat": 22.0,
        "deductible_input_vat": 13.0,
        "nondeductible_input_vat": 6.0,
        "pending_input_vat": 3.0,
        "signed_vat_position": -5.0,
        "internal_eliminated_net": 300.0,
        "internal_eliminated_vat": 27.0,
        "input_vat_accounted": 22.0,
        "input_vat_unaccounted": 0.0,
        "input_vat_identity_ok": True,
        "real_cost": 166.0,
        "invoice_count": 5,
        "source_of_truth": "analytics_canonical_facts_current",
        "real_cost_basis": "canonical_external_invoices",
        "data_gaps": [],
    }
    monkeypatch.setattr(collections, "SessionLocal", lambda: _ProjectRouteDb())
    monkeypatch.setattr(collections, "project_tax_analysis_summary", lambda *_args, **_kwargs: project_summary)

    project_envelope = collections.project_tax_analysis(
        project_id=7,
        period=None,
        entity=None,
        entity_code=None,
        _user=object(),
    )
    assert project_envelope["scope"] == "PROJECT_BOUNDARY"
    assert project_envelope["is_filing_basis"] is False
    assert project_envelope["source_of_truth"] == "analytics_canonical_facts_current"
    item = project_envelope["items"][0]
    assert item["scope"] == "PROJECT_BOUNDARY"
    assert item["is_filing_basis"] is False
    assert item["nondeductible_input_vat"] == 6.0
    assert item["pending_input_vat"] == 3.0
    assert item["signed_vat_position"] == -5.0
    assert item["input_vat_identity_ok"] is True
    assert "vat_payable" not in item
    assert "vat_payable_after_prepayment" not in item


def test_collections_permanently_blocks_legacy_taxledger_read_chain():
    source = Path(collections.__file__).read_text(encoding="utf-8")

    assert "select(TaxLedger)" not in source
    assert "TaxLedger" not in source
    assert "CanonicalV3Bridge.list_official_entity_vat_ledgers" in source
    assert '"/api/entity-tax-ledger"' in source
    assert '"/api/project-tax-analysis"' in source
    assert 'status_value="DEPRECATED"' in source
