"""ProjectRAG application composition entrypoint.

The legacy route surface is kept in ``legacy_routes`` while routes are migrated
incrementally into focused router modules. Keeping this module small preserves
the public ``app.main:app`` startup contract and prevents new endpoint logic
from accumulating in the composition layer.
"""
from __future__ import annotations

from . import legacy_routes as _legacy
from .project_master_boundary import (
    assert_project_master_read_only,
    enforce_project_master_read_only,
)
from .routers.health import install_health_routes
from .routers.phase3_retirement import install_phase3_retirement
from .routers.phase4_canonical import router as phase4_canonical_router
from .routers.project_delete import router as project_delete_router
from .time_types import apply_timezone_types

# The legacy route surface has imported the full mapped model graph at this
# point. Upgrade legacy *_at mappings before the application starts serving.
apply_timezone_types()
app = _legacy.app

# Register project-delete router FIRST so the route is available before the SSOT
# boundary validation runs.  The route is the only permitted RAG mutation that
# reaches the project scope; it forwards to Tax for the authoritative write.
app.include_router(project_delete_router)

# Tax owns Project Master.  Retire the remaining legacy RAG create/sync/delete
# endpoints before any additional canonical routers are composed.  The final
# assertion is deliberately repeated after composition so a future router
# cannot accidentally reopen a second Project Master writer.
enforce_project_master_read_only(app)
install_phase3_retirement(app)
app.include_router(phase4_canonical_router)
install_health_routes(app)
assert_project_master_read_only(app)


def __getattr__(name: str):
    """Preserve imports of legacy helpers while routers are extracted."""
    return getattr(_legacy, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_legacy)))


__all__ = ["app"]
