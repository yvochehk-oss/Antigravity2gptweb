"""V0.2: 应用入口。"""
from __future__ import annotations

from fastapi import FastAPI

from .routers import ALL_ROUTERS
from .structured_logging import configure_logging

configure_logging()

app = FastAPI(
    title="建筑项目经营与税务统筹系统",
    version="2.5",
    description=(
        "V0.2: 确定性引擎 + AI 审查 + 整改闭环。"
        "AI 仅负责解释、审查和建议，不覆盖确定性数字。"
    ),
)

for r in ALL_ROUTERS:
    app.include_router(r)


@app.get("/healthz", tags=["meta"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": app.version}