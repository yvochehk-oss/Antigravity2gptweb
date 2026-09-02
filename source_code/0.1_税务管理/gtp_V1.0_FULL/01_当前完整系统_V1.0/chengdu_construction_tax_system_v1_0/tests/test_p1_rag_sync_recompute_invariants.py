"""Verification suite for the 4 P1 Architecture Invariants:
1. [P1-1] TAX_CERT isolation from contract flow
2. [P1-2] External party post-parse idempotent normalization (ensure_external_party)
3. [P1-3] No-Silent-Drop accounting integrity
4. [P1-4] One-click sync-and-recompute endpoint orchestration
"""
from decimal import Decimal
import json
import pytest
from sqlalchemy import select, text
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    Contract,
    Entity,
    ExternalParty,
    Invoice,
    Project,
    SyncLog,
    SyncPending,
    TaxPaymentRecord,
    User,
)
from app.auth import issue_jwt
from app.routers.rag_sync import ensure_external_party, _resolve_party_code, _do_sync_background
from app.main import app


def test_p1_1_tax_cert_type_guard_skips_in_contract_flow(seeded_app, monkeypatch):
    """[P1-1] TAX_CERT 完税凭证绝不允许作为合同待复核项入库。"""
    db = SessionLocal()
    try:
        proj = db.get(Project, 1)
        assert proj is not None

        sync_log = SyncLog(
            project_id=1,
            sync_type="contract",
            rag_project_id=1,
            status="RUNNING",
        )
        db.add(sync_log)
        db.commit()

        mock_rag_items = [
            {
                "source_chunk_id": 101,
                "source_document_id": 201,
                "filename": "TAX_CERT_A08_202301_总承包合同印花税完税证明_电子税票.jpg",
                "confidence": 0.95,
                "fields": {
                    "contract_no": "TAX-CERT-NOT-CONTRACT",
                    "total_amount": 50000,
                },
            },
            {
                "source_chunk_id": 102,
                "source_document_id": 202,
                "filename": "CDTF-MAIN-2023-01_建筑工程施工总承包合同.pdf",
                "confidence": 0.98,
                "fields": {
                    "contract_no": "CDTF-MAIN-2023-01",
                    "party_a_code": "A08",
                    "party_b_code": "EXT-TEST-SUPPLIER",
                    "party_b_name": "四川宏达建筑物资有限公司",
                    "party_b_tax_id": "91510100MA6TEST001",
                    "total_amount": 1000000,
                },
            },
        ]

        monkeypatch.setattr(
            "app.routers.rag_sync._call_rag_extract",
            lambda *args, **kwargs: {"extracted_items": mock_rag_items, "total_chunks": 2, "errors": []},
        )

        _do_sync_background(
            sync_log_id=sync_log.id,
            project_id=1,
            rag_project_id=1,
            rag_url="http://127.0.0.1:8001",
            rag_api_key="test_key",
            extract_type="contract",
            period_start=None,
            period_end=None,
            top_k=30,
        )

        db.expire_all()
        # Contract pending should NOT contain TAX_CERT
        pendings = db.query(SyncPending).filter(SyncPending.sync_log_id == sync_log.id).all()
        for p in pendings:
            assert not p.filename.startswith("TAX_CERT_"), f"TAX_CERT leaked into pending: {p.filename}"
    finally:
        db.close()


def test_p1_2_ensure_external_party_idempotent_normalization(seeded_app):
    """[P1-2] 外部主体 post-parse 幂等归一化。"""
    db = SessionLocal()
    try:
        # 1. 首次遇到新外部主体，自动建档
        code1 = ensure_external_party(
            db,
            name="四川华西特种设备租赁有限公司",
            tax_id="91510100MA6TEST999",
        )
        assert code1 is not None
        assert code1.startswith("EXT-")
        db.flush()

        party = db.query(ExternalParty).filter(ExternalParty.code == code1).first()
        assert party is not None
        assert party.name == "四川华西特种设备租赁有限公司"

        # 2. 幂等再次调用，返回相同 code，不报错不重复创建
        code2 = ensure_external_party(
            db,
            name="四川华西特种设备租赁有限公司",
            tax_id="91510100MA6TEST999",
        )
        assert code2 == code1

        # 3. 别名主体自动映射
        code3 = ensure_external_party(
            db,
            name="攀钢集团攀枝花钢铁钒物资销售有限公司",
        )
        assert code3 == "EB"

        resolved = _resolve_party_code(
            db,
            name="攀钢集团攀枝花钢铁钒物资销售有限公司",
        )
        assert resolved == "EB"
    finally:
        db.close()


