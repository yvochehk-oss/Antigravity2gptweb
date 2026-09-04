from types import SimpleNamespace

from app.services.canonical_facts import build_candidate, infer_fact_type


def _doc(**overrides):
    values = {
        "id": 10,
        "project_id": 1,
        "file_hash": "a" * 64,
        "document_type": "contract",
        "entity_code": "A08",
        "counterparty_code": "EXT-CRANE",
        "contract_no": "TF-A08-EXT-CRANE",
        "invoice_no": "",
        "invoice_date": "",
        "tax_vat_input": 0,
        "tax_vat_output": 0,
        "metadata_confidence": 0.95,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_contract_document_promotes_canonical_external_identity() -> None:
    doc = _doc()
    candidate = build_candidate(
        doc,
        "contract",
        {"total_amount": 1000000, "party_b_name": "重庆重交大件起重吊装工程有限公司"},
    )
    assert candidate.status == "accepted"
    assert candidate.business_key == "TF-A08-EXT-CRANE"
    assert candidate.payload["party_a_entity_code"] == "A08"
    assert candidate.payload["party_b_entity_code"] == "ED01"
    assert candidate.validation_errors == []


def test_invoice_document_uses_document_identity_and_requires_validation() -> None:
    doc = _doc(
        document_type="invoice",
        counterparty_code="EXT-PG",
        invoice_no="INV-001",
        invoice_date="2026-08-31",
        tax_vat_input=130,
    )
    candidate = build_candidate(
        doc,
        "invoice",
        {
            "direction": "in",
            "net_amount": 1000,
            "vat_amount": 130,
            "total_amount": 1130,
            "validation_status": "VALID",
        },
    )
    assert candidate.status == "accepted"
    assert candidate.payload["buyer_entity_code"] == "A08"
    assert candidate.payload["seller_entity_code"] == "EB01"


def test_incomplete_fact_is_review_only() -> None:
    doc = _doc(counterparty_code="")
    candidate = build_candidate(doc, "contract", {"total_amount": 0})
    assert candidate.status == "needs_review"
    assert candidate.confidence < 1
    assert any("party_b_entity_code" in item for item in candidate.validation_errors)


def test_equipment_contract_is_contract_not_payment() -> None:
    assert infer_fact_type("equipment_contract") == "contract"


def test_payment_without_bank_reference_keeps_document_identity() -> None:
    fields = {
        "direction": "out",
        "payer_entity_code": "A08",
        "payee_entity_code": "ED",
        "payment_date": "2026-09-01",
        "amount": 1000,
    }
    first = build_candidate(_doc(id=101, document_type="payment"), "payment", fields)
    second = build_candidate(_doc(id=102, document_type="payment"), "payment", fields)
    assert first.status == "accepted"
    assert second.status == "accepted"
    assert first.business_key == "document:101:payment"
    assert second.business_key == "document:102:payment"


def test_invoice_business_key_is_scoped_by_canonical_seller() -> None:
    fields = {
        "direction": "in",
        "invoice_no": "00012345",
        "invoice_code": "5100",
        "net_amount": 1000,
        "vat_amount": 130,
        "total_amount": 1130,
        "validation_status": "VALID",
    }
    eb = build_candidate(
        _doc(document_type="invoice", counterparty_code="EB", invoice_no="00012345", tax_vat_input=130),
        "invoice",
        fields,
    )
    ea = build_candidate(
        _doc(document_type="invoice", counterparty_code="EA", invoice_no="00012345", tax_vat_input=130),
        "invoice",
        fields,
    )
    assert eb.status == "accepted"
    assert ea.status == "accepted"
    assert eb.business_key != ea.business_key
    assert eb.business_key.startswith("invoice:EB01:")
    assert ea.business_key.startswith("invoice:EA01:")
