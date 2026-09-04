"""Contract tests for the retired RAG Project Master delete surface.

Project lifecycle is owned by the Tax system. RAG may read project master data
and mutate RAG-owned documentary data, but it must not expose Project Master
create, sync, or delete endpoints.
"""

from pathlib import Path

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROUTES = ROOT / "app" / "legacy_routes.py"

FORBIDDEN_PROJECT_MASTER_ROUTES = {
    ("POST", "/api/v1/projects"),
    ("POST", "/api/v1/projects/sync"),
    ("DELETE", "/api/v1/projects/{project_id}"),
    ("POST", "/api/v1/projects/{project_id}/delete"),
    ("POST", "/projects"),
}

FORBIDDEN_DECORATORS = (
    '@app.post("/api/v1/projects")',
    '@app.post("/api/v1/projects/sync")',
    '@app.delete("/api/v1/projects/{project_id}")',
    '@app.post("/api/v1/projects/{project_id}/delete")',
    '@app.post("/projects")',
)

RETIRED_PASSWORD_LITERALS = (
    "888888",
    "admin123",
    "123456",
    "cdjg@2026",
    "Admin@2026",
)


def _registered_methods() -> set[tuple[str, str]]:
    registered: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path:
            continue
        for method in methods:
            registered.add((str(method).upper(), str(path)))
    return registered


def test_project_master_mutation_decorators_are_removed_from_legacy_source() -> None:
    source = LEGACY_ROUTES.read_text(encoding="utf-8")

    for decorator in FORBIDDEN_DECORATORS:
        assert decorator not in source


def test_project_master_delete_routes_are_not_registered_in_production_app() -> None:
    registered = _registered_methods()

    assert FORBIDDEN_PROJECT_MASTER_ROUTES.isdisjoint(registered)


def test_project_master_read_routes_remain_available() -> None:
    registered = _registered_methods()

    assert ("GET", "/api/v1/projects") in registered
    assert ("GET", "/api/v1/projects/{project_id}") in registered
    assert ("GET", "/api/v1/projects/{project_id}/audit") in registered


def test_rag_owned_project_document_repair_route_remains_available() -> None:
    """Read-only Project Master does not prohibit RAG-owned document repair."""
    registered = _registered_methods()

    assert ("POST", "/api/v1/projects/{project_id}/documents/repair") in registered


def test_retired_project_delete_password_fallbacks_are_absent() -> None:
    source = LEGACY_ROUTES.read_text(encoding="utf-8")

    for literal in RETIRED_PASSWORD_LITERALS:
        assert literal not in source
