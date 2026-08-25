#!/usr/bin/env bash
# ProjectRAG V2.0 direct launcher (PostgreSQL-only, no auto-seed, no reload).
# The root V2.0/start_all.sh remains the preferred two-service launcher.

set -Eeuo pipefail

RAG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V2_ROOT="$(cd "$RAG_DIR/../../.." && pwd)"
PID_FILE="${PROJECT_RAG_PID_FILE:-$RAG_DIR/.rag.pid}"
LOG_FILE="${PROJECT_RAG_LOG_FILE:-$RAG_DIR/rag_runtime.log}"
_EMBEDDING_WAS_SET="${PROJECT_RAG_EMBEDDING_MODEL+x}"
_EMBEDDING_OVERRIDE="${PROJECT_RAG_EMBEDDING_MODEL-}"
_RERANKER_WAS_SET="${PROJECT_RAG_RERANKER_MODEL+x}"
_RERANKER_OVERRIDE="${PROJECT_RAG_RERANKER_MODEL-}"
_HOST_WAS_SET="${PROJECT_RAG_HOST+x}"
_HOST_OVERRIDE="${PROJECT_RAG_HOST-}"
_PORT_WAS_SET="${PROJECT_RAG_PORT+x}"
_PORT_OVERRIDE="${PROJECT_RAG_PORT-}"
_SHARED_KEY_WAS_SET="${RAG_SHARED_API_KEY+x}"
_SHARED_KEY_OVERRIDE="${RAG_SHARED_API_KEY-}"
_SHARED_KEY_FILE_WAS_SET="${RAG_SHARED_API_KEY_FILE+x}"
_SHARED_KEY_FILE_OVERRIDE="${RAG_SHARED_API_KEY_FILE-}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[rag]${NC} $1"; }
warn() { echo -e "${YELLOW}[rag]${NC} WARNING: $1" >&2; }
die()  { echo -e "${RED}[rag]${NC} ERROR: $1" >&2; exit 1; }

is_numeric_pid() {
  case "${1:-}" in
    ''|*[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

pid_alive() {
  is_numeric_pid "${1:-}" || return 1
  kill -0 "$1" >/dev/null 2>&1
}

port_listening() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  if command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 "$port" >/dev/null 2>&1
    return $?
  fi
  die "需要 lsof 或 nc 才能检查 RAG 端口"
}

if [ -f "$PID_FILE" ]; then
  existing_pid="$(tr -d '[:space:]' < "$PID_FILE")"
  if pid_alive "$existing_pid"; then
    die "RAG 已在运行（PID $existing_pid）；请先停止旧进程"
  fi
  rm -f "$PID_FILE"
fi

if [ -f "$RAG_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$RAG_DIR/.env"
  set +a
fi

# A direct launcher must fail before dependency preparation or server fork
# when an explicitly protected RAG instance has no usable shared credential.
# Preserve one-off process overrides across dotenv loading, matching the root
# launcher contract.  The Python configuration remains authoritative for file
# contents, permissions, and env/file mismatch validation.
if [ "$_SHARED_KEY_WAS_SET" = x ]; then
  export RAG_SHARED_API_KEY="$_SHARED_KEY_OVERRIDE"
fi
if [ "$_SHARED_KEY_FILE_WAS_SET" = x ]; then
  export RAG_SHARED_API_KEY_FILE="$_SHARED_KEY_FILE_OVERRIDE"
fi

is_truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

is_loopback_host() {
  case "${1:-}" in
    127.0.0.1|localhost|::1) return 0 ;;
    *) return 1 ;;
  esac
}

