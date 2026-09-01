"""Phase 3 retirement of legacy RAG-to-Tax HTTP writers."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request


_RETIRED_PATH = "/api/v1/extract-tax"


def _remove_extract_tax(app: FastAPI) -> int:
    kept = []
    removed = 0
    for route in app.router.routes:
        methods = {str(item).upper() for item in (getattr(route, "methods", None) or set())}
        if getattr(route, "path", "") == _RETIRED_PATH and "POST" in methods:
            removed += 1
            continue
        kept.append(route)
    app.router.routes[:] = kept
    return removed


async def _extract_tax_gone(_request: Request) -> None:
    raise HTTPException(
        status_code=410,
        detail=(
            "Phase 3 已退役 /extract-tax；RAG 直接写 canonical_facts，"
            "Tax 通过 analytics_canonical_facts_current 同库只读。"
        ),
    )


def install_phase3_retirement(app: FastAPI) -> int:
    removed = _remove_extract_tax(app)
    app.add_api_route(
        _RETIRED_PATH,
        _extract_tax_gone,
        methods=["POST"],
        status_code=410,
        name="phase3_retired_extract_tax",
        tags=["deprecated"],
    )
    return removed


__all__ = ["install_phase3_retirement"]
