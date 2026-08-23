#!/bin/bash
set -e
cd "$(dirname "$0")/.."
python3 -m venv .test-venv 2>/dev/null || true
source .test-venv/bin/activate
python -m pip install -q -r requirements.txt -r requirements-dev.txt
PROJECT_RAG_EMBEDDING_BACKEND=hash_v1 PROJECT_RAG_RERANKER_BACKEND=off pytest -q