validate_shared_key_config() {
  local key_file="${RAG_SHARED_API_KEY_FILE:-}"
  local key="${RAG_SHARED_API_KEY:-}"
  local auth_requested=false
  if is_truthy "${PROJECT_RAG_AUTH_REQUIRED:-}"; then auth_requested=true; fi
  case "${APP_ENV:-}" in
    production|prod|staging|stage|preprod|pre-production) auth_requested=true ;;
  esac
  if ! is_loopback_host "${PROJECT_RAG_HOST:-127.0.0.1}"; then auth_requested=true; fi
  if [ -n "${RAG_API_KEY:-}" ] || [ -n "${PROJECT_RAG_API_KEY:-}" ]; then auth_requested=true; fi

  if [ -n "$key_file" ]; then
    case "$key_file" in
      /*) : ;;
      *) key_file="$RAG_DIR/$key_file" ;;
    esac
    [ -f "$key_file" ] && [ -r "$key_file" ] || \
      die "RAG_SHARED_API_KEY_FILE 已配置但文件不可读，拒绝启动"
    # A zero-byte file is never a valid credential.  The application performs
    # the stricter one-line and POSIX mode checks before serving requests.
    [ -s "$key_file" ] || die "RAG_SHARED_API_KEY_FILE 为空，拒绝启动"
  fi

  if [ "$auth_requested" = true ] && [ -z "$key" ] && [ -z "$key_file" ]; then
    die "RAG_SHARED_API_KEY 未配置（可使用 RAG_SHARED_API_KEY_FILE），拒绝启动"
  fi
}

validate_shared_key_config

if [ "$_HOST_WAS_SET" = x ]; then HOST="$_HOST_OVERRIDE"; else HOST="${PROJECT_RAG_HOST:-127.0.0.1}"; fi
if [ "$_PORT_WAS_SET" = x ]; then PORT="$_PORT_OVERRIDE"; else PORT="${PROJECT_RAG_PORT:-8922}"; fi
if port_listening "$PORT"; then
  die "RAG 端口 $PORT 已被占用"
fi

case "${PROJECT_RAG_DB_URL:-}" in
  postgresql://*|postgresql+*) ;;
  *) die "PROJECT_RAG_DB_URL 必须是 PostgreSQL 连接" ;;
esac

# Keep an explicit process-environment override.  If .env contains an old
# absolute path that no longer exists, derive the model path from this bundle.
if [ "$_EMBEDDING_WAS_SET" = x ]; then
  embedding_model="$_EMBEDDING_OVERRIDE"
elif [ -n "${PROJECT_RAG_EMBEDDING_MODEL:-}" ] && [ -f "${PROJECT_RAG_EMBEDDING_MODEL}/config.json" ]; then
  embedding_model="$PROJECT_RAG_EMBEDDING_MODEL"
else
  embedding_model="$V2_ROOT/models/bge-m3"
fi
if [ "$_RERANKER_WAS_SET" = x ]; then
  reranker_model="$_RERANKER_OVERRIDE"
elif [ -n "${PROJECT_RAG_RERANKER_MODEL:-}" ] && [ -f "${PROJECT_RAG_RERANKER_MODEL}/config.json" ]; then
  reranker_model="$PROJECT_RAG_RERANKER_MODEL"
else
  reranker_model="$V2_ROOT/models/bge-reranker-v2-m3"
fi
[ -f "$embedding_model/config.json" ] || die "Embedding 模型路径不可用：$embedding_model"
[ -f "$reranker_model/config.json" ] || die "Reranker 模型路径不可用：$reranker_model"
export PROJECT_RAG_EMBEDDING_MODEL="$embedding_model"
export PROJECT_RAG_RERANKER_MODEL="$reranker_model"

if command -v uv >/dev/null 2>&1; then
  (cd "$RAG_DIR" && uv sync --inexact)
else
  [ -x "$RAG_DIR/.venv/bin/python" ] || python3 -m venv "$RAG_DIR/.venv"
  "$RAG_DIR/.venv/bin/python" -m pip install -q -r "$RAG_DIR/requirements.txt"
fi

# No seed is performed here.  If an explicit project bootstrap is needed:
# PROJECT_RAG_SEED_PROJECT_CODE=... PROJECT_RAG_SEED_PROJECT_NAME=... \
# PROJECT_RAG_SEED_CONTRACT_AMOUNT=... PROJECT_RAG_SEED_LOCATION=... \
#   "$RAG_DIR/.venv/bin/python" -m app.seed

server_pid=""
cleanup() {
  local status="$?"
  trap - EXIT INT TERM
  if pid_alive "$server_pid"; then
    kill -TERM "$server_pid" >/dev/null 2>&1 || :
    local deadline=$((SECONDS + 15))
    while pid_alive "$server_pid" && [ "$SECONDS" -lt "$deadline" ]; do sleep 1; done
    if pid_alive "$server_pid"; then kill -KILL "$server_pid" >/dev/null 2>&1 || :; fi
  fi
  if [ -f "$PID_FILE" ] && [ "$(tr -d '[:space:]' < "$PID_FILE")" = "$$" ]; then
    rm -f "$PID_FILE"
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

log "启动 RAG（${HOST}:${PORT}），模型路径已解析为当前 V2.0 bundle"
"$RAG_DIR/.venv/bin/python" -m uvicorn app.main:app --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 &
server_pid="$!"
echo "$$" > "$PID_FILE"
wait "$server_pid"
