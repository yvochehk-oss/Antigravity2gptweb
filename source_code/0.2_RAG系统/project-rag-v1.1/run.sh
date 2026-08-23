#!/bin/bash
# ProjectRAG V1.1 - Run Script (merged v0.2-optimized + v1.0 + v0.2 regulation)
set -e
cd "$(dirname "$0")"

# Load environment variables
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Sync dependencies with uv
uv sync

# Initialize demo project
uv run python -m app.seed 2>/dev/null || true

# Start server (single process: serves /api/v1/* RAG + /api/v1/facts + /api/v1/ai-review)
exec uv run uvicorn app.main:app \
    --host "${PROJECT_RAG_HOST:-127.0.0.1}" \
    --port "${PROJECT_RAG_PORT:-8922}" \
    --reload
