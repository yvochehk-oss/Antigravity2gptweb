"""Health check components and aggregation for RAG service."""
from __future__ import annotations

import time as _time

from .config import AUTO_START_WORKER
from .services.jobs import get_worker_status
from .services.extractor import llm_extraction_available
from .services.embeddings import embedding_runtime
from .services.reranker import reranker_runtime


def _check_db_component() -> dict[str, object]:
    """Return the database component health, including latency."""
    started = _time.monotonic()
    try:
        from .db import SessionLocal
        from sqlalchemy import text
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        latency_ms = int((_time.monotonic() - started) * 1000)
        return {"status": "ok", "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = int((_time.monotonic() - started) * 1000)
        return {"status": "down", "latency_ms": latency_ms, "error": str(exc)}


def _check_worker_component() -> dict[str, object]:
    """Surface worker liveness from the in-process status dictionary."""
    status = get_worker_status()
    if status.get("running"):
        return {
            "status": "ok",
            "thread_id": status.get("thread_id"),
            "thread_name": status.get("thread_name"),
        }
    if bool(AUTO_START_WORKER):
        return {"status": "down", "error": "worker 未运行（auto_start 已启用）"}
    return {"status": "ok", "auto_start": False}


def _check_ai_component() -> dict[str, object]:
    """Surface whether structured LLM extraction is available."""
    return {
        "status": "ok" if llm_extraction_available() else "degraded",
        "extraction_available": llm_extraction_available(),
    }


def _check_facts_component() -> dict[str, object]:
    """Probe the Facts provider and its deterministic analytics source."""
    try:
        from facts_provider import facts_provider as _fp
        from sqlalchemy import text
        from .db import SessionLocal
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
    """Report embedding backend status without loading a model."""
    runtime = embedding_runtime()
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
    """Report reranker status; explicit off/none is a healthy choice."""
    runtime = reranker_runtime()
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


def get_health_components() -> dict[str, dict[str, object]]:
    """Return all health check components."""
    return {
        "db": _check_db_component(),
        "worker": _check_worker_component(),
        "ai": _check_ai_component(),
        "facts": _check_facts_component(),
        "embedding": _check_embedding_component(),
        "reranker": _check_reranker_component(),
    }


def worst_status(components: dict[str, dict[str, object]]) -> str:
    """Return the worst status from a dict of component checks."""
    priority = {"ok": 0, "degraded": 1, "down": 2}
    worst = "ok"
    for component in components.values():
        status = str(component.get("status", "down"))
        if priority.get(status, 2) > priority.get(worst, 0):
            worst = status
    return worst
