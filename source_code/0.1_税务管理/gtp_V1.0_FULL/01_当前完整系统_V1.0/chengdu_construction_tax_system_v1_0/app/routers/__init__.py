"""V0.2: 路由聚合。"""

from .ai_review import router as ai_review_router
from .api import router as api_router
from .auth import router as auth_router
from .canonical_ssot import router as canonical_ssot_router
from .cockpit import router as cockpit_router
from .collections import router as collections_router
from .health_check import router as health_check_router
from .manager import router as manager_router
from .matching import router as matching_router
from .models import router as models_router
from .phase4_accounting import router as phase4_accounting_router
from .planning import router as planning_router
from .prompts import router as prompts_router
from .rag_sync import router as rag_sync_router
from .ssot_reconciliation import router as ssot_reconciliation_router
from .tasks import router as tasks_router
from .tax import router as tax_router
from .users import router as users_router
from .v3_canonical import router as v3_canonical_router
from ..user_center.router import router as user_center_router

ALL_ROUTERS = [
    auth_router,
    cockpit_router,
    collections_router,
    matching_router,
    tax_router,
    ai_review_router,
    health_check_router,
    manager_router,
    planning_router,
    tasks_router,
    prompts_router,
    models_router,
    api_router,
    rag_sync_router,
    canonical_ssot_router,
    ssot_reconciliation_router,
    phase4_accounting_router,
    v3_canonical_router,
    users_router,
    user_center_router,
]

__all__ = ["ALL_ROUTERS"]
