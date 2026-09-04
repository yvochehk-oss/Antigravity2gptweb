"""V0.2: Application entry point.

Imports app construction from wiring.py and registers startup helpers from startup.py.
Kept minimal so that compile-time checks are fast and the entry point is obvious.
"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from .services.phase3_retirement import install_phase3_retirement
from .startup import (
    RequestIdMiddleware,
    _check_ai,
    _check_db,
    _check_facts,
    _check_rag,
)
from .time_types import apply_timezone_types
from .wiring import create_app

# Routers import the model graph before this entry point reaches app creation.
# Upgrade legacy *_at VARCHAR mappings to TIMESTAMPTZ-compatible types without
# forcing every existing call site to stop using ISO strings in one release.
apply_timezone_types()
app = create_app()
install_phase3_retirement(app)


def _collect_health_components() -> dict[str, dict[str, object]]:
    """Run dependency probes concurrently while preserving request context.

    Each individual probe already has its own bounded timeout.  Running them
    serially made ``/healthz`` take the sum of the RAG, Facts, and AI budgets,
    so a slow AI endpoint could make the macOS controller time out even when
    Tax itself was still serving requests.  A copied context keeps the
    request-id header propagation of the serial implementation intact.
    """
    checks = {
        "db": _check_db,
        "rag": _check_rag,
        "facts": _check_facts,
        "ai": _check_ai,
    }
    with ThreadPoolExecutor(
        max_workers=len(checks),
        thread_name_prefix="tax-health",
    ) as executor:
        futures = {
            name: executor.submit(copy_context().run, check)
            for name, check in checks.items()
        }
        return {name: future.result() for name, future in futures.items()}


@app.get("/healthz", tags=["meta"])
def healthz() -> dict[str, object]:
    """Return a bounded, non-sensitive dependency health aggregate.

    The helpers are re-exported from this entry point intentionally: tests and
    operators can replace an individual check without touching the FastAPI
    route registration or performing a live dependency call.
    """
    components = _collect_health_components()
    priority = {"ok": 0, "degraded": 1, "down": 2}
    worst = "ok"
    for component in components.values():
        status = str(component.get("status", "down"))
        if priority.get(status, 2) > priority[worst]:
            worst = status
    return {
        "status": worst,
        "overall": worst,
        "degraded": worst != "ok",
        "version": app.version,
        "components": components,
    }


app.add_middleware(RequestIdMiddleware)
