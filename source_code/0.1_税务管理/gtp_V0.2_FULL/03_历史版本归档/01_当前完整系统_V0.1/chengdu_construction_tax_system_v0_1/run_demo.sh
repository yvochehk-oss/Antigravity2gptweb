#!/bin/bash
set -e
cd "$(dirname "$0")"
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip -q install -r requirements.txt
export DATABASE_URL=${DATABASE_URL:-sqlite:///./data/demo.db}
python -m app.seed
uvicorn app.main:app --host 127.0.0.1 --port 8765
