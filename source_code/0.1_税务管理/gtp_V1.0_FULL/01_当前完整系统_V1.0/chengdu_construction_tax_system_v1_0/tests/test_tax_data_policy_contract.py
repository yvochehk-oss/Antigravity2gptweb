from __future__ import annotations

from datetime import date
from decimal import Decimal
import inspect

from app.domain.tax_data_policy import (
    ADVISORY_SOURCE_TAX_ENGINE,
    DATA_CLASS_ADVISORY,
    DATA_CLASS_FACT,
    FACT_SOURCE_RAG_POSTGRESQL,
)
from app.routers.tax_advisory import project_tax_advisory
from app.services import tax_advisory
from app.services.canonical_project_summary import resolve_project_transaction_price


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


def test_tax_policy_constants_lock_fact_and_advisory_sources():
    assert DATA_CLASS_FACT == "FACT"
    assert FACT_SOURCE_RAG_POSTGRESQL == "RAG_POSTGRESQL"
    assert DATA_CLASS_ADVISORY == "ADVISORY"
    assert ADVISORY_SOURCE_TAX_ENGINE == "TAX_ENGINE"


def test_actual_vat_and_cit_payments_are_rag_facts_through_period():
    rows = [
        {
            "fact_id": 11,
            "business_identity_key": "tax-payment:vat:11",
            "version_no": 2,
            "project_id": 7,
            "reporting_party_id": 101,
            "tax_type": "VAT",
            "tax_period": date(2025, 11, 1),
            "tax_event_date": date(2025, 11, 18),
            "tax_amount": Decimal("500.00"),
            "taxable_base": Decimal("5000.00"),
            "currency": "CNY",
            "event_type": "PREPAYMENT",
            "source_system": "rag",
            "external_reference": "VAT-001",
            "note": "",
        },
        {
            "fact_id": 12,
            "business_identity_key": "tax-payment:cit:12",
            "version_no": 1,
            "project_id": 7,
            "reporting_party_id": 101,
            "tax_type": "企业所得税",
            "tax_period": date(2025, 12, 1),
            "tax_event_date": date(2025, 12, 20),
            "tax_amount": Decimal("300.00"),
            "taxable_base": Decimal("1200.00"),
            "currency": "CNY",
            "event_type": "PAYMENT",
            "source_system": "rag",
            "external_reference": "CIT-001",
            "note": "",
        },
    ]

    class Db:
        sql = ""
        params = None

        def execute(self, stmt, params):
            self.sql = str(stmt)
            self.params = params
            return _Rows(rows)

    db = Db()
    result = tax_advisory.actual_tax_payment_facts(db, 7, period="2025-12")

    assert result["data_class"] == DATA_CLASS_FACT
    assert result["source"] == FACT_SOURCE_RAG_POSTGRESQL
    assert result["actual_occurred"] is True
    assert result["scope"] == "PROJECT_TO_DATE_THROUGH_PERIOD"
    assert result["totals_by_tax_family"] == {
        "CIT": Decimal("300.00"),
        "VAT": Decimal("500.00"),
    }
    assert all(item["data_class"] == DATA_CLASS_FACT for item in result["items"])
    assert all(item["actual_occurred"] is True for item in result["items"])
    assert "f.is_current=TRUE" in db.sql
    assert "f.validation_status='VALID'" in db.sql
    assert "tpf.tax_period<=:tax_period" in db.sql
    assert db.params["tax_period"] == date(2025, 12, 1)


def test_advisory_calculation_never_overwrites_actual_tax_facts(monkeypatch):
    actual = {
        "data_class": DATA_CLASS_FACT,
        "source": FACT_SOURCE_RAG_POSTGRESQL,
        "actual_occurred": True,
        "is_filing_basis": False,
        "scope": "PROJECT_TO_DATE_THROUGH_PERIOD",
        "through_period": "2025-12",
        "items": [],
        "totals_by_tax_family": {
            "VAT": Decimal("5.00"),
            "CIT": Decimal("3.00"),
        },
        "count": 0,
    }

    def fake_actual(db, project_id, *, reporting_party_id=None, period=None):
        assert project_id == 7
        assert reporting_party_id is None
        assert period == "2025-12"
        return actual

    def fake_accounting(db, project_id, *, as_of=None, **kwargs):
        assert project_id == 7
        assert as_of == date(2025, 12, 31)
        return {
            "status": "READY",
            "engine_version": "TEST",
            "recognition": {
                "recognized_revenue": Decimal("100.00"),
                "recognized_cost": Decimal("60.00"),
            },
            "book_tax": {
                "accounting_profit": Decimal("40.00"),
                "current_cit": Decimal("10.00"),
            },
            "boundary": {
                "signed_vat_position": Decimal("12.00"),
            },
            "lineage": {},
            "data_gaps": [],
        }

    monkeypatch.setattr(tax_advisory, "actual_tax_payment_facts", fake_actual)
    monkeypatch.setattr(tax_advisory, "build_project_accounting", fake_accounting)

    result = tax_advisory.build_tax_advisory(None, 7, period="2025-12")

    assert result["actual_tax_payments"] is actual
    assert result["actual_tax_payments"]["totals_by_tax_family"]["VAT"] == Decimal("5.00")
    assert result["actual_tax_payments"]["totals_by_tax_family"]["CIT"] == Decimal("3.00")

    advisory = result["advisory"]
    assert advisory["data_class"] == DATA_CLASS_ADVISORY
    assert advisory["source"] == ADVISORY_SOURCE_TAX_ENGINE
    assert advisory["actual_occurred"] is False
    assert advisory["is_filing_basis"] is False
    assert advisory["revenue"] == Decimal("100.00")
    assert advisory["cost"] == Decimal("60.00")
    assert advisory["profit"] == Decimal("40.00")
    assert advisory["vat"] == Decimal("12.00")
    assert advisory["cit"] == Decimal("10.00")

    assert result["differences"]["vat_advisory_minus_actual_paid"] == Decimal("7.00")
    assert result["differences"]["cit_advisory_minus_actual_paid"] == Decimal("7.00")
    assert result["principles"] == {
        "fact_source": FACT_SOURCE_RAG_POSTGRESQL,
        "advisory_source": ADVISORY_SOURCE_TAX_ENGINE,
        "tax_reads_source_files": False,
        "actual_tax_payments_are_facts": True,
        "advisory_never_overwrites_fact": True,
        "comparison_scope": "PROJECT",
    }


def test_project_advisory_api_does_not_expose_unmatched_legal_entity_scope():
    signature = inspect.signature(project_tax_advisory)
    assert "reporting_party_id" not in signature.parameters


def test_transaction_price_has_no_project_master_financial_fallback():
    class Db:
        def execute(self, _stmt):
            return _ScalarRows([])

    result = resolve_project_transaction_price(Db(), 7, contract_facts=[])

    assert result["status"] == "EMPTY"
    assert result["amount"] == Decimal("0")
    assert result["source"] == FACT_SOURCE_RAG_POSTGRESQL
    assert result["resolution_reason"] == "RAG_POSTGRESQL_NO_CONTRACT_PRICE_FACT"
