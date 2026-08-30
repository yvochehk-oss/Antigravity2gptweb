#!/bin/bash
set -e
cd "$(dirname "$0")"
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-ai.txt
echo
echo "BGE 运行依赖已安装。首次检索/索引时会下载："
echo "  BAAI/bge-m3"
