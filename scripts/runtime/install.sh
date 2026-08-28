#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAX_DIR="$PROJECT_DIR/source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0"
RAG_DIR="$PROJECT_DIR/source_code/0.2_RAG系统/project-rag-v1.1"
BOSS_DIR="$PROJECT_DIR/source_code/0.3_老板端安卓App_天府掌舵"

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

echo "[install] Tax / RAG / Boss dependencies ready"
