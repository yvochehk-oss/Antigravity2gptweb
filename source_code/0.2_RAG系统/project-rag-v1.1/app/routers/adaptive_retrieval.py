"""Authenticated HTTP endpoints for the V0.3 adaptive retrieval pipeline.

The orchestration itself lives in :mod:`app.services.retrieval`.  This module
only adapts the service to a stable HTTP contract: it resolves a project,
passes options by name, keeps pipeline failures visible, and applies the same
Tax-issued JWT/RBAC boundary as the other protected API routes.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..auth import TaxPrincipal, require_auth
from ..config import ENABLE_HYDE, ENABLE_QUERY_REWRITE
from ..logging_config import get_logger
from ..models import Project
from ..schemas import (
    AdaptiveRetrievalResponse,
    DeepRetrievalRequest,
    QueryRequestV3,
    RetrievalExplainResponse,
)
from ..services import retrieval as retrieval_service
from ..services.llm import answer_with_evidence
from ..session import get_db

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/retrieve", tags=["adaptive-retrieval"])


def require_retrieval_access(principal: TaxPrincipal = Depends(require_auth)) -> TaxPrincipal:
    """Allow only the Tax roles that can read project evidence."""
    if principal.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "RETRIEVAL_ROLE_FORBIDDEN", "message": "需要 admin 或 operator 角色"},
        )
    return principal


def _resolve_project(db: Any, project_id: int | None, project_code: str | None) -> Project:
    """Resolve exactly one project and reject missing/ambiguous references."""
    normalized_code = (project_code or "").strip()
    if project_id is None and not normalized_code:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "PROJECT_REFERENCE_REQUIRED",
                "message": "project_id or project_code is required",
            },
        )
    if project_id is not None and project_id < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_PROJECT_ID", "message": "project_id must be positive"},
        )

    project = db.get(Project, project_id) if project_id is not None else None
    if project_id is None and normalized_code:
        project = db.scalar(select(Project).where(Project.project_code == normalized_code))
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROJECT_NOT_FOUND", "message": "project not found"},
        )
    if normalized_code and project.project_code != normalized_code:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "PROJECT_REFERENCE_CONFLICT",
                "message": "project_id and project_code refer to different projects",
            },
        )
    return project


def _selected_flag(value: bool | None, configured: bool) -> bool:
    """Use request flags when supplied, otherwise use the configured default."""
    return configured if value is None else bool(value)


def _normalise_pipeline_result(
    result: list[dict] | dict,
    *,
    project_id: int,
    query: str,
    deep: bool,
) -> dict:
    """Normalize legacy list responses without hiding adaptive metadata."""
    if isinstance(result, list):
        payload: dict[str, Any] = {
            "results": result,
            "rewrite_used": False,
            "hyde_used": False,
            "deep_mode": deep,
            "pipeline_errors": [],
            "reranker_status": "UNKNOWN",
            "retrieval_status": "OK",
            "search_diagnostics": {},
            "effective_filters": {},
            "latency_ms": 0,
            "bm25_candidates": [],
            "vector_candidates": [],
        }
    elif isinstance(result, dict):
        payload = dict(result)
        payload.setdefault("results", [])
        payload.setdefault("pipeline_errors", [])
        payload.setdefault("deep_mode", deep)
        payload.setdefault("search_diagnostics", {})
        payload.setdefault("effective_filters", {})
        payload.setdefault("latency_ms", 0)
        payload.setdefault("bm25_candidates", [])
        payload.setdefault("vector_candidates", [])
        payload.setdefault("reranker_status", "UNKNOWN")
    else:
        raise RuntimeError("retrieval service returned an invalid response type")

    payload["project_id"] = project_id
    payload["query"] = query
    payload.setdefault("rewrite_used", False)
    payload.setdefault("hyde_used", False)
    retrieval_status = str(payload.get("retrieval_status") or "UNKNOWN")
    errors = payload.get("pipeline_errors") or []
    if errors and retrieval_status in {"OK", "UNKNOWN"}:
        retrieval_status = "DEGRADED"
    payload["retrieval_status"] = retrieval_status
    # ``status`` is the stable HTTP-facing alias.  Keep the service's
    # retrieval_status intact for clients that already consume that field.
    current_status = str(payload.get("status") or "")
    if not current_status or current_status == "UNKNOWN":
        current_status = retrieval_status
    elif current_status == "OK" and retrieval_status not in {"OK", "UNKNOWN"}:
        current_status = retrieval_status
    payload["status"] = current_status
    return payload


def _append_answer(payload: dict, query: str, enabled: bool) -> None:
    """Attach a citation-grounded answer, surfacing generation failures."""
    if not enabled:
        return
    try:
        answer_result = answer_with_evidence(query, payload.get("results", []))
        payload["answer"] = answer_result.answer
        payload["citations"] = [
            {"index": index, "chunk_id": payload["results"][index - 1].get("chunk_id")}
            for index in answer_result.citations_used
            if isinstance(index, int) and 1 <= index <= len(payload.get("results", []))
        ]
        payload["faithful"] = answer_result.faithful
        payload["no_answer_confidence"] = answer_result.no_answer_confidence
    except Exception as exc:  # pragma: no cover - defensive boundary
        message = str(exc)
        logger.exception("Adaptive retrieval answer generation failed: %s", message)
        payload.setdefault("pipeline_errors", []).append(
            {"step": "answer", "code": "ANSWER_FAILED", "message": message}
        )
        payload["retrieval_status"] = "DEGRADED"
        payload["status"] = "DEGRADED"


def _execute_retrieval(
    body: QueryRequestV3 | DeepRetrievalRequest,
    *,
    db: Any,
    project_id: int,
    force_deep: bool,
) -> dict:
    """Execute the service with keyword arguments matching its real contract."""
    rewrite = _selected_flag(body.rewrite, ENABLE_QUERY_REWRITE)
    hyde = _selected_flag(body.hyde, ENABLE_HYDE)
    deep = bool(force_deep or body.deep)
    if deep:
        # ``retrieve`` intentionally enables both adaptive stages in deep
        # mode.  Passing the flags explicitly prevents the old positional
        # signature mismatch from silently disabling one of the stages.
        rewrite = True
        hyde = True

    return _normalise_pipeline_result(
        retrieval_service.retrieve(
            db=db,
            project_id=project_id,
            query=body.query,
            filters=body.filters,
            top_k=body.top_k,
            use_rerank=body.rerank,
            rewrite=rewrite,
            hyde=hyde,
            deep=deep,
        ),
        project_id=project_id,
        query=body.query,
        deep=deep,
    )


def _run(
    body: QueryRequestV3 | DeepRetrievalRequest,
    *,
    principal: TaxPrincipal,
    force_deep: bool,
    include_answer: bool | None = None,
) -> dict:
    """Open a request-scoped DB session and translate backend failures."""
    del principal  # Dependency enforces JWT authentication/RBAC at the boundary.
    if body.stream:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "STREAM_NOT_SUPPORTED", "message": "streaming adaptive retrieval is not enabled"},
        )
    if body.history:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "HISTORY_NOT_SUPPORTED",
                "message": "history context is not supported by the current retrieval service",
            },
        )

    try:
        with get_db() as db:
            project = _resolve_project(db, body.project_id, body.project_code)
            # Keep the DB session explicit and request-scoped.  Passing it as
            # an ordinary argument also keeps concurrent requests isolated.
            payload = _execute_retrieval(
                body,
                db=db,
                project_id=project.id,
                force_deep=force_deep,
            )
            answer_requested = bool(body.answer)
            if force_deep:
                answer_requested = bool(getattr(body, "generate_answer", False) and body.answer)
            if include_answer is not None:
                answer_requested = include_answer
            _append_answer(payload, body.query, answer_requested)
            return payload
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_RETRIEVAL_REQUEST", "message": str(exc)},
        ) from exc
    except SQLAlchemyError as exc:
        logger.exception("Adaptive retrieval database failure: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "RETRIEVAL_DATABASE_UNAVAILABLE", "message": "retrieval database unavailable"},
        ) from exc
    except Exception as exc:
        logger.exception("Adaptive retrieval backend failure: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "RETRIEVAL_BACKEND_UNAVAILABLE", "message": "retrieval backend unavailable"},
        ) from exc


@router.post("/adaptive", response_model=AdaptiveRetrievalResponse)
def adaptive_retrieve(body: QueryRequestV3, principal: TaxPrincipal = Depends(require_retrieval_access)):
    """Run rewrite → hybrid search → rerank → quality gate → optional HyDE."""
    payload = _run(body, principal=principal, force_deep=False)
    return AdaptiveRetrievalResponse.model_validate(payload)


@router.post("/deep", response_model=AdaptiveRetrievalResponse)
def deep_retrieve(body: DeepRetrievalRequest, principal: TaxPrincipal = Depends(require_retrieval_access)):
    """Run the complete adaptive pipeline and return a grounded evidence pack."""
    payload = _run(body, principal=principal, force_deep=True)
    return AdaptiveRetrievalResponse.model_validate(payload)


@router.post("/explain", response_model=RetrievalExplainResponse)
def explain_retrieve(body: QueryRequestV3, principal: TaxPrincipal = Depends(require_retrieval_access)):
    """Return scoring and metadata explanations with pipeline status."""
    payload = _run(body, principal=principal, force_deep=True, include_answer=False)
    results = payload.get("results", [])
    query_tokens = retrieval_service.tokens(body.query)
    results = retrieval_service._attach_explain_metadata(results, query_tokens)
    items = []
    for item in results[: body.top_k]:
        items.append(
            {
                "chunk_id": item.get("chunk_id"),
                "document_id": item.get("document_id"),
                "filename": item.get("filename", ""),
                "heading_path": item.get("heading_path", ""),
                "title_chain": item.get("title_chain", ""),
                "rerank_score": item.get("rerank_score", item.get("score", 0.0)),
                "vector_score": item.get("vector_score", 0.0),
                "bm25_score": item.get("bm25_score", 0.0),
                "matched_keywords": item.get("matched_keywords", []),
                "metadata_matched": item.get("metadata_matched", {}),
            }
        )
    return RetrievalExplainResponse(
        query=body.query,
        project_id=payload["project_id"],
        items=items,
        quality_gate=payload.get("quality_gate"),
        status=payload.get("status", "UNKNOWN"),
        retrieval_status=payload.get("retrieval_status", "UNKNOWN"),
        pipeline_errors=payload.get("pipeline_errors", []),
        latency_ms=payload.get("latency_ms", 0),
    )


__all__ = ["require_retrieval_access", "router"]
