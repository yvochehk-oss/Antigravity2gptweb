"""V0.2: 路由聚合。"""

from .cockpit import router as cockpit_router
from .matching import router as matching_router
from .tax import router as tax_router
from .ai_review import router as ai_review_router
from .health_check import router as health_check_router
from .tasks import router as tasks_router
from .prompts import router as prompts_router
from .models import router as models_router
from .api import router as api_router
from .rag_sync import router as rag_sync_router

ALL_ROUTERS = [
    cockpit_router, matching_router, tax_router,
    ai_review_router, health_check_router, tasks_router,
    prompts_router, models_router, api_router,
    rag_sync_router,
]

__all__ = ["ALL_ROUTERS"]