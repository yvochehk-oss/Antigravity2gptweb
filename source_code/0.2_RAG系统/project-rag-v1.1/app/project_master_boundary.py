"""Project Master SSOT boundary enforcement for ProjectRAG.

Project master data is owned by the Tax subsystem.  RAG may read projects and
may mutate RAG-owned documentary resources scoped to an existing project, but
it must never create, synchronize, rename, or delete Project Master rows.

This module retires the remaining legacy write routes at application
composition time.  Keeping the guard in the composition layer makes the
boundary fail-closed even while ``legacy_routes`` is incrementally decomposed.
"""
from __future__ import annotations

from collections.abc import Iterable

from fastapi import FastAPI
from fastapi.routing import APIRoute

# Only these legacy Project Master mutations are forbidden.  RAG-owned
# documentary routes such as /api/v1/projects/{id}/documents/repair remain
# available because they do not mutate Project Master data.
_FORBIDDEN_PROJECT_MASTER_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/v1/projects"),
        ("POST", "/api/v1/projects/sync"),
        ("DELETE", "/api/v1/projects/{project_id}"),
        # NOTE: POST /api/v1/projects/{project_id}/delete is intentionally NOT
        # listed here.  It is implemented in app/routers/project_delete.py,
        # registered BEFORE enforce_project_master_read_only, and delegates to
        # Tax for the authoritative Project Master mutation while wiping RAG-owned
        # documentary resources locally.  This preserves the SSOT boundary:
        # RAG never writes to the projects table directly.
        ("POST", "/projects"),
    }
)


def _methods(route: APIRoute) -> Iterable[str]:
    return route.methods or set()


def enforce_project_master_read_only(app: FastAPI) -> tuple[tuple[str, str], ...]:
    """Remove legacy Project Master write routes and assert the SSOT boundary.

    The function is intentionally idempotent.  It removes only exact route /
    method pairs listed above, then validates that none survived.  Any future
    legacy route that reintroduces one of these mutations causes startup to
    fail instead of silently reopening a second Project Master writer.
    """
    kept = []
    removed: list[tuple[str, str]] = []

    for route in app.router.routes:
        if not isinstance(route, APIRoute):
            kept.append(route)
            continue

        forbidden_methods = {
            method
            for method in _methods(route)
            if (method.upper(), route.path) in _FORBIDDEN_PROJECT_MASTER_ROUTES
        }
        if forbidden_methods:
            removed.extend((method.upper(), route.path) for method in forbidden_methods)
            continue

        kept.append(route)

    app.router.routes[:] = kept

    survivors: list[tuple[str, str]] = []
    for route in app.router.routes:
        if not isinstance(route, APIRoute):
            continue
        survivors.extend(
            (method.upper(), route.path)
            for method in _methods(route)
            if (method.upper(), route.path) in _FORBIDDEN_PROJECT_MASTER_ROUTES
        )

    if survivors:
        raise RuntimeError(
            "Project Master SSOT boundary violation: forbidden RAG routes remain: "
            + ", ".join(f"{method} {path}" for method, path in sorted(survivors))
        )

    return tuple(sorted(set(removed)))


def assert_project_master_read_only(app: FastAPI) -> None:
    """Fail closed if a forbidden Project Master mutation is registered."""
    violations: list[tuple[str, str]] = []
    for route in app.router.routes:
        if not isinstance(route, APIRoute):
            continue
        violations.extend(
            (method.upper(), route.path)
            for method in _methods(route)
            if (method.upper(), route.path) in _FORBIDDEN_PROJECT_MASTER_ROUTES
        )
    if violations:
        raise RuntimeError(
            "Project Master SSOT boundary violation: "
            + ", ".join(f"{method} {path}" for method, path in sorted(violations))
        )
