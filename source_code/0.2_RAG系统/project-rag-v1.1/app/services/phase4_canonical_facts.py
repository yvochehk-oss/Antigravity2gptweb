"""Structured Phase 4 fact promotion owned by RAG.

These facts extend the same ``canonical_facts`` SSOT with accounting evidence
that is not an invoice/contract/payment: progress recognition, accrued unbilled
cost and book-tax adjustments.  The writer is deterministic, versioned and
fail-closed; Tax never writes these rows.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text

from ..models import Document

PHASE4_FACT_TYPES = frozenset({"progress", "accrual", "tax_adjustment"})
PHASE4_SCHEMA_VERSION = 1
PHASE4_PRODUCER = "rag:phase4-structured-v1"


def infer_phase4_fact_type(document_type: str | None) -> str | None:
    value = str(document_type or "").strip().lower().replace("-", "_")
    if value in {"progress", "progress_claim", "completion_certificate", "work_confirmation"}:
        return "progress"
    if value in {"accrual", "unbilled_cost", "cost_accrual", "settlement_accrual"}:
        return "accrual"
    if value in {"tax_adjustment", "cit_adjustment", "book_tax_adjustment"}:
        return "tax_adjustment"
    return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def validate_phase4_payload(fact_type: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if fact_type not in PHASE4_FACT_TYPES:
        return [f"unsupported Phase 4 fact_type: {fact_type}"]

    if fact_type == "progress":
        completion = _decimal(payload.get("completion_percent"))
        estimated_total_cost = _decimal(payload.get("estimated_total_cost"))
        recognized_revenue = _decimal(payload.get("recognized_revenue"))
        if completion is not None and not (Decimal("0") <= completion <= Decimal("1")):
            errors.append("completion_percent must be between 0 and 1")
        if completion is None and (estimated_total_cost is None or estimated_total_cost <= 0):
            if recognized_revenue is None or recognized_revenue < 0:
                errors.append(
                    "progress requires completion_percent, positive estimated_total_cost, or recognized_revenue"
                )
        if estimated_total_cost is not None and estimated_total_cost <= 0:
            errors.append("estimated_total_cost must be positive")

    elif fact_type == "accrual":
        amount = _decimal(payload.get("amount"))
        if amount is None or amount <= 0:
            errors.append("accrual amount must be positive")
        reversal = _decimal(payload.get("reversal_amount"))
        if reversal is not None and reversal < 0:
            errors.append("reversal_amount must not be negative")

    elif fact_type == "tax_adjustment":
        amount = _decimal(payload.get("amount"))
        direction = str(payload.get("direction") or "").strip().upper()
        if amount is None or amount <= 0:
            errors.append("tax_adjustment amount must be positive")
        if direction not in {"ADD", "DEDUCT"}:
            errors.append("tax_adjustment direction must be ADD or DEDUCT")

    return errors


def _source_hash(doc: Document, fact_type: str, business_key: str, payload: dict[str, Any]) -> str:
    body = {
        "schema": PHASE4_SCHEMA_VERSION,
        "policy": "phase4-accounting-facts-v1",
        "document_hash": str(getattr(doc, "file_hash", "") or ""),
        "fact_type": fact_type,
        "business_key": business_key,
        "payload": payload,
    }
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _business_lock(db, project_id: int, fact_type: str, business_key: str) -> None:
    bind = db.get_bind()
    if getattr(getattr(bind, "dialect", None), "name", "") == "postgresql":
        db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(CAST(:key AS text), 0))"
            ),
            {"key": f"canonical_fact|{project_id}|{fact_type}|{business_key}"},
        )


def promote_phase4_document_fact(
    db,
    *,
    document_id: int,
    fact_type: str,
    business_key: str,
    payload: dict[str, Any],
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Promote one structured accounting fact into the shared SSOT."""
    fact_type = str(fact_type or "").strip().lower()
    business_key = str(business_key or "").strip()
    doc = db.get(Document, int(document_id))
    if doc is None:
        raise ValueError(f"document not found: {document_id}")
    if not business_key:
        raise ValueError("business_key is required")

    normalized = dict(payload or {})
    errors = validate_phase4_payload(fact_type, normalized)
    if str(getattr(doc, "parse_status", "") or "").upper() != "INDEXED":
        errors.append("source document must be INDEXED before canonical promotion")
    if getattr(doc, "duplicate_of_id", None):
        errors.append("duplicate source document cannot create a current canonical fact")

    status = "accepted" if not errors else "needs_review"
    source_hash = _source_hash(doc, fact_type, business_key, normalized)
    project_id = int(doc.project_id)
    _business_lock(db, project_id, fact_type, business_key)

    existing = db.execute(
        text(
            "SELECT id, fact_version, status FROM canonical_facts "
            "WHERE source_document_id=:document_id AND fact_type=:fact_type "
            "AND source_hash=:source_hash FOR UPDATE"
        ),
        {"document_id": doc.id, "fact_type": fact_type, "source_hash": source_hash},
    ).mappings().first()
    if existing:
        return {
            "id": int(existing["id"]),
            "fact_version": int(existing["fact_version"]),
            "status": str(existing["status"]),
            "idempotent": True,
        }

    fact_version = int(
        db.execute(
            text(
                "SELECT COALESCE(MAX(fact_version), 0) FROM canonical_facts "
                "WHERE project_id=:project_id AND fact_type=:fact_type AND business_key=:business_key"
            ),
            {"project_id": project_id, "fact_type": fact_type, "business_key": business_key},
        ).scalar_one()
    ) + 1

    if status == "accepted":
        db.execute(
            text(
                "UPDATE canonical_facts SET status='superseded', is_current=FALSE, updated_at=now() "
                "WHERE project_id=:project_id AND fact_type=:fact_type AND business_key=:business_key "
                "AND status='accepted' AND is_current=TRUE"
            ),
            {"project_id": project_id, "fact_type": fact_type, "business_key": business_key},
        )

    confidence = _decimal(getattr(doc, "metadata_confidence", None)) or Decimal("0")
    confidence = max(Decimal("0"), min(Decimal("0.99999"), confidence))
    result = db.execute(
        text(
            "INSERT INTO canonical_facts ("
            "source_document_id, project_id, fact_type, business_key, schema_version, fact_version, "
            "source_hash, payload, evidence, validation_errors, confidence, status, is_current, producer, accepted_at"
            ") VALUES ("
            ":document_id, :project_id, :fact_type, :business_key, :schema_version, :fact_version, "
            ":source_hash, CAST(:payload AS jsonb), CAST(:evidence AS jsonb), CAST(:errors AS jsonb), "
            ":confidence, CAST(:status AS varchar), :is_current, :producer, "
            "CASE WHEN CAST(:status AS varchar)='accepted' THEN now() ELSE NULL END"
            ") RETURNING id"
        ),
        {
            "document_id": doc.id,
            "project_id": project_id,
            "fact_type": fact_type,
            "business_key": business_key,
            "schema_version": PHASE4_SCHEMA_VERSION,
            "fact_version": fact_version,
            "source_hash": source_hash,
            "payload": json.dumps(normalized, ensure_ascii=False, default=str),
            "evidence": json.dumps(evidence or {"document_id": doc.id}, ensure_ascii=False, default=str),
            "errors": json.dumps(errors, ensure_ascii=False),
            "confidence": confidence,
            "status": status,
            "is_current": status == "accepted",
            "producer": PHASE4_PRODUCER,
        },
    )
    fact_id = int(result.scalar_one())
    if status == "accepted":
        db.execute(
            text(
                "INSERT INTO canonical_fact_outbox (fact_id, event_type, payload) "
                "VALUES (:fact_id, 'canonical_fact.accepted', CAST(:payload AS jsonb)) "
                "ON CONFLICT (fact_id, event_type) DO NOTHING"
            ),
            {
                "fact_id": fact_id,
                "payload": json.dumps(
                    {
                        "fact_id": fact_id,
                        "project_id": project_id,
                        "fact_type": fact_type,
                        "business_key": business_key,
                        "fact_version": fact_version,
                    },
                    ensure_ascii=False,
                ),
            },
        )
    return {
        "id": fact_id,
        "fact_version": fact_version,
        "status": status,
        "idempotent": False,
        "validation_errors": errors,
    }


__all__ = [
    "PHASE4_FACT_TYPES",
    "infer_phase4_fact_type",
    "promote_phase4_document_fact",
    "validate_phase4_payload",
]
