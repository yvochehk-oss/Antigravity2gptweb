#!/bin/bash
# 停止 Tax V1.0 + RAG v1.1
# 用法: ./stop_all.sh

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'
log() { echo -e "${GREEN}[stop_all]${NC} $1"; }

stop_service() {
  local name="$1"; shift
  for pid in "$@"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null && log "$name (PID $pid) 已停止" || true
    fi
  done
}

log "停止 Tax V1.0..."
[ -f "$PROJECT_DIR/.tax.pid" ] && stop_service "Tax" $(cat "$PROJECT_DIR/.tax.pid")

log "停止 RAG v1.1..."
[ -f "$PROJECT_DIR/.rag.pid" ] && stop_service "RAG" $(cat "$PROJECT_DIR/.rag.pid")

log "清理 PID 文件..."
rm -f "$PROJECT_DIR/.tax.pid" "$PROJECT_DIR/.rag.pid"

log "全部服务已停止"
