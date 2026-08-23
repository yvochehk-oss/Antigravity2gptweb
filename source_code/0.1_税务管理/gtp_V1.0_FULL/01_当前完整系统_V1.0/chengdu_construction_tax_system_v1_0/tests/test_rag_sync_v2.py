"""RAG-to-tax synchronization regression tests for the V1.0 entity contract."""
from __future__ import annotations

from decimal import Decimal

import pytest


def _sync_module():
    from app.routers import rag_sync

    return rag_sync


def _db():
    from app.db import SessionLocal

    return SessionLocal()


def _item(chunk_id: int, fields: dict, confidence: float = 0.99) -> dict:
    return {
        "source_chunk_id": chunk_id,
        "source_document_id": 9000 + chunk_id,
        "filename": f"rag-{chunk_id}.json",
        "confidence": confidence,
        "fields": fields,
    }


def _payment(chunk_id: int, date: str, amount: int, reference: str, *, note: str = "") -> dict:
    return _item(
        chunk_id,
        {
            "payment_date": date,
            "direction": "out",
            "payer_code": "A08",
            "counterparty_code": "B01",
            "amount": amount,
            "bank_reference": reference,
            "note": note,
        },
    )


def test_entity_resolution_rejects_virtual_and_rolls_branch_to_parent(seeded_app):
    rag_sync = _sync_module()
    db = _db()
    try:
        assert rag_sync._resolve_entity_identifier(db, raw="A04") == "A03"
        with pytest.raises(rag_sync.SyncReviewRequired, match="虚拟主体"):
            rag_sync._resolve_entity_code(db, code="A")
        with pytest.raises(rag_sync.SyncReviewRequired, match="虚拟"):
            rag_sync._resolve_party_code(db, raw="甲")
    finally:
        db.close()


def test_invoice_uses_real_external_party_and_validates_net(seeded_app):
    rag_sync = _sync_module()
    db = _db()
    try:
        mapped = rag_sync._map_invoice_fields(
            db,
            {
                "invoice_no": "RAG-INV-EXT-01",
                "invoice_date": "2026-08-01",
                "direction": "out",
                "seller_code": "A08",
                "buyer_code": "EXT-TF",
                "buyer_name": "成都市天府新区金融城投公司",
                "total_amount": 109,
                "vat_amount": 9,
            },
            1,
        )
        assert mapped["entity_code"] == "A08"
        assert mapped["counterparty_code"] == "EXT-TF"
        assert mapped["net"] == Decimal("100")

        with pytest.raises(rag_sync.SyncReviewRequired, match="不一致"):
            rag_sync._map_invoice_fields(
                db,
                {
                    "invoice_no": "RAG-INV-BAD-NET",
                    "invoice_date": "2026-08-01",
                    "direction": "out",
                    "seller_code": "A08",
                    "buyer_code": "EXT-TF",
                    "total_amount": 109,
                    "vat_amount": 9,
                    "net_amount": 97,
                },
                1,
            )
    finally:
        db.close()


def test_payer_account_is_resolved_only_by_bank_master(seeded_app):
    from app.models import Entity, EntityBankAccount

    rag_sync = _sync_module()
    db = _db()
    try:
        db.add(
            EntityBankAccount(
                entity_code="A08",
                account_no="6222000000000008",
                bank_name="测试银行",
            )
        )
        db.commit()
        mapped = rag_sync._map_cashflow_fields(
            db,
            {
                "payment_date": "2026-08-10",
                "direction": "out",
                "payer_account": "6222000000000008",
                "counterparty_code": "B01",
                "amount": 88,
                "bank_reference": "BANK-ACCOUNT-01",
            },
            1,
        )
        assert mapped["entity_code"] == "A08"

        # The same value is a tax id in the entity master, but is not a bank
        # account mapping and therefore cannot identify a payer here.
        entity_tax_id = db.query(Entity).filter(Entity.code == "A08").one().tax_id
        with pytest.raises(rag_sync.SyncReviewRequired, match="银行账号"):
            rag_sync._map_cashflow_fields(
                db,
                {
                    "payment_date": "2026-08-11",
                    "direction": "out",
                    "payer_account": entity_tax_id,
                    "counterparty_code": "B01",
                    "amount": 89,
                    "bank_reference": "BANK-ACCOUNT-02",
                },
                1,
            )
    finally:
        db.close()


