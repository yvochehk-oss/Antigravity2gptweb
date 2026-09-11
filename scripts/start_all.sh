#!/usr/bin/env bash
# 成都建工 V3.0 runtime dispatcher.
set -Eeuo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$PROJECT_DIR/scripts/runtime"

usage() {
  cat <<'EOF'
用法：./start_all.sh [start|migrate|install|health] [选项]

默认：start
  start             启动本地 LLM、IDP、Tax、RAG（兼容原默认行为）
  migrate           先迁移，再启动（兼容原 --migrate）
  install           安装/同步 Tax、RAG、IDP、Boss 开发依赖
  health            只检查已运行的 IDP/Tax/RAG 健康端点

兼容旧参数：
  --migrate         等同 migrate
  --skip-postgres   传给启动引擎
  --no-local-llm    传给启动引擎
  --help            显示本帮助
EOF
}

command_name="${1:-start}"
case "$command_name" in
  start)
    shift || true
    exec "$RUNTIME_DIR/start.sh" "$@"
    ;;
  migrate)
    shift || true
    exec "$RUNTIME_DIR/migrate.sh" "$@"
    ;;
  install)
    shift || true
    exec "$RUNTIME_DIR/install.sh" "$@"
    ;;
  health)
    shift || true
    exec "$RUNTIME_DIR/health.sh" "$@"
    ;;
  --migrate)
    shift || true
    exec "$RUNTIME_DIR/migrate.sh" "$@"
    ;;
  --help|-h)
    usage
    ;;
  *)
    # Preserve legacy option-only invocation such as --skip-postgres.
    exec "$RUNTIME_DIR/start.sh" "$@"
    ;;
esac
