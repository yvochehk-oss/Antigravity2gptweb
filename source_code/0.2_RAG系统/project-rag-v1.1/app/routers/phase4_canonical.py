"""RAG-owned Phase 4 Canonical Fact intake and deterministic derivation."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import SessionLocal
from ..models import Document
from ..services.phase4_canonical_facts import (
    infer_phase4_fact_type,
    promote_phase4_document_fact,
)
from ..services.phase4_extraction import (
    extract_phase4_payload_from_chunks,
    phase4_business_key,
)

router = APIRouter(prefix="/api/v1/canonical-facts", tags=["canonical-facts"])


class Phase4FactRequest(BaseModel):
    document_id: int = Field(gt=0)
    fact_type: str
    business_key: str = Field(min_length=1, max_length=240)
    payload: dict[str, Any]
    evidence: dict[str, Any] = Field(default_factory=dict)


def _result_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_of_truth": "canonical_facts",
        "writer": "RAG",
        "phase": 4,
        **result,
    }


@router.post("/phase4/promote")
def promote_phase4_fact(body: Phase4FactRequest) -> dict[str, Any]:
    db = SessionLocal()
    try:
        result = promote_phase4_document_fact(
            db,
            document_id=body.document_id,
            fact_type=body.fact_type,
            business_key=body.business_key,
            payload=body.payload,
            evidence=body.evidence,
        )
        db.commit()
        return _result_payload(result)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/phase4/documents/{document_id}/derive")
def derive_phase4_fact(document_id: int) -> dict[str, Any]:
    """Derive labelled progress/accrual/tax facts without LLM guesswork."""
    db = SessionLocal()
    try:
        doc = db.get(Document, int(document_id))
        if doc is None:
            raise HTTPException(status_code=404, detail="document not found")
        fact_type = infer_phase4_fact_type(doc.document_type)
        if fact_type is None:
            raise HTTPException(
                status_code=422,
                detail=f"document_type {doc.document_type!r} is not a Phase 4 accounting fact type",
            )
        payload, evidence = extract_phase4_payload_from_chunks(db, doc, fact_type)
        business_key = phase4_business_key(doc, fact_type, payload)
        result = promote_phase4_document_fact(
            db,
            document_id=doc.id,
            fact_type=fact_type,
            business_key=business_key,
            payload=payload,
            evidence=evidence,
        )
        db.commit()
        return _result_payload(
            {
                **result,
                "fact_type": fact_type,
                "business_key": business_key,
                "derivation": "phase4-labelled-fields-v1",
            }
        )
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


__all__ = ["router"]