def test_payment_fingerprint_keeps_same_direction_rows_and_is_idempotent(seeded_app, monkeypatch):
    from app.models import CashFlow

    rag_sync = _sync_module()
    items = [
        _payment(7101, "2026-08-12", 101, "PAY-7101"),
        _payment(7102, "2026-08-13", 202, "PAY-7102"),
    ]
    monkeypatch.setattr(
        rag_sync,
        "_call_rag_extract",
        lambda *args, **kwargs: {"extracted_items": items, "errors": [], "total_chunks": 2},
    )
    db = _db()
    try:
        first = rag_sync._do_sync(db, 1, 710, "", "", "payment", None, None, 30, "", "test")
        assert first.status == "SUCCESS"
        assert first.total_imported == 2
        assert len({
            row.source_fingerprint
            for row in db.query(CashFlow)
            .filter(CashFlow.source_fingerprint.is_not(None))
            .all()
        }) >= 2

        second = rag_sync._do_sync(db, 1, 711, "", "", "payment", None, None, 30, "", "test")
        assert second.status == "SUCCESS"
        assert second.total_imported == 0
        assert second.errors == []
    finally:
        db.close()


def test_tax_payment_goes_to_documentary_table_and_unresolved_is_pending(seeded_app, monkeypatch):
    from app.models import TaxLedger, TaxPaymentRecord

    rag_sync = _sync_module()
    db = _db()
    try:
        before_ledger = db.query(TaxLedger).count()
        monkeypatch.setattr(
            rag_sync,
            "_call_rag_extract",
            lambda *args, **kwargs: {
                "extracted_items": [
                    _item(
                        7201,
                        {
                            "tax_type": "VAT",
                            "tax_period": "2026-08",
                            "payment_date": "2026-08-14",
                            "taxpayer_name": "无法识别的公司",
                            "tax_amount": 12,
                            "receipt_no": "TAX-7201",
                        },
                    )
                ],
                "errors": [],
                "total_chunks": 1,
            },
        )
        pending = rag_sync._do_sync(db, 1, 720, "", "", "tax_payment", None, None, 30, "", "test")
        assert pending.status == "PENDING_REVIEW"
        assert pending.total_pending == 1
        assert db.query(TaxPaymentRecord).filter(TaxPaymentRecord.receipt_no == "TAX-7201").count() == 0
        assert db.query(TaxLedger).count() == before_ledger

        monkeypatch.setattr(
            rag_sync,
            "_call_rag_extract",
            lambda *args, **kwargs: {
                "extracted_items": [
                    _item(
                        7202,
                        {
                            "tax_type": "VAT",
                            "tax_period": "2026-08",
                            "payment_date": "2026-08-15",
                            "taxpayer_code": "A08",
                            "tax_amount": 13,
                            "receipt_no": "TAX-7202",
                        },
                    )
                ],
                "errors": [],
                "total_chunks": 1,
            },
        )
        success = rag_sync._do_sync(db, 1, 721, "", "", "tax_payment", None, None, 30, "", "test")
        assert success.status == "SUCCESS"
        assert db.query(TaxPaymentRecord).filter(TaxPaymentRecord.receipt_no == "TAX-7202").count() == 1
        assert db.query(TaxLedger).count() == before_ledger
    finally:
        db.close()


def test_item_savepoint_keeps_success_when_next_item_fails(seeded_app, monkeypatch):
    rag_sync = _sync_module()
    original_import = rag_sync._import_record
    items = [
        _payment(7301, "2026-08-16", 303, "PAY-7301"),
        _payment(7302, "2026-08-17", 404, "PAY-7302", note="boom"),
    ]

    def failing_import(db, project_id, extract_type, fields, *, mapped=None):
        if fields.get("note") == "boom":
            raise RuntimeError("synthetic row failure")
        return original_import(db, project_id, extract_type, fields, mapped=mapped)

    monkeypatch.setattr(rag_sync, "_import_record", failing_import)
    monkeypatch.setattr(
        rag_sync,
        "_call_rag_extract",
        lambda *args, **kwargs: {"extracted_items": items, "errors": [], "total_chunks": 2},
    )
    db = _db()
    try:
        result = rag_sync._do_sync(db, 1, 730, "", "", "payment", None, None, 30, "", "test")
        assert result.status == "PARTIAL"
        assert result.total_imported == 1
        assert result.total_pending == 0
        assert any("synthetic row failure" in error for error in result.errors)
        from app.models import CashFlow

        assert db.query(CashFlow).filter(CashFlow.source_fingerprint.is_not(None)).filter(
            CashFlow.bank_reference == "PAY-7301"
        ).count() == 1
        assert db.query(CashFlow).filter(CashFlow.bank_reference == "PAY-7302").count() == 0
    finally:
        db.close()