def test_p1_3_p1_4_sync_and_recompute_endpoint_integrity(seeded_app, monkeypatch):
    """[P1-3 & P1-4] 一键同步与自动重算端点及 No-Silent-Drop 完整性恒等式校验。"""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "admin").first()
        access_token, _ = issue_jwt(user)
    finally:
        db.close()

    def mock_extract(url, key, rag_pid, extract_type, *args, **kwargs):
        if extract_type == "contract":
            return {
                "extracted_items": [
                    {
                        "source_chunk_id": 301,
                        "source_document_id": 401,
                        "filename": "CDTF-SUB-01_专业分包合同.pdf",
                        "confidence": 0.99,
                        "fields": {
                            "contract_no": "CDTF-SUB-01",
                            "party_a_code": "A08",
                            "party_b_name": "四川嘉陵劳务工程有限公司",
                            "party_b_tax_id": "91510100MA6TEST888",
                            "total_amount": 5000000,
                        },
                    }
                ],
                "total_chunks": 1,
                "errors": [],
            }
        elif extract_type == "invoice":
            return {
                "extracted_items": [
                    {
                        "source_chunk_id": 302,
                        "source_document_id": 402,
                        "filename": "INVOICE_202603_001.pdf",
                        "confidence": 1.0,
                        "fields": {
                            "invoice_no": "INV-202603-9999",
                            "invoice_date": "2026-03-15",
                            "period": "2026-03",
                            "seller_name": "四川嘉陵劳务工程有限公司",
                            "seller_tax_id": "91510100MA6TEST888",
                            "buyer_entity_code": "A08",
                            "buyer_tax_id": "91510100MA6A08TAX1",
                            "buyer_name": "成都天府锐宝建设工程有限公司",
                            "net_amount": 1000000,
                            "vat_amount": 90000,
                            "total_amount": 1090000,
                            "vat_rate": 0.09,
                            "validation_status": "VALID",
                            "evidence": {
                                "invoice_no": "INV-202603-9999",
                                "invoice_date": "2026-03-15",
                                "seller_name": "四川嘉陵劳务工程有限公司",
                                "seller_tax_id": "91510100MA6TEST888",
                                "buyer_name": "成都天府锐宝建设工程有限公司",
                                "buyer_tax_id": "91510100MA6A08TAX1",
                                "net_amount": "1000000",
                                "vat_amount": "90000",
                                "total_amount": "1090000",
                                "vat_rate": "0.09",
                            },
                            "arithmetic_validation": {
                                "gross_equals_net_plus_vat": True,
                                "vat_equals_net_times_rate": True,
                            },
                        },
                    }
                ],
                "total_chunks": 1,
                "errors": [],
            }
        return {"extracted_items": [], "total_chunks": 0, "errors": []}

    monkeypatch.setattr("app.routers.rag_sync._call_rag_extract", mock_extract)
    monkeypatch.setattr("app.routers.rag_sync.invalidate_facts", lambda *a, **kw: {"status": "ok"})

    client = TestClient(app)
    response = client.post(
        "/rag-sync/sync-and-recompute",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"project_id": 1, "extract_types": ["contract", "invoice"]},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] in ("SUCCESS", "PENDING_REVIEW")
    assert "no_silent_drop" in data
    nsd = data["no_silent_drop"]
    assert nsd["total_source"] == nsd["accepted"] + nsd["pending_review"] + nsd["duplicates"] + nsd["failed"]
    assert nsd["unaccounted"] == 0
    assert "recalculated_periods" in data
