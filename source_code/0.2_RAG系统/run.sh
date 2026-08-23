#!/bin/bash
# ProjectRAG V0.2 Optimized - Run Script
set -e
cd "$(dirname "$0")"

# Load environment variables
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Create virtual environment if needed
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate

# Install dependencies
pip install -q -r requirements.txt

# Initialize demo project
python -m app.seed

# Start server
exec uvicorn app.main:app \
    --host "${PROJECT_RAG_HOST:-127.0.0.1}" \
    --port "${PROJECT_RAG_PORT:-8800}" \
    --reload
