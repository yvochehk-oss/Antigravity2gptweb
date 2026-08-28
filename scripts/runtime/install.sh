#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAX_DIR="$PROJECT_DIR/source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0"
RAG_DIR="$PROJECT_DIR/source_code/0.2_RAG系统/project-rag-v1.1"
BOSS_DIR="$PROJECT_DIR/source_code/0.3_老板端安卓App_天府掌舵"
IDP_DIR="$PROJECT_DIR/source_code/0.4_IDP文档录入引擎_V3.0"

command -v uv >/dev/null 2>&1 || { echo "[install] 缺少 uv" >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "[install] 缺少 npm" >&2; exit 1; }

# pyproject.toml is authoritative.  Do not use --frozen here: when dependency
# declarations change this command is the explicit place that refreshes uv.lock.
(
  cd "$TAX_DIR"
  uv sync --extra dev
)
(
  cd "$RAG_DIR"
  uv sync --extra dev
)
(
  cd "$BOSS_DIR"
  npm ci
)

# IDP currently uses a requirements file rather than a pyproject.toml.  Keep
# its isolated environment independent from Tax/RAG and install the test
# dependencies used by the CI gate as well.
PYTHON_BIN="${PYTHON_BIN:-python3}"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "[install] 缺少 $PYTHON_BIN" >&2; exit 1; }
IDP_VENV="$IDP_DIR/.venv"
IDP_MARKER="$IDP_VENV/.idp-v3-ready"
if [ ! -x "$IDP_VENV/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$IDP_VENV"
fi
if [ ! -f "$IDP_MARKER" ] || [ "$IDP_DIR/requirements-dev-v3.txt" -nt "$IDP_MARKER" ] || [ "$IDP_DIR/requirements-v3.txt" -nt "$IDP_MARKER" ]; then
  "$IDP_VENV/bin/python" -m pip install --upgrade pip
  "$IDP_VENV/bin/python" -m pip install -r "$IDP_DIR/requirements-dev-v3.txt"
  touch "$IDP_MARKER"
fi

echo "[install] Tax / RAG / IDP / Boss dependencies ready"
