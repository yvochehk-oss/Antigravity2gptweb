from pathlib import Path

from app.services.canonical_ledger import (
    aggregate_counterparties_from_facts,
    serialize_contract_fact,
    serialize_invoice_fact,
    serialize_payment_fact,
)


def _fact(fid: int, fact_type: str, payload: dict) -> dict:
    return {
        "fact_id": fid,
        "source_document_id": 1000 + fid,
        "project_id": 1,
        "fact_type": fact_type,
        "business_key": f"{fact_type}:{fid}",
        "fact_version": 1,
        "accepted_at": "2026-09-01T00:00:00+00:00",
        "payload": payload,
    }


def test_phase2_serializers_preserve_lineage_and_canonical_party_codes() -> None:
    names = {
        "A08": "系统内 A08",
        "EB": "攀钢集团特种钢材直销部",
        "ED": "重庆巨力重型起重设备吊装公司",
    }
    contract = serialize_contract_fact(
        _fact(
            1,
            "contract",
            {
                "contract_no": "TF-A08-EXT-CRANE",
                "party_a_entity_code": "A08",
                "party_b_entity_code": "ED",
                "total_amount": 1000,
            },
        ),
        names,
    )
    invoice = serialize_invoice_fact(
        _fact(
            2,
            "invoice",
            {
                "invoice_no": "INV-EB-1",
                "direction": "in",
                "seller_entity_code": "EB",
                "buyer_entity_code": "A08",
                "net_amount": 100,
                "vat_amount": 13,
                "total_amount": 113,
            },
        ),
        names,
    )
    payment = serialize_payment_fact(
        _fact(
            3,
            "payment",
            {
                "payment_date": "2026-09-01",
                "direction": "out",
                "payer_entity_code": "A08",
                "payee_entity_code": "ED",
                "amount": 500,
            },
        ),
        names,
    )

    assert contract["party_a_code"] == "A08"
    assert contract["party_b_code"] == "ED"
    assert invoice["seller_code"] == "EB"
    assert invoice["buyer_code"] == "A08"
    assert payment["payer_code"] == "A08"
    assert payment["payee_code"] == "ED"
    for item in (contract, invoice, payment):
        assert item["source"] == "analytics_canonical_facts_current"
        assert item["read_only"] is True
        assert item["fact_id"] > 0
        assert item["source_document_id"] > 0


def test_phase2_counterparty_aggregation_uses_boundary_cost_not_internal_turnover() -> None:
    contracts = [
        _fact(
            1,
            "contract",
            {
                "party_a_entity_code": "A08",
                "party_b_entity_code": "ED",
                "total_amount": 500,
            },
        )
    ]
    invoices = [
        _fact(
            2,
            "invoice",
            {
                "direction": "in",
                "seller_entity_code": "EB",
                "buyer_entity_code": "A08",
                "net_amount": 100,
                "vat_amount": 13,
                "deductible": True,
            },
        ),
        _fact(
            3,
            "invoice",
            {
                "direction": "out",
                "seller_entity_code": "A08",
                "buyer_entity_code": "B03",
                "net_amount": 120,
                "vat_amount": 10.8,
                "deductible": True,
            },
        ),
        _fact(
            4,
            "invoice",
            {
                "direction": "in",
                "seller_entity_code": "EA",
                "buyer_entity_code": "A08",
                "net_amount": 50,
                "vat_amount": 3,
                "deductible": False,
            },
        ),
    ]
    payments = [
        _fact(
            5,
            "payment",
            {
                "direction": "out",
                "payer_entity_code": "A08",
                "payee_entity_code": "ED",
                "amount": 300,
            },
        )
    ]
    names = {
        "A08": "A08",
        "B03": "B03",
        "EA": "EA",
        "EB": "EB",
        "ED": "ED",
    }
    rows = aggregate_counterparties_from_facts(
        contracts,
        invoices,
        payments,
        names=names,
        internal_codes={"A08", "B03"},
        external_codes={"EA", "EB", "ED"},
    )
    by_code = {row["party_code"]: row for row in rows}

    assert by_code["EB"]["real_cost_count"] == 1
    assert by_code["EB"]["real_cost_amount"] == 100
    assert by_code["EA"]["real_cost_count"] == 1
    assert by_code["EA"]["real_cost_amount"] == 53
    assert by_code["A08"]["real_cost_amount"] == 0
    assert by_code["B03"]["real_cost_amount"] == 0
    assert by_code["ED"]["contract_amount"] == 500
    assert by_code["ED"]["cashflow_out_amount"] == 300
    assert by_code["A08"]["isInternal"] is True
    assert by_code["EA"]["isInternal"] is False


def test_phase2_direct_read_service_contains_no_legacy_ledger_query() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "canonical_ledger.py"
    ).read_text(encoding="utf-8").lower()

    assert "analytics_canonical_facts_current" in source
    assert "from contracts" not in source
    assert "from invoices" not in source
    assert "from cashflows" not in source
    assert "from cash_flows" not in source
    assert "select(contract" not in source
    assert "select(invoice" not in source
    assert "select(cashflow" not in source
