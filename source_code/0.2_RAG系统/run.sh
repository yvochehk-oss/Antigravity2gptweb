#!/bin/bash
# ProjectRAG V2.0 - Run Script
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RAG_DIR="$SCRIPT_DIR/project-rag-v1.1"
V2_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$RAG_DIR"

# Preserve explicitly supplied model paths.  The local .env is a convenient
# workstation default, but must not override a deployment/operator override.
_embedding_model_was_set="${PROJECT_RAG_EMBEDDING_MODEL+x}"
_embedding_model_override="${PROJECT_RAG_EMBEDDING_MODEL-}"
_reranker_model_was_set="${PROJECT_RAG_RERANKER_MODEL+x}"
_reranker_model_override="${PROJECT_RAG_RERANKER_MODEL-}"

# Load environment variables
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

if [ "$_embedding_model_was_set" = x ]; then
    export PROJECT_RAG_EMBEDDING_MODEL="$_embedding_model_override"
else
    export PROJECT_RAG_EMBEDDING_MODEL="${PROJECT_RAG_EMBEDDING_MODEL:-$V2_ROOT/models/bge-m3}"
fi
if [ "$_reranker_model_was_set" = x ]; then
    export PROJECT_RAG_RERANKER_MODEL="$_reranker_model_override"
else
    export PROJECT_RAG_RERANKER_MODEL="${PROJECT_RAG_RERANKER_MODEL:-$V2_ROOT/models/bge-reranker-v2-m3}"
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
    --port "${PROJECT_RAG_PORT:-8922}" \
    --reload
