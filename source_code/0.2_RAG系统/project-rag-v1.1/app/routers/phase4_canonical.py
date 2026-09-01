"""RAG-owned Phase 4 structured canonical fact intake."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import SessionLocal
from ..services.phase4_canonical_facts import promote_phase4_document_fact

router = APIRouter(prefix="/api/v1/canonical-facts", tags=["canonical-facts"])


class Phase4FactRequest(BaseModel):
    document_id: int = Field(gt=0)
    fact_type: str
    business_key: str = Field(min_length=1, max_length=240)
    payload: dict[str, Any]
    evidence: dict[str, Any] = Field(default_factory=dict)


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
        return {
            "source_of_truth": "canonical_facts",
            "writer": "RAG",
            "phase": 4,
            **result,
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


__all__ = ["router"]
