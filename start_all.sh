#!/bin/bash
# 成都建工 一键启动：Tax V1.0 + RAG v1.1 + PostgreSQL（可选）
# 用法: ./start_all.sh [--skip-postgres]

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKIP_POSTGRES=false

for arg in "$@"; do
  case $arg in
    --skip-postgres) SKIP_POSTGRES=true ;;
  esac
done

# 颜色
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

log()  { echo -e "${GREEN}[start_all]${NC} $1"; }
warn() { echo -e "${YELLOW}[start_all]${NC} WARNING: $1"; }
die()  { echo -e "${RED}[start_all]${NC} ERROR: $1" >&2; exit 1; }

# ---- PostgreSQL ----
start_postgres() {
  log "启动 PostgreSQL..."
  "$PROJECT_DIR/start_postgres.sh"
  # 等待就绪
  for i in {1..10}; do
    if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
      log "PostgreSQL 已就绪"
      return 0
    fi
    sleep 1
  done
  warn "PostgreSQL 启动超时（可能已由其他方式管理）"
}

# ---- Tax V1.0 ----
start_tax() {
  log "启动 Tax V1.0 (端口 8921)..."
  cd "$PROJECT_DIR/source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0"
  # 加载 .env
  set -a; source .env 2>/dev/null || true; set +a
  # uv sync（依赖已同步则秒过）
  uv sync
  # Seed 数据
  uv run python -m app.seed 2>/dev/null || true
  # 启动
  uv run uvicorn app.main:app --host 127.0.0.1 --port "${TAX_PORT:-8921}" &
  echo $! > "$PROJECT_DIR/.tax.pid"
  log "Tax V1.0 已启动 (PID: $(cat $PROJECT_DIR/.tax.pid))"
}

# ---- RAG v1.1 ----
start_rag() {
  log "启动 RAG v1.1 (端口 8922)..."
  cd "$PROJECT_DIR/source_code/0.2_RAG系统/project-rag-v1.1"
  # Preserve operator-supplied model paths while defaulting to the V2.0 bundle.
  _rag_embedding_model_was_set="${PROJECT_RAG_EMBEDDING_MODEL+x}"
  _rag_embedding_model_override="${PROJECT_RAG_EMBEDDING_MODEL-}"
  _rag_reranker_model_was_set="${PROJECT_RAG_RERANKER_MODEL+x}"
  _rag_reranker_model_override="${PROJECT_RAG_RERANKER_MODEL-}"
  set -a; source .env 2>/dev/null || true; set +a
  if [ "$_rag_embedding_model_was_set" = x ]; then
    export PROJECT_RAG_EMBEDDING_MODEL="$_rag_embedding_model_override"
  else
    export PROJECT_RAG_EMBEDDING_MODEL="${PROJECT_RAG_EMBEDDING_MODEL:-$PROJECT_DIR/models/bge-m3}"
  fi
  if [ "$_rag_reranker_model_was_set" = x ]; then
    export PROJECT_RAG_RERANKER_MODEL="$_rag_reranker_model_override"
  else
    export PROJECT_RAG_RERANKER_MODEL="${PROJECT_RAG_RERANKER_MODEL:-$PROJECT_DIR/models/bge-reranker-v2-m3}"
  fi
  uv sync
  uv run python -m app.seed 2>/dev/null || true
  uv run uvicorn app.main:app \
    --host "${PROJECT_RAG_HOST:-127.0.0.1}" \
    --port "${PROJECT_RAG_PORT:-8922}" \
    --reload &
  echo $! > "$PROJECT_DIR/.rag.pid"
  log "RAG v1.1 已启动 (PID: $(cat $PROJECT_DIR/.rag.pid))"
}

# ---- 主流程 ----
log "=== 成都建工 全量启动 ==="

if [ "$SKIP_POSTGRES" = false ]; then
  start_postgres
else
  warn "跳过 PostgreSQL（使用 SQLite 模式）"
fi

start_tax
start_rag

sleep 3

# ---- 汇总 ----
echo ""
log "=== 全部服务已启动 ==="
echo "  Tax V1.0 : http://127.0.0.1:8921"
echo "  RAG v1.1 : http://127.0.0.1:8922"
echo "  PostgreSQL : 127.0.0.1:5432 (仅 PostgreSQL 模式)"
echo ""
echo "停止命令: kill \$(cat $PROJECT_DIR/.tax.pid) \$(cat $PROJECT_DIR/.rag.pid)"
echo "或直接关闭此终端"
