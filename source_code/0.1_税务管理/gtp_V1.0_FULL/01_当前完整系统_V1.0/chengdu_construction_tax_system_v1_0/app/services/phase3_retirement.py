"""Phase 3 retirement guard for legacy Tax/RAG write channels.

Canonical Facts are the only durable business-fact write boundary. The old
Tax sync routes remain addressable only to return HTTP 410 so stale clients
fail loudly instead of silently recreating a second source of truth.
"""
from __future__ import annotations

from collections.abc import Iterable

from fastapi import FastAPI, HTTPException, Request


_RETIRED_POST_PATHS = (
    "/rag-sync/sync",
    "/rag-sync/sync-batch",
    "/rag-sync/sync-pending",
    "/sync-pending",
    "/api/sync-pending",
    "/rag-sync/pending/{pending_id}/confirm",
    "/rag-sync/pending/{pending_id}/confirm-contract-and-create-parties",
    "/rag-sync/pending/{pending_id}/reject",
)


def _remove_routes(app: FastAPI, paths: Iterable[str], method: str = "POST") -> int:
    targets = set(paths)
    method = method.upper()
    kept = []
    removed = 0
    for route in app.router.routes:
        route_path = getattr(route, "path", "")
        methods = {
            str(item).upper()
            for item in (getattr(route, "methods", None) or set())
        }
        if route_path in targets and method in methods:
            removed += 1
            continue
        kept.append(route)
    app.router.routes[:] = kept
    return removed


async def _gone(_request: Request) -> None:
    raise HTTPException(
        status_code=410,
        detail=(
            "Phase 3 已物理退役旧 Tax/RAG 同步写通道；"
            "业务事实只能由 RAG Canonical Facts 写入，Tax 为只读消费者。"
        ),
    )


def install_phase3_retirement(app: FastAPI) -> dict[str, object]:
    """Replace legacy POST writers with permanent fail-closed 410 routes."""
    removed = _remove_routes(app, _RETIRED_POST_PATHS)
    for index, path in enumerate(_RETIRED_POST_PATHS, start=1):
        app.add_api_route(
            path,
            _gone,
            methods=["POST"],
            status_code=410,
            name=f"phase3_retired_tax_write_{index}",
            tags=["deprecated"],
        )
    return {
        "phase": 3,
        "source_of_truth": "canonical_facts",
        "removed_legacy_writers": removed,
        "retired_paths": list(_RETIRED_POST_PATHS),
    }


__all__ = ["install_phase3_retirement"]
