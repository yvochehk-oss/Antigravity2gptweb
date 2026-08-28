"""V0.2: Application entry point.

Imports app construction from wiring.py and registers startup helpers from startup.py.
Kept minimal so that compile-time checks are fast and the entry point is obvious.
"""
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


@app.get("/healthz", tags=["meta"])
def healthz() -> dict[str, object]:
    """Return a bounded, non-sensitive dependency health aggregate.

    The helpers are re-exported from this entry point intentionally: tests and
    operators can replace an individual check without touching the FastAPI
    route registration or performing a live dependency call.
    """
    components = {
        "db": _check_db(),
        "rag": _check_rag(),
        "facts": _check_facts(),
        "ai": _check_ai(),
    }
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
