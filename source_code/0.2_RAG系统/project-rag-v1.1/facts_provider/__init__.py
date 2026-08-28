"""
Facts Provider 集成模块

将 Facts Provider 集成到 FastAPI 应用，并启用请求级批量 Facts 读取，
避免高管项目列表逐项目查询 analytics_project_full。
"""

from fastapi import FastAPI

from .routes import router as facts_router
from .bulk_loader import install_bulk_facts_loading

install_bulk_facts_loading()


def setup_facts_provider(app: FastAPI):
    """将 Facts Provider 路由注册到 FastAPI 应用"""
    app.include_router(facts_router)
    
    @app.get("/health/facts")
    async def facts_health():
        """Facts Provider health and canonical source availability."""

        from sqlalchemy import text

        from app.db import SessionLocal

        db = SessionLocal()
        try:
            try:
                db.execute(text("SELECT 1 FROM analytics_project_full LIMIT 1"))
            except Exception as exc:
                return {
                    "status": "degraded",
                    "service": "facts_provider",
                    "facts_available": False,
                    "source": "analytics_project_full",
                    "reason": f"analytics_project_full unavailable: {exc.__class__.__name__}",
                }
            return {
                "status": "ok",
                "service": "facts_provider",
                "facts_available": True,
                "source": "analytics_project_full",
            }
        finally:
            db.close()
