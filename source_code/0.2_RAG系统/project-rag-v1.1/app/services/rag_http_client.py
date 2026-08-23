"""
rag_http_client.py
==================
HTTP 桥接客户端，供 v1.0 时代的 ai_review / facts_provider 调用 v0.2 风格的 RAG API。

设计目标：
- 同进程部署：默认 RAG_HTTP_BASE_URL=http://127.0.0.1:8922，httpx 直连本进程，性能可接受
- 跨进程部署：未来把 RAG HTTP 拆出去时无需改 ai_review / facts_provider
- 容错：调用失败时返回结构化的 ``{"error": ..., "status": "DEGRADED"}``
  响应；调用方有责任检查 ``status``/``error``。这避免了把异常静默伪装
  成“成功 + 空结果”的反模式。

注意：本文件不引入 sql/sqlalchemy 之外的依赖（httpx 已在 requirements.txt）。
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = os.environ.get("RAG_HTTP_BASE_URL", "http://127.0.0.1:8922")
DEFAULT_TIMEOUT = float(os.environ.get("RAG_HTTP_TIMEOUT", "10"))


def _client(timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    """Build the shared bridge client with the current cross-system token.

    ``RAG_SHARED_API_KEY`` is deliberately read at request-client creation
    time rather than copied into another module-level setting.  This keeps
    the authentication boundary in one place, allows a controlled secret
    rotation without re-importing this module, and makes the no-key local
    development contract explicit.  The key is only supplied to httpx as a
    request header; it is never included in logs or error messages.
    """
    shared_key = os.environ.get("RAG_SHARED_API_KEY", "").strip()
    headers = {"Authorization": f"Bearer {shared_key}"} if shared_key else None
    return httpx.Client(
        base_url=DEFAULT_BASE_URL,
        timeout=timeout,
        headers=headers,
    )


def _format_http_error(e: Exception) -> dict[str, Any]:
    """Build a deterministic error payload from a raised exception."""
    if isinstance(e, httpx.TimeoutException):
        return {"error": "rag_http_client timeout", "status": "DEGRADED"}
    if isinstance(e, httpx.HTTPStatusError):
        return {
            # Do not echo arbitrary response bodies: an upstream error body
            # can contain credentials or other sensitive deployment details.
            "error": f"rag_http_client HTTP {e.response.status_code}",
            "status": "DEGRADED",
        }
    return {"error": f"rag_http_client failed: {e}", "status": "DEGRADED"}


# ---------------- Projects ----------------

def list_projects() -> list[dict] | dict[str, Any]:
    """获取所有项目列表（用于 project_code → project_id 解析）。

    Returns the parsed list on success.  On failure returns a structured
    ``{"error": ..., "status": "DEGRADED"}`` dict that callers must
    detect before promoting the result to a financial statement.
    """
    try:
        with _client() as c:
            r = c.get("/api/v1/projects")
            r.raise_for_status()
            return r.json() or []
    except Exception as e:
        logger.warning("list_projects failed: %s", e)
        return _format_http_error(e)


def resolve_project_id(project_code: str) -> int | None:
    """根据 project_code 拿 project_id，未找到返回 None。

    Network failures propagate as ``None`` because there is no caller
    that can usefully act on a degraded bridge error here — but a
    warning is logged so the failure is never invisible.
    """
    projects = list_projects()
    if isinstance(projects, dict):
        logger.warning(
            "resolve_project_id: cannot reach RAG HTTP bridge for %s",
            project_code,
        )
        return None
    for p in projects:
        if p.get("project_code") == project_code:
            return p.get("id")
    return None


# ---------------- Documents / Chunks ----------------

def retrieve(project_id: int, query: str, top_k: int = 10,
              business_category: str = "", entity_code: str = "") -> list[dict] | dict[str, Any]:
    """调用 /api/v1/retrieve 拿证据块。

    A failure is returned as a structured dict that includes
    ``status="DEGRADED"``; the legacy contract of silently returning
    ``[]`` has been removed.
    """
    try:
        with _client() as c:
            r = c.post(
                "/api/v1/retrieve",
                json={
                    "project_id": project_id,
                    "query": query,
                    "top_k": top_k,
                    "business_category": business_category,
                    "entity_code": entity_code,
                },
            )
            r.raise_for_status()
            data = r.json()
            return data.get("results") or []
    except Exception as e:
        logger.warning("retrieve failed: %s", e)
        return _format_http_error(e)


def query(project_id: int, query: str, top_k: int = 10,
          business_category: str = "", entity_code: str = "") -> dict:
    """调用 /api/v1/query 拿完整问答（answer + results + citations）。

    Returns the parsed JSON on success.  On failure the response carries
    ``status="DEGRADED"`` so the caller can distinguish a transport
    failure from a successful empty answer.
    """
    try:
        with _client() as c:
            r = c.post(
                "/api/v1/query",
                json={
                    "project_id": project_id,
                    "query": query,
                    "top_k": top_k,
                    "business_category": business_category,
                    "entity_code": entity_code,
                },
            )
            r.raise_for_status()
            return r.json() or {}
    except Exception as e:
        logger.warning("query failed: %s", e)
        return {
            "answer": "",
            "results": [],
            "citations": [],
            **_format_http_error(e),
        }


# ---------------- Regulations ----------------

def retrieve_regulations(query: str, jurisdiction: str | None = None,
                         tax_type: str | None = None, industry: str | None = None,
                         top_k: int = 10) -> list[dict] | dict[str, Any]:
    """调用 /api/v1/regulations/retrieve"""
    try:
        with _client() as c:
            r = c.post(
                "/api/v1/regulations/retrieve",
                json={
                    "query": query,
                    "jurisdiction": jurisdiction,
                    "tax_type": tax_type,
                    "industry": industry,
                    "top_k": top_k,
                },
            )
            r.raise_for_status()
            data = r.json()
            return data.get("results") or []
    except Exception as e:
        logger.warning("retrieve_regulations failed: %s", e)
        return _format_http_error(e)


def query_regulations(query: str, jurisdiction: str | None = None,
                      tax_type: str | None = None, industry: str | None = None,
                      top_k: int = 10) -> dict:
    """调用 /api/v1/regulations/query"""
    try:
        with _client() as c:
            r = c.post(
                "/api/v1/regulations/query",
                json={
                    "query": query,
                    "jurisdiction": jurisdiction,
                    "tax_type": tax_type,
                    "industry": industry,
                    "top_k": top_k,
                    "answer": True,
                },
            )
            r.raise_for_status()
            return r.json() or {}
    except Exception as e:
        logger.warning("query_regulations failed: %s", e)
        return {
            "answer": "",
            "results": [],
            "citations": [],
            **_format_http_error(e),
        }


# ---------------- Health ----------------

def health() -> dict[str, Any]:
    """健康检查（用于 ai_review 启动判断 RAG 服务是否可用）"""
    try:
        with _client(timeout=2) as c:
            r = c.get("/api/v1/health")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("health failed: %s", e)
        return {"status": "unreachable", "error": str(e), "degraded": True}
