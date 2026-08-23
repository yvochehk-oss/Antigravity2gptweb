"""
rag_client.py
=============
AI Review 调用的 RAG 客户端（V1.1）。

封装两种调用：
1. 项目文档证据检索：通过 app.services.rag_http_client 走 HTTP 桥接
2. 法规知识证据检索：直接 import 同进程的 regulation_retrieval（同进程性能更好）
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.services.rag_http_client import (
    list_projects as rag_list_projects,
    retrieve as rag_retrieve,
    retrieve_regulations as rag_retrieve_regulations,
)

logger = logging.getLogger(__name__)

_CANONICAL_ENTITY_CODES = frozenset(
    {f"A{i:02d}" for i in range(1, 12)}
    | {f"B{i:02d}" for i in range(1, 11)}
    | {f"C{i:02d}" for i in range(1, 3)}
    | {f"D{i:02d}" for i in range(1, 4)}
)


def _canonical_entity_code(value: Any) -> str | None:
    """Validate an entity identifier without treating business roles as IDs."""
    if value is None or str(value).strip() == "":
        return None
    code = str(value).strip().upper()
    try:
        from app.models import is_canonical_entity_code
        valid = is_canonical_entity_code(code)
    except Exception as exc:
        # The service can be imported by an offline worker that has no DBAPI
        # driver installed.  Keep the same explicit 26-code contract here;
        # never broaden validation to a role or a guessed company.
        logger.warning("canonical entity master unavailable: %s", exc)
        valid = code in _CANONICAL_ENTITY_CODES
    return code if valid else None


class RAGClientError(RuntimeError):
    """Explicit AI Review boundary error for an unusable RAG response."""

    status = "DEGRADED"
    needs_review = True


class RAGBridgeUnavailableError(RAGClientError):
    """The HTTP bridge reported a degraded/unavailable upstream response."""

    def __init__(self, operation: str, response: Mapping[str, Any]):
        self.operation = operation
        self.response_status = str(response.get("status") or "DEGRADED")
        detail = str(response.get("error") or "bridge unavailable")
        # The HTTP client already redacts upstream response bodies.  Keep only
        # its deterministic error string here; never copy arbitrary payloads.
        super().__init__(f"RAG bridge {operation} unavailable ({self.response_status}): {detail}")


class RAGBridgeProtocolError(RAGClientError):
    """The bridge returned a shape that violates its list-based contract."""


def _raise_for_degraded(operation: str, value: Any) -> None:
    """Raise an explicit boundary error for a structured degraded payload."""
    if isinstance(value, Mapping) and str(value.get("status", "")).upper() == "DEGRADED":
        raise RAGBridgeUnavailableError(operation, value)


def _require_list(value: Any, operation: str) -> list[dict[str, Any]]:
    """Validate a bridge list response without iterating a degraded dict."""
    _raise_for_degraded(operation, value)
    if not isinstance(value, list):
        raise RAGBridgeProtocolError(
            f"RAG bridge {operation} returned {type(value).__name__}; expected list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise RAGBridgeProtocolError(
                f"RAG bridge {operation} item {index} is {type(item).__name__}; expected object"
            )
    return [dict(item) for item in value]


class ProjectRAGClient:
    """AI Review 用的 RAG 客户端"""

    def __init__(self, project_code: str, default_top_k: int = 10):
        self.project_code = project_code
        self.default_top_k = default_top_k
        self._project_id: int | None = None

    @property
    def project_id(self) -> int | None:
        if self._project_id is None:
            projects = _require_list(rag_list_projects(), "list_projects")
            for project in projects:
                if project.get("project_code") == self.project_code:
                    self._project_id = project.get("id")
                    break
        return self._project_id

    def retrieve_evidence(self, query: str, top_k: int | None = None,
                          business_category: str = "") -> list[dict]:
        """检索项目文档证据，失败时抛出可识别的降级边界错误。"""
        pid = self.project_id
        if pid is None:
            logger.warning(f"无法解析 project_code={self.project_code} 对应的 project_id")
            return []
        response = rag_retrieve(
            project_id=pid,
            query=query,
            top_k=top_k or self.default_top_k,
            business_category=business_category,
        )
        return _require_list(response, "retrieve")

    def retrieve_regulation_evidence(self, query: str, jurisdiction: str | None = None,
                                     tax_type: str | None = None,
                                     top_k: int | None = None) -> list[dict]:
        """检索法规证据，失败时抛出可识别的降级边界错误。"""
        response = rag_retrieve_regulations(
            query=query,
            jurisdiction=jurisdiction,
            tax_type=tax_type,
            top_k=top_k or self.default_top_k,
        )
        return _require_list(response, "retrieve_regulations")

    def format_evidence(self, evidence: list[dict] | Mapping[str, Any] | None) -> list[dict]:
        """Normalize RAG results into auditable, citation-bearing evidence.

        Evidence is documentary truth only.  Any supplied ``entity_code`` is
        retained only when it is an actual canonical code (A01-A11/B01-B10/C01-C02/D01-D03); A/B/C/D
        and 甲乙丙丁 are rejected rather than silently re-labelled.

        A degraded bridge payload is an integration failure, not an empty
        evidence set.  Raise ``RAGBridgeUnavailableError`` so callers can
        route the review to manual review instead of iterating dict keys or
        silently producing a successful-looking empty result.
        """
        evidence_list = [] if evidence is None else _require_list(evidence, "format_evidence")
        out: list[dict] = []
        for ev in evidence_list:
            content = ev.get("content") or ev.get("text") or ev.get("snippet") or ""
            content = str(content)
            entity_code = _canonical_entity_code(ev.get("entity_code"))
            raw_entity_code = ev.get("entity_code")
            if raw_entity_code not in (None, "",) and entity_code is None:
                logger.warning("dropping evidence with invalid entity_code=%r", raw_entity_code)
                continue

            document_id = ev.get("document_id") or ev.get("regulation_id")
            chunk_id = ev.get("chunk_id") or ev.get("id")
            source = ev.get("source") or ev.get("source_uri") or ev.get("filename") or ev.get("document_filename")
            # A citation must have both body and a stable source reference.
            if not str(content).strip() or not str(source or "").strip():
                continue
            evidence_id = ev.get("evidence_id")
            if not evidence_id:
                if document_id is not None or chunk_id is not None:
                    evidence_id = f"doc:{document_id or 'unknown'}:chunk:{chunk_id or 'unknown'}"
                else:
                    evidence_id = f"source:{source}:page:{ev.get('page_start') or ev.get('page') or 'unknown'}"
            out.append({
                "evidence_id": str(evidence_id),
                "document_id": document_id,
                "chunk_id": chunk_id,
                "filename": ev.get("filename") or ev.get("document_filename") or "未知文件",
                "page": ev.get("page_start") or ev.get("page") or "N/A",
                "content": content,
                "summary": content[:200],
                "score": ev.get("score"),
                "source": source,
                "version": ev.get("version") or ev.get("version_label"),
                "effective_from": ev.get("effective_from") or ev.get("effective_date"),
                "effective_to": ev.get("effective_to") or ev.get("expiry_date"),
                "entity_code": entity_code,
                "business_role": ev.get("business_role") or ev.get("business_category") or ev.get("industry"),
            })
        return out


# 默认导出，便于 import
__all__ = [
    "ProjectRAGClient",
    "RAGClientError",
    "RAGBridgeUnavailableError",
    "RAGBridgeProtocolError",
]
