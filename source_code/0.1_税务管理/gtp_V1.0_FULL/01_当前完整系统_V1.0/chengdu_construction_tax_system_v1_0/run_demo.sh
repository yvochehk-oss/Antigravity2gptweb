#!/bin/bash
# Tax V1.0 一键启动（Mac/Linux 通用）。使用 uv 管理依赖。
set -e
cd "$(dirname "$0")"

# 加载 .env（如果存在）
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# 演示模式：清库并重写数据（开发演示专用）
export TAX_SEED_MODE=demo

# 使用 uv 同步依赖
uv sync

# Seed 数据库（TAX_SEED_MODE=demo 触发清库）
uv run python -m app.seed

# 启动服务
uv run uvicorn app.main:app --host 127.0.0.1 --port "${TAX_PORT:-8921}"
