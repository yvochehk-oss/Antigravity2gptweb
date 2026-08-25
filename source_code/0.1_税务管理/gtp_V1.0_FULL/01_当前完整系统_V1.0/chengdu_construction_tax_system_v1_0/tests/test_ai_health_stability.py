"""Regression coverage for PostgreSQL health-check and ledger stability."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest


def _mock_endpoint_ids(db) -> list[int]:
    from app.models import AIModelEndpoint

    return [
        endpoint.id
        for endpoint in db.query(AIModelEndpoint)
        .filter(
            AIModelEndpoint.enabled.is_(True),
            AIModelEndpoint.adapter == "mock",
        )
        .order_by(AIModelEndpoint.id)
        .limit(2)
        .all()
    ]


def _new_batch(db, endpoint_ids: list[int], scopes: list[str]):
    from app.ai.timeutil import now_iso
    from app.models import AIReviewBatch

    batch = AIReviewBatch(
        project_id=1,
        profile="quick",
        scopes_json=json.dumps(scopes, ensure_ascii=False),
        endpoint_ids_json=json.dumps(endpoint_ids),
        user_instruction="stability regression",
        status="pending",
        created_at=now_iso(),
        actor="stability-test",
    )
    db.add(batch)
    db.commit()
    return batch


def test_health_check_deduplicates_and_reuses_completed_jobs(seeded_app, monkeypatch):
    """Duplicate form values and a repeated trigger must not duplicate work."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AI_ALLOW_MOCK_ENDPOINTS", "1")
    from app.ai.orchestrator import run_health_check
    from app.db import SessionLocal
    from app.models import AIConsensusReport, AIReviewJob

    db = SessionLocal()
    try:
        endpoint_ids = _mock_endpoint_ids(db)
        assert len(endpoint_ids) >= 2
        batch = _new_batch(
            db,
            [endpoint_ids[0], endpoint_ids[0], endpoint_ids[1]],
            ["tax", "tax"],
        )

        first = run_health_check(db, batch)
        first_job_count = db.query(AIReviewJob).filter(
            AIReviewJob.batch_id == batch.id
        ).count()
        first_report_count = db.query(AIConsensusReport).filter(
            AIConsensusReport.batch_id == batch.id
        ).count()

        second = run_health_check(db, batch)
        second_job_count = db.query(AIReviewJob).filter(
            AIReviewJob.batch_id == batch.id
        ).count()
        second_report_count = db.query(AIConsensusReport).filter(
            AIConsensusReport.batch_id == batch.id
        ).count()

        assert first.status == "completed"
        assert second.status == "completed"
        assert first_job_count == 2
        assert second_job_count == first_job_count
        assert first_report_count == second_report_count == 1
    finally:
        db.close()


def test_concurrent_tax_ledger_rebuilds_are_serialized(seeded_app):
    """Concurrent scope/model context builds leave one complete ledger."""
    from app.calc.tax import rebuild_tax_ledger
    from app.db import SessionLocal
    from app.models import Entity, TaxLedger

    period = "2098-01"

    def rebuild(_worker: int) -> int:
        db = SessionLocal()
        try:
            return len(rebuild_tax_ledger(db, period))
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(rebuild, [1, 2]))

    db = SessionLocal()
    try:
        legal_count = db.query(Entity).filter(
            Entity.active.is_(True), Entity.legal_entity.is_(True)
        ).count()
        persisted_count = db.query(TaxLedger).filter(
            TaxLedger.period == period
        ).count()
    finally:
        db.close()

    assert counts == [legal_count, legal_count]
    assert persisted_count == legal_count


def test_failed_tax_input_preserves_previous_ledger(seeded_app):
    """Validation failure must rollback without deleting an existing period."""
    from app.calc.tax import EntityScopeError, rebuild_tax_ledger
    from app.db import SessionLocal
    from app.models import Invoice, TaxLedger

    period = "2098-02"
    db = SessionLocal()
    invoice_no = "STABILITY-INVALID-209802"
    try:
        baseline = rebuild_tax_ledger(db, period)
        before = {
            row.entity_code: (Decimal(row.revenue), Decimal(row.real_cost))
            for row in baseline
        }
        db.add(
            Invoice(
                project_id=1,
                invoice_no=invoice_no,
                period=period,
                entity_code="NOT-CANONICAL",
                direction="out",
                counterparty_code="EXT-STABILITY",
                category="service",
                net=Decimal("10.00"),
                vat=Decimal("0.90"),
                rate=Decimal("0.09"),
                deductible=True,
                note="intentional invalid stability fixture",
            )
        )
        db.commit()

        with pytest.raises(EntityScopeError):
            rebuild_tax_ledger(db, period)

        after = {
            row.entity_code: (Decimal(row.revenue), Decimal(row.real_cost))
            for row in db.query(TaxLedger).filter(
                TaxLedger.period == period
            ).all()
        }
        assert after == before
    finally:
        db.query(Invoice).filter(Invoice.invoice_no == invoice_no).delete(
            synchronize_session=False
        )
        db.commit()
        db.close()
