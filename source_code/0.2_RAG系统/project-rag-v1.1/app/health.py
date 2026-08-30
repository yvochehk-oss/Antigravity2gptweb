"""Single-pass health collection for the RAG service."""
from __future__ import annotations

import time as _time
from typing import Any

from sqlalchemy import text

from .config import AUTO_START_WORKER
from .config_validator import validate_config
from .db import SessionLocal
from .observability import get_request_id
from .services.embeddings import embedding_runtime
from .services.extractor import llm_extraction_available
from .services.jobs import get_worker_status
from .services.reranker import reranker_runtime


def _check_db_component() -> dict[str, object]:
    started = _time.monotonic()
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "latency_ms": int((_time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {
            "status": "down",
            "latency_ms": int((_time.monotonic() - started) * 1000),
            "error": str(exc),
        }


def _check_worker_component() -> dict[str, object]:
    runtime = dict(get_worker_status())
    if runtime.get("running"):
        status = "ok"
        error = None
    elif bool(AUTO_START_WORKER):
        status = "down"
        error = "worker 未运行（auto_start 已启用）"
    else:
        status = "ok"
        error = None
    component: dict[str, object] = {
        "status": status,
        "auto_start": bool(AUTO_START_WORKER),
        "runtime": runtime,
    }
    if error:
        component["error"] = error
    return component


def _check_ai_component() -> dict[str, object]:
    available = bool(llm_extraction_available())
    return {
        "status": "ok" if available else "degraded",
        "extraction_available": available,
    }


def _check_facts_component() -> dict[str, object]:
    try:
        from facts_provider import facts_provider as _fp

        with SessionLocal() as db:
            row = db.execute(
                text("SELECT 1 FROM analytics_project_full LIMIT 1")
            ).first()
        if row is None:
            return {
                "status": "degraded",
                "module": _fp.__name__,
                "source": "analytics_project_full",
                "error": "analytics source has no rows",
            }
        return {
            "status": "ok",
            "module": _fp.__name__,
            "source": "analytics_project_full",
        }
    except Exception as exc:
        return {
            "status": "degraded",
            "source": "analytics_project_full",
            "error": f"Facts source unavailable: {exc}",
        }


def _check_embedding_component() -> dict[str, object]:
    runtime = dict(embedding_runtime())
    backend = str(runtime.get("backend") or "")
    if backend == "hash_v1":
        runtime.update({
            "status": "degraded",
            "error": "development hash embedding backend is active",
        })
    elif backend == "bge_m3":
        runtime["status"] = "ok" if runtime.get("model_loaded") else "degraded"
        if not runtime.get("model_loaded"):
            runtime["error"] = "BGE model is configured but not loaded"
    else:
        runtime.update({
            "status": "down",
            "error": f"unsupported embedding backend: {backend or '<empty>'}",
        })
    return runtime


def _check_reranker_component() -> dict[str, object]:
    runtime = dict(reranker_runtime())
    backend = str(runtime.get("backend") or "")
    if backend in {"", "none", "off"}:
        runtime.update({"status": "ok", "disabled": True})
    elif backend == "bge_v2_m3":
        runtime["status"] = "ok" if runtime.get("model_loaded") else "degraded"
        if not runtime.get("model_loaded"):
            runtime["error"] = "reranker model is configured but not loaded"
    else:
        runtime.update({
            "status": "down",
            "error": f"unsupported reranker backend: {backend}",
        })
    return runtime


def worst_status(components: dict[str, dict[str, object]]) -> str:
    priority = {"ok": 0, "degraded": 1, "down": 2}
    worst = "ok"
    for component in components.values():
        status = str(component.get("status", "down"))
        if priority.get(status, 2) > priority[worst]:
            worst = status
    return worst


def collect_health_snapshot() -> dict[str, Any]:
    """Collect every health dependency exactly once for one endpoint call."""
    started = _time.monotonic()
    validation_errors = validate_config()
    components = {
        "db": _check_db_component(),
        "worker": _check_worker_component(),
        "ai": _check_ai_component(),
        "facts": _check_facts_component(),
        "embedding": _check_embedding_component(),
        "reranker": _check_reranker_component(),
    }
    worst = worst_status(components)
    overall = "ok" if not validation_errors and worst == "ok" else (
        worst if worst != "ok" else "degraded"
    )
    worker_runtime = components["worker"].get("runtime", {})
    return {
        "status": overall,
        "service": "project-rag",
        "version": "1.1.0",
        "request_id": get_request_id(),
        "native_parser_available": True,
        "database": components["db"],
        "embedding": components["embedding"],
        "reranker": components["reranker"],
        "llm_extraction": components["ai"].get("extraction_available", False),
        "worker": worker_runtime,
        "worker_auto_start": bool(AUTO_START_WORKER),
        "validation_errors": validation_errors,
        "components": components,
        "checked_in_ms": int((_time.monotonic() - started) * 1000),
    }


def get_health_components() -> dict[str, dict[str, object]]:
    """Compatibility wrapper for callers that only need component status."""
    return collect_health_snapshot()["components"]


__all__ = ["collect_health_snapshot", "get_health_components", "worst_status"]
