#!/bin/bash
# Installs MinerU into a separate local virtualenv so ProjectRAG dependencies stay isolated.
set -e
cd "$(dirname "$0")"
python3 -m venv .mineru-venv
source .mineru-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install uv
uv pip install -U "mineru[all]"
echo
echo "MinerU installed: $(pwd)/.mineru-venv/bin/mineru"
"$(pwd)/.mineru-venv/bin/mineru" --version || true
echo "ProjectRAG will auto-detect this local MinerU executable."
