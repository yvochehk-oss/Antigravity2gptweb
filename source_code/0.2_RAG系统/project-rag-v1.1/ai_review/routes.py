"""
ai_review routes (V1.1)

把 AI Review 暴露成 HTTP 端点（AI Review 本身已经是 service 层封装）。
主要给 metabase / 外部调度使用。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from sqlalchemy.orm import Session

from ai_review.db import Base
from ai_review.models import FactsSnapshot, AIReviewRun

router = APIRouter(prefix="/api/v1/ai-review", tags=["ai-review"])


class RunRequest(BaseModel):
    project_code: str = Field(..., description="项目编码")
    model: str = Field("gpt-4o", description="使用的 LLM 模型")
    prompt_version: str = Field("1.0", description="Prompt 版本")
    require_fresh: bool = Field(True, description="是否强制获取最新数据")


@router.post("/run")
async def run_review(req: RunRequest):
    """触发项目 AI 体检（同步版）"""
    from ai_review.review_service import AIReviewService
    from facts_provider.facts_provider import FactsProvider
    from app.db import SessionLocal

    db: Session = SessionLocal()
    try:
        fp = FactsProvider(db)
        svc = AIReviewService(db, fp)
        try:
            return svc.run_review(
                project_code=req.project_code,
                model=req.model,
                prompt_version=req.prompt_version,
                require_fresh=req.require_fresh,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.get("/projects/{project_code}/history")
async def review_history(project_code: str, limit: int = 10):
    """获取项目 AI Review 历史"""
    from ai_review.review_service import AIReviewService
    from facts_provider.facts_provider import FactsProvider
    from app.db import SessionLocal

    db: Session = SessionLocal()
    try:
        fp = FactsProvider(db)
        svc = AIReviewService(db, fp)
        return {"project_code": project_code, "history": svc.get_review_history(project_code, limit)}
    finally:
        db.close()


@router.get("/health")
async def health():
    """AI Review 模块健康检查"""
    return {"status": "ok", "module": "ai_review", "version": "1.1.0"}


__all__ = ["router", "Base"]