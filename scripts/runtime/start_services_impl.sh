#!/usr/bin/env bash
# 成都建工 V3.0 一键启动：IDP + Tax + RAG + PostgreSQL
#
# 默认启动本地 llama.cpp 保底模型、IDP、Tax 与 RAG，不迁移、不 seed、不重置业务数据。
# 首次使用或数据库结构变更时显式执行：
#   ./start_all.sh --migrate
#
# 只允许 PostgreSQL；本地 LLM 缺失时明确报告 DEGRADED，但不阻断业务服务。

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAX_DIR="$PROJECT_DIR/source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0"
RAG_DIR="$PROJECT_DIR/source_code/0.2_RAG系统/project-rag-v1.1"
IDP_DIR="$PROJECT_DIR/source_code/0.4_IDP文档录入引擎_V3.0"
ROOT_ENV_FILE="$PROJECT_DIR/.env"
TAX_PID_FILE="$PROJECT_DIR/.tax.pid"
RAG_PID_FILE="$PROJECT_DIR/.rag.pid"
IDP_PID_FILE="$PROJECT_DIR/.idp.pid"
LOCAL_LLM_PID_FILE="$PROJECT_DIR/.local_llm.pid"
TAX_LOG_FILE="$PROJECT_DIR/tax_runtime.log"
RAG_LOG_FILE="$PROJECT_DIR/rag_runtime.log"
IDP_LOG_FILE="$PROJECT_DIR/idp_runtime.log"
LOCAL_LLM_LOG_FILE="$PROJECT_DIR/local_llm_runtime.log"

# Root .env contains only orchestration/local-LLM settings.  Preserve values
# explicitly supplied by the caller so a one-off command-line override wins.
_LOCAL_LLM_ENABLED_WAS_SET="${LOCAL_LLM_ENABLED+x}"
_LOCAL_LLM_ENABLED_OVERRIDE="${LOCAL_LLM_ENABLED-}"
_LOCAL_LLM_HOST_WAS_SET="${LOCAL_LLM_HOST+x}"
_LOCAL_LLM_HOST_OVERRIDE="${LOCAL_LLM_HOST-}"
_LOCAL_LLM_PORT_WAS_SET="${LOCAL_LLM_PORT+x}"
_LOCAL_LLM_PORT_OVERRIDE="${LOCAL_LLM_PORT-}"
_LOCAL_LLM_MODEL_WAS_SET="${LOCAL_LLM_MODEL+x}"
_LOCAL_LLM_MODEL_OVERRIDE="${LOCAL_LLM_MODEL-}"
_LOCAL_LLM_SERVER_WAS_SET="${LOCAL_LLM_SERVER_BIN+x}"
_LOCAL_LLM_SERVER_OVERRIDE="${LOCAL_LLM_SERVER_BIN-}"
_LOCAL_LLM_ALIAS_WAS_SET="${LOCAL_LLM_ALIAS+x}"
_LOCAL_LLM_ALIAS_OVERRIDE="${LOCAL_LLM_ALIAS-}"
_LOCAL_LLM_CTX_WAS_SET="${LOCAL_LLM_CTX_SIZE+x}"
_LOCAL_LLM_CTX_OVERRIDE="${LOCAL_LLM_CTX_SIZE-}"
_LOCAL_LLM_THREADS_WAS_SET="${LOCAL_LLM_THREADS+x}"
_LOCAL_LLM_THREADS_OVERRIDE="${LOCAL_LLM_THREADS-}"
_LOCAL_LLM_THREADS_BATCH_WAS_SET="${LOCAL_LLM_THREADS_BATCH+x}"
_LOCAL_LLM_THREADS_BATCH_OVERRIDE="${LOCAL_LLM_THREADS_BATCH-}"
_LOCAL_LLM_BATCH_WAS_SET="${LOCAL_LLM_BATCH_SIZE+x}"
_LOCAL_LLM_BATCH_OVERRIDE="${LOCAL_LLM_BATCH_SIZE-}"
_LOCAL_LLM_UBATCH_WAS_SET="${LOCAL_LLM_UBATCH_SIZE+x}"
_LOCAL_LLM_UBATCH_OVERRIDE="${LOCAL_LLM_UBATCH_SIZE-}"
_LOCAL_LLM_GPU_WAS_SET="${LOCAL_LLM_GPU_LAYERS+x}"
_LOCAL_LLM_GPU_OVERRIDE="${LOCAL_LLM_GPU_LAYERS-}"
_LOCAL_LLM_REASONING_WAS_SET="${LOCAL_LLM_REASONING+x}"
_LOCAL_LLM_REASONING_OVERRIDE="${LOCAL_LLM_REASONING-}"
_LOCAL_LLM_TIMEOUT_WAS_SET="${LOCAL_LLM_STARTUP_TIMEOUT_SECONDS+x}"
_LOCAL_LLM_TIMEOUT_OVERRIDE="${LOCAL_LLM_STARTUP_TIMEOUT_SECONDS-}"
if [ -f "$ROOT_ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ROOT_ENV_FILE"
  set +a
fi
if [ "$_LOCAL_LLM_ENABLED_WAS_SET" = x ]; then export LOCAL_LLM_ENABLED="$_LOCAL_LLM_ENABLED_OVERRIDE"; fi
if [ "$_LOCAL_LLM_HOST_WAS_SET" = x ]; then export LOCAL_LLM_HOST="$_LOCAL_LLM_HOST_OVERRIDE"; fi
if [ "$_LOCAL_LLM_PORT_WAS_SET" = x ]; then export LOCAL_LLM_PORT="$_LOCAL_LLM_PORT_OVERRIDE"; fi
if [ "$_LOCAL_LLM_MODEL_WAS_SET" = x ]; then export LOCAL_LLM_MODEL="$_LOCAL_LLM_MODEL_OVERRIDE"; fi
if [ "$_LOCAL_LLM_SERVER_WAS_SET" = x ]; then export LOCAL_LLM_SERVER_BIN="$_LOCAL_LLM_SERVER_OVERRIDE"; fi
if [ "$_LOCAL_LLM_ALIAS_WAS_SET" = x ]; then export LOCAL_LLM_ALIAS="$_LOCAL_LLM_ALIAS_OVERRIDE"; fi
if [ "$_LOCAL_LLM_CTX_WAS_SET" = x ]; then export LOCAL_LLM_CTX_SIZE="$_LOCAL_LLM_CTX_OVERRIDE"; fi
if [ "$_LOCAL_LLM_THREADS_WAS_SET" = x ]; then export LOCAL_LLM_THREADS="$_LOCAL_LLM_THREADS_OVERRIDE"; fi
if [ "$_LOCAL_LLM_THREADS_BATCH_WAS_SET" = x ]; then export LOCAL_LLM_THREADS_BATCH="$_LOCAL_LLM_THREADS_BATCH_OVERRIDE"; fi
if [ "$_LOCAL_LLM_BATCH_WAS_SET" = x ]; then export LOCAL_LLM_BATCH_SIZE="$_LOCAL_LLM_BATCH_OVERRIDE"; fi
if [ "$_LOCAL_LLM_UBATCH_WAS_SET" = x ]; then export LOCAL_LLM_UBATCH_SIZE="$_LOCAL_LLM_UBATCH_OVERRIDE"; fi
if [ "$_LOCAL_LLM_GPU_WAS_SET" = x ]; then export LOCAL_LLM_GPU_LAYERS="$_LOCAL_LLM_GPU_OVERRIDE"; fi
if [ "$_LOCAL_LLM_REASONING_WAS_SET" = x ]; then export LOCAL_LLM_REASONING="$_LOCAL_LLM_REASONING_OVERRIDE"; fi
if [ "$_LOCAL_LLM_TIMEOUT_WAS_SET" = x ]; then export LOCAL_LLM_STARTUP_TIMEOUT_SECONDS="$_LOCAL_LLM_TIMEOUT_OVERRIDE"; fi

_TAX_PORT_WAS_SET="${TAX_PORT+x}"
_TAX_PORT_OVERRIDE="${TAX_PORT-}"
_RAG_PORT_WAS_SET="${PROJECT_RAG_PORT+x}"
_RAG_PORT_OVERRIDE="${PROJECT_RAG_PORT-}"
_RAG_EMBEDDING_WAS_SET="${PROJECT_RAG_EMBEDDING_MODEL+x}"
_RAG_EMBEDDING_OVERRIDE="${PROJECT_RAG_EMBEDDING_MODEL-}"
_RAG_RERANKER_WAS_SET="${PROJECT_RAG_RERANKER_MODEL+x}"
_RAG_RERANKER_OVERRIDE="${PROJECT_RAG_RERANKER_MODEL-}"
_DATABASE_URL_WAS_SET="${DATABASE_URL+x}"
_DATABASE_URL_OVERRIDE="${DATABASE_URL-}"
_RAG_DB_URL_WAS_SET="${PROJECT_RAG_DB_URL+x}"
_RAG_DB_URL_OVERRIDE="${PROJECT_RAG_DB_URL-}"
_RAG_HOST_WAS_SET="${PROJECT_RAG_HOST+x}"
_RAG_HOST_OVERRIDE="${PROJECT_RAG_HOST-}"
_RAG_WORKER_WAS_SET="${PROJECT_RAG_AUTO_START_WORKER+x}"
_RAG_WORKER_OVERRIDE="${PROJECT_RAG_AUTO_START_WORKER-}"
_IDP_ENABLED_WAS_SET="${IDP_ENABLED+x}"
_IDP_ENABLED_OVERRIDE="${IDP_ENABLED-}"
_IDP_HOST_WAS_SET="${IDP_HOST+x}"
_IDP_HOST_OVERRIDE="${IDP_HOST-}"
_IDP_PORT_WAS_SET="${IDP_PORT+x}"
_IDP_PORT_OVERRIDE="${IDP_PORT-}"
_TAX_RAG_URL_WAS_SET="${TAX_RAG_SERVICE_URL+x}"
_TAX_RAG_URL_OVERRIDE="${TAX_RAG_SERVICE_URL-}"
_TAX_FACTS_URL_WAS_SET="${TAX_RAG_V1_FACTS_URL+x}"
_TAX_FACTS_URL_OVERRIDE="${TAX_RAG_V1_FACTS_URL-}"
_APP_ENV_WAS_SET="${APP_ENV+x}"
_APP_ENV_OVERRIDE="${APP_ENV-}"
_SHARED_KEY_WAS_SET="${RAG_SHARED_API_KEY+x}"
_SHARED_KEY_OVERRIDE="${RAG_SHARED_API_KEY-}"
_SHARED_KEY_FILE_WAS_SET="${RAG_SHARED_API_KEY_FILE+x}"
_SHARED_KEY_FILE_OVERRIDE="${RAG_SHARED_API_KEY_FILE-}"
_JWT_SECRET_WAS_SET="${JWT_SECRET_KEY+x}"
_JWT_SECRET_OVERRIDE="${JWT_SECRET_KEY-}"
TAX_PORT=""
RAG_PORT=""
IDP_PORT=""
EFFECTIVE_TAX_PORT=""
EFFECTIVE_RAG_PORT=""
EFFECTIVE_IDP_PORT=""
JWT_SECRET_MIN_LENGTH=32
STARTUP_TIMEOUT_SECONDS="${STARTUP_TIMEOUT_SECONDS:-180}"
MANAGE_POSTGRES=true
RUN_MIGRATIONS=false
STARTED_TAX_PID=""
STARTED_RAG_PID=""
STARTED_IDP_PID=""
STARTED_LOCAL_LLM_PID=""
LOCAL_LLM_ACTIVE=false

LOCAL_LLM_ENABLED="${LOCAL_LLM_ENABLED:-1}"
LOCAL_LLM_HOST="${LOCAL_LLM_HOST:-127.0.0.1}"
LOCAL_LLM_PORT="${LOCAL_LLM_PORT:-8930}"
DEFAULT_LOCAL_MODEL=""
DEFAULT_LOCAL_ALIAS=""
if [ -f "$PROJECT_DIR/models/local-llm/Spark-X2.5-4B-Q4_K_M.gguf" ]; then
  DEFAULT_LOCAL_MODEL="$PROJECT_DIR/models/local-llm/Spark-X2.5-4B-Q4_K_M.gguf"
  DEFAULT_LOCAL_ALIAS="Spark-X2.5-4B"
elif [ -f "$PROJECT_DIR/models/local-llm/Ling-3.0-tiny-Q4_K_M.gguf" ]; then
  DEFAULT_LOCAL_MODEL="$PROJECT_DIR/models/local-llm/Ling-3.0-tiny-Q4_K_M.gguf"
  DEFAULT_LOCAL_ALIAS="ling-3.0-tiny"
elif [ -f "$PROJECT_DIR/models/local-llm/Qwen3.5-2B-Q4_K_M.gguf" ]; then
  DEFAULT_LOCAL_MODEL="$PROJECT_DIR/models/local-llm/Qwen3.5-2B-Q4_K_M.gguf"
  DEFAULT_LOCAL_ALIAS="local-qwen3.5-2b"
fi
LOCAL_LLM_MODEL="${LOCAL_LLM_MODEL:-$DEFAULT_LOCAL_MODEL}"
LOCAL_LLM_SERVER_BIN="${LOCAL_LLM_SERVER_BIN:-$PROJECT_DIR/models/local-llm/runtime-macos-arm64/llama-server}"
LOCAL_LLM_ALIAS="${LOCAL_LLM_ALIAS:-$DEFAULT_LOCAL_ALIAS}"
LOCAL_LLM_CTX_SIZE="${LOCAL_LLM_CTX_SIZE:-16384}"
LOCAL_LLM_THREADS="${LOCAL_LLM_THREADS:-6}"
LOCAL_LLM_THREADS_BATCH="${LOCAL_LLM_THREADS_BATCH:-6}"
LOCAL_LLM_BATCH_SIZE="${LOCAL_LLM_BATCH_SIZE:-1024}"
LOCAL_LLM_UBATCH_SIZE="${LOCAL_LLM_UBATCH_SIZE:-512}"
LOCAL_LLM_GPU_LAYERS="${LOCAL_LLM_GPU_LAYERS:-99}"
LOCAL_LLM_REASONING="${LOCAL_LLM_REASONING:-off}"
LOCAL_LLM_STARTUP_TIMEOUT_SECONDS="${LOCAL_LLM_STARTUP_TIMEOUT_SECONDS:-180}"
IDP_ENABLED="${IDP_ENABLED:-1}"
IDP_HOST="${IDP_HOST:-127.0.0.1}"
IDP_PORT="${IDP_PORT:-8933}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[start_all]${NC} $1"; }
warn() { echo -e "${YELLOW}[start_all]${NC} WARNING: $1" >&2; }
die()  { echo -e "${RED}[start_all]${NC} ERROR: $1" >&2; exit 1; }

usage() {
  cat <<'EOF'
用法：./start_all.sh [选项]

默认行为：启动本地 llama.cpp 保底模型、IDP、Tax 与 RAG，不执行迁移，不执行 seed，不修改业务数据。

选项：
  --migrate         先执行 Tax Alembic、RAG Alembic 和 IDP schema，然后启动服务
  --skip-postgres   不管理 PostgreSQL 进程，但仍要求 PostgreSQL 已就绪
  --no-local-llm    本次启动跳过本地 llama.cpp（等同于 LOCAL_LLM_ENABLED=0）
  --help            显示帮助

如果需要显式初始化业务主数据，请在完成迁移后单独运行对应 seed 命令，
并先确认环境变量和数据库对象；启动脚本不会自动写入示范项目。
EOF
}

for arg in "$@"; do
  case "$arg" in
    --migrate) RUN_MIGRATIONS=true ;;
    --skip-postgres) MANAGE_POSTGRES=false ;;
    --no-local-llm) LOCAL_LLM_ENABLED=0 ;;
    --help|-h) usage; exit 0 ;;
    *) die "未知参数：${arg}（使用 --help 查看用法）" ;;
  esac
done

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

pid_command() {
  ps -p "$1" -o command= 2>/dev/null || :
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
  die "需要 lsof 或 nc 才能检查端口 ${port} 是否被占用"
}

expected_service_process() {
  local pid="$1" port="$2"
  local command
  command="$(pid_command "$pid")"
  case "$command" in
    *uvicorn*"--port $port"*|*uvicorn*"--port"$'\t'"$port"*) return 0 ;;
    *) return 1 ;;
  esac
}

prepare_service_slot() {
  local name="$1" pid_file="$2" port="$3"
  local pid
  if [ -f "$pid_file" ]; then
    pid="$(tr -d '[:space:]' < "$pid_file")"
    if pid_alive "$pid"; then
      if expected_service_process "$pid" "$port"; then
        die "$name 已在运行（PID ${pid}，端口 ${port}）"
      fi
      die "$name PID 文件 ${pid_file} 指向了非本项目进程（PID ${pid}）；请人工确认后处理"
    fi
    rm -f "$pid_file"
  fi
  if port_listening "$port"; then
    die "$name 端口 ${port} 已被占用，未启动任何新进程"
  fi
}

load_env_file() {
  local env_file="$1"
  if [ -f "$env_file" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  fi
}

validate_jwt_secret() {
  local name="$1" value="$2"
  if [ -z "$value" ]; then
    die "$name JWT_SECRET_KEY 未配置"
  fi
  if [ "${#value}" -lt "$JWT_SECRET_MIN_LENGTH" ]; then
    die "$name JWT_SECRET_KEY 长度不足（至少 ${JWT_SECRET_MIN_LENGTH} 个字符）"
  fi
}

resolve_shared_jwt_secret() {
  local tax_secret="$1" rag_secret="$2" effective_secret

  # An explicitly supplied process value is the trust-domain override.  The
  # value is validated without ever echoing it, and is restored after both
  # .env files have been sourced so neither file can replace it.
  if [ "$_JWT_SECRET_WAS_SET" = x ]; then
    effective_secret="$_JWT_SECRET_OVERRIDE"
    validate_jwt_secret "外部 JWT_SECRET_KEY" "$effective_secret"
  else
    validate_jwt_secret "Tax" "$tax_secret"
    validate_jwt_secret "RAG" "$rag_secret"
    if [ "$tax_secret" != "$rag_secret" ]; then
      die "Tax 与 RAG 的 JWT_SECRET_KEY 不一致，拒绝启动"
    fi
    effective_secret="$tax_secret"
  fi

  # Child processes inherit this one explicit trust-domain value.
  export JWT_SECRET_KEY="$effective_secret"
}

resolve_shared_service_key() {
  local configured_key="${RAG_SHARED_API_KEY:-}"
  local key_file="${RAG_SHARED_API_KEY_FILE:-}"
  local file_key
  if [ "$_SHARED_KEY_FILE_WAS_SET" = x ]; then
    export RAG_SHARED_API_KEY_FILE="$_SHARED_KEY_FILE_OVERRIDE"
    key_file="$_SHARED_KEY_FILE_OVERRIDE"
  fi
  [ -n "$key_file" ] || return 0
  case "$key_file" in
    /*) : ;;
    *) key_file="$RAG_DIR/$key_file" ;;
  esac
  [ -f "$key_file" ] && [ -r "$key_file" ] || \
    die "RAG_SHARED_API_KEY_FILE 已配置但文件不可读，拒绝启动"
  [ -s "$key_file" ] || die "RAG_SHARED_API_KEY_FILE 为空，拒绝启动"
  file_key="$(tr -d '\r' < "$key_file")"
  [ -n "$file_key" ] || die "RAG_SHARED_API_KEY_FILE 为空，拒绝启动"
  if [ -n "$configured_key" ] && [ "$configured_key" != "$file_key" ]; then
    die "RAG_SHARED_API_KEY 与 RAG_SHARED_API_KEY_FILE 不一致，拒绝启动"
  fi
  # Pass the resolved value to both child services.  It is never printed.
  export RAG_SHARED_API_KEY="$file_key"
}

require_postgresql_url() {
  local name="$1" value="$2"
  case "$value" in
    postgresql://*|postgresql+*) ;;
    *) die "$name 未配置 PostgreSQL 连接（只支持 PostgreSQL）" ;;
  esac
}

start_postgres_and_wait() {
  if [ "$MANAGE_POSTGRES" = true ]; then
    [ -x "$PROJECT_DIR/start_postgres.sh" ] || die "缺少可执行的 start_postgres.sh"
    "$PROJECT_DIR/start_postgres.sh"
  fi
  command -v pg_isready >/dev/null 2>&1 || die "未找到 pg_isready，无法确认 PostgreSQL 状态"
  local i
  for i in $(seq 1 30); do
    if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
      log "PostgreSQL 已就绪"
      return 0
    fi
    sleep 1
  done
  die "PostgreSQL 在等待窗口内没有就绪"
}

prepare_tax_runtime() {
  require_postgresql_url "Tax DATABASE_URL" "${DATABASE_URL:-}"
  command -v uv >/dev/null 2>&1 || die "未找到 uv，无法准备 Tax 运行环境"
  (cd "$TAX_DIR" && uv sync)
}

prepare_rag_runtime() {
  local embedding_model reranker_model
  require_postgresql_url "RAG PROJECT_RAG_DB_URL" "${PROJECT_RAG_DB_URL:-}"

  if [ "$_RAG_EMBEDDING_WAS_SET" = x ]; then
    embedding_model="$_RAG_EMBEDDING_OVERRIDE"
  elif [ -n "${PROJECT_RAG_EMBEDDING_MODEL:-}" ] && [ -f "${PROJECT_RAG_EMBEDDING_MODEL}/config.json" ]; then
    embedding_model="$PROJECT_RAG_EMBEDDING_MODEL"
  else
    embedding_model="$PROJECT_DIR/models/bge-m3"
  fi
  [ -f "$embedding_model/config.json" ] || die "Embedding 模型路径不可用：$embedding_model"
  export PROJECT_RAG_EMBEDDING_MODEL="$embedding_model"

  if reranker_is_enabled; then
    if [ "$_RAG_RERANKER_WAS_SET" = x ]; then
      reranker_model="$_RAG_RERANKER_OVERRIDE"
    elif [ -n "${PROJECT_RAG_RERANKER_MODEL:-}" ] && [ -f "${PROJECT_RAG_RERANKER_MODEL}/config.json" ]; then
      reranker_model="$PROJECT_RAG_RERANKER_MODEL"
    else
      reranker_model="$PROJECT_DIR/models/bge-reranker-v2-m3"
    fi
    [ -f "$reranker_model/config.json" ] || die "Reranker 模型路径不可用：$reranker_model"
    export PROJECT_RAG_RERANKER_MODEL="$reranker_model"
  else
    unset PROJECT_RAG_RERANKER_MODEL
    log "Reranker 已关闭；跳过模型目录校验与加载。"
  fi

  command -v uv >/dev/null 2>&1 || die "未找到 uv，无法准备 RAG 运行环境"
  (cd "$RAG_DIR" && uv sync --inexact)
}

reranker_is_enabled() {
  case "${PROJECT_RAG_RERANKER_ENABLED:-0}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

local_llm_is_enabled() {
  case "${LOCAL_LLM_ENABLED:-1}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_local_llm_model() {
  case "$LOCAL_LLM_MODEL" in
    /*) printf '%s' "$LOCAL_LLM_MODEL" ;;
    *) printf '%s/%s' "$PROJECT_DIR" "$LOCAL_LLM_MODEL" ;;
  esac
}

resolve_local_llm_server_bin() {
  case "$LOCAL_LLM_SERVER_BIN" in
    /*) printf '%s' "$LOCAL_LLM_SERVER_BIN" ;;
    */*) printf '%s/%s' "$PROJECT_DIR" "$LOCAL_LLM_SERVER_BIN" ;;
    *) command -v "$LOCAL_LLM_SERVER_BIN" 2>/dev/null || return 1 ;;
  esac
}

prepare_local_llm_runtime() {
  local model server_bin
  local_llm_is_enabled || return 0
  if ! server_bin="$(resolve_local_llm_server_bin)" || [ ! -x "$server_bin" ]; then
    warn "LOCAL_LLM_ENABLED=1 但未找到可执行的 llama.cpp server：$LOCAL_LLM_SERVER_BIN（IDP/Tax/RAG 将继续启动，AI 保底状态为 DEGRADED）"
    return 1
  fi
  model="$(resolve_local_llm_model)"
  if [ ! -f "$model" ]; then
    warn "本地 LLM 模型文件不存在：$model（请先运行 models/local-llm/download_ling3_tiny.sh 或 download_qwen35_2b.sh；IDP/Tax/RAG 将继续启动）"
    return 1
  fi
  case "$LOCAL_LLM_PORT" in ''|*[!0-9]*) warn "LOCAL_LLM_PORT 必须是数字：$LOCAL_LLM_PORT"; return 1 ;; esac
  case "$LOCAL_LLM_CTX_SIZE" in ''|*[!0-9]*) warn "LOCAL_LLM_CTX_SIZE 必须是正整数：$LOCAL_LLM_CTX_SIZE"; return 1 ;; esac
  case "$LOCAL_LLM_THREADS" in ''|*[!0-9]*) warn "LOCAL_LLM_THREADS 必须是正整数：$LOCAL_LLM_THREADS"; return 1 ;; esac
  case "$LOCAL_LLM_THREADS_BATCH" in ''|*[!0-9]*) warn "LOCAL_LLM_THREADS_BATCH 必须是正整数：$LOCAL_LLM_THREADS_BATCH"; return 1 ;; esac
  case "$LOCAL_LLM_BATCH_SIZE" in ''|*[!0-9]*) warn "LOCAL_LLM_BATCH_SIZE 必须是正整数：$LOCAL_LLM_BATCH_SIZE"; return 1 ;; esac
  case "$LOCAL_LLM_UBATCH_SIZE" in ''|*[!0-9]*) warn "LOCAL_LLM_UBATCH_SIZE 必须是正整数：$LOCAL_LLM_UBATCH_SIZE"; return 1 ;; esac
  LOCAL_LLM_SERVER_BIN="$server_bin"
  log "本地 LLM 已配置：$(basename "$model")（${LOCAL_LLM_HOST}:${LOCAL_LLM_PORT}，alias=${LOCAL_LLM_ALIAS}，server=${server_bin}）"
}

expected_local_llm_process() {
  local pid="$1" port="$2" command
  command="$(pid_command "$pid")"
  case "$command" in
    *llama-server*"--port $port"*|*llama-server*"--port"$'\t'"$port"*) return 0 ;;
    *) return 1 ;;
  esac
}

prepare_local_llm_slot() {
  local pid
  [ -f "$LOCAL_LLM_PID_FILE" ] || :
  if [ -f "$LOCAL_LLM_PID_FILE" ]; then
    pid="$(tr -d '[:space:]' < "$LOCAL_LLM_PID_FILE")"
    if pid_alive "$pid"; then
      if expected_local_llm_process "$pid" "$LOCAL_LLM_PORT"; then
        log "本地 LLM 已在运行（PID ${pid}，端口 ${LOCAL_LLM_PORT}），复用现有进程"
        return 2
      fi
      warn "本地 LLM PID 文件指向了非 llama-server 进程（PID ${pid}），跳过本地 LLM"
      return 1
    fi
    rm -f "$LOCAL_LLM_PID_FILE"
  fi
  if port_listening "$LOCAL_LLM_PORT"; then
    warn "本地 LLM 端口 ${LOCAL_LLM_PORT} 已被占用，跳过本地 LLM（IDP/Tax/RAG 将继续启动）"
    return 1
  fi
}

start_local_llm() {
  local model pid slot_status
  local_llm_is_enabled || { log "LOCAL_LLM_ENABLED=0，跳过本地 llama.cpp"; return 0; }
  prepare_local_llm_runtime || return 1
  if prepare_local_llm_slot; then
    slot_status=0
  else
    slot_status=$?
  fi
  case "$slot_status" in
    1) return 1 ;;
    2) LOCAL_LLM_ACTIVE=true; return 0 ;;
  esac
  model="$(resolve_local_llm_model)"
  log "启动本地 llama.cpp（端口 ${LOCAL_LLM_PORT}，模型 $(basename "$model")）..."
  (
    exec nohup "$LOCAL_LLM_SERVER_BIN" \
      --model "$model" \
      --host "$LOCAL_LLM_HOST" \
      --port "$LOCAL_LLM_PORT" \
      --alias "$LOCAL_LLM_ALIAS" \
      --ctx-size "$LOCAL_LLM_CTX_SIZE" \
      --threads "$LOCAL_LLM_THREADS" \
      --threads-batch "$LOCAL_LLM_THREADS_BATCH" \
      --batch-size "$LOCAL_LLM_BATCH_SIZE" \
      --ubatch-size "$LOCAL_LLM_UBATCH_SIZE" \
      --gpu-layers "$LOCAL_LLM_GPU_LAYERS" \
      --reasoning "$LOCAL_LLM_REASONING" \
      --parallel 1 \
      --jinja
  ) >"$LOCAL_LLM_LOG_FILE" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" > "$LOCAL_LLM_PID_FILE"
  STARTED_LOCAL_LLM_PID="$pid"
  LOCAL_LLM_ACTIVE=true
  log "本地 LLM 已启动（PID ${pid}）；日志：$LOCAL_LLM_LOG_FILE"
}

resolve_idp_python() {
  if [ -x "$IDP_DIR/.venv/bin/python" ]; then
    printf '%s' "$IDP_DIR/.venv/bin/python"
  elif [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    printf '%s' "$PROJECT_DIR/.venv/bin/python"
  else
    return 1
  fi
}

run_migrations_if_requested() {
  [ "$RUN_MIGRATIONS" = true ] || return 0
  log "执行 Tax Alembic 迁移（第一阶段）..."
  (cd "$TAX_DIR" && uv run alembic -c alembic.ini upgrade head)
  log "执行 RAG Alembic 迁移（第二阶段）..."
  (cd "$RAG_DIR" && "$RAG_DIR/.venv/bin/python" -m alembic -c alembic.ini upgrade head)
  if idp_is_enabled; then
    local idp_py
    idp_py="$(resolve_idp_python)" || die "IDP Python 运行时不存在，无法初始化 IDP schema（请先运行 ./start_all.sh install）"
    log "执行 IDP schema 初始化（第三阶段）..."
    "$idp_py" - "$IDP_DIR/database/schema_v3.sql" <<'PY'
import os
import sys
from pathlib import Path

import psycopg

database_url = os.environ.get("DATABASE_URL", "").strip()
if database_url.startswith("postgresql+psycopg://"):
    database_url = "postgresql://" + database_url.removeprefix("postgresql+psycopg://")
if not database_url:
    raise SystemExit("DATABASE_URL is required for IDP schema initialization")
schema_path = Path(sys.argv[1])
with psycopg.connect(database_url) as connection:
    with connection.cursor() as cursor:
        cursor.execute(schema_path.read_text(encoding="utf-8"))
    connection.commit()
PY
  fi
  log "Tax → RAG → IDP 迁移顺序完成"
}

start_tax() {
  local port="${1:-}" pid
  [ -n "$port" ] || die "Tax 端口未解析，拒绝启动"
  [ -x "$TAX_DIR/.venv/bin/python" ] || die "Tax Python 运行时不存在：$TAX_DIR/.venv/bin/python"
  log "启动 Tax（端口 ${port}）..."
  (
    cd "$TAX_DIR"
    exec nohup "$TAX_DIR/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "$port"
  ) >"$TAX_LOG_FILE" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" > "$TAX_PID_FILE"
  STARTED_TAX_PID="$pid"
  log "Tax 已启动（PID ${pid}）；日志：$TAX_LOG_FILE"
}

start_rag() {
  local port="${1:-}" pid
  [ -n "$port" ] || die "RAG 端口未解析，拒绝启动"
  log "启动 RAG（端口 ${port}）..."
  (
    cd "$RAG_DIR"
    exec nohup "$RAG_DIR/.venv/bin/python" -m uvicorn app.main:app \
      --host "${PROJECT_RAG_HOST:-127.0.0.1}" --port "$port"
  ) >"$RAG_LOG_FILE" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" > "$RAG_PID_FILE"
  STARTED_RAG_PID="$pid"
  log "RAG 已启动（PID ${pid}）；日志：$RAG_LOG_FILE"
}

idp_is_enabled() {
  case "${IDP_ENABLED:-1}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

start_idp() {
  local port="${1:-}" pid idp_py
  [ -n "$port" ] || die "IDP 端口未解析，拒绝启动"
  idp_py="$(resolve_idp_python)" || die "IDP Python 运行时不存在（请先运行 ./start_all.sh install）"
  log "启动 IDP（端口 ${port}）..."
  (
    cd "$IDP_DIR"
    exec nohup "$idp_py" -m uvicorn app.main:app --host "$IDP_HOST" --port "$port"
  ) >"$IDP_LOG_FILE" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" > "$IDP_PID_FILE"
  STARTED_IDP_PID="$pid"
  log "IDP 已启动（PID ${pid}）；日志：$IDP_LOG_FILE"
}

stop_started_process() {
  local pid="${1:-}"
  if pid_alive "$pid"; then
    kill -TERM "$pid" >/dev/null 2>&1 || :
  fi
}

remove_owned_pid_file() {
  local pid_file="$1" pid="$2" recorded_pid
  [ -n "$pid" ] || return 0
  [ -f "$pid_file" ] || return 0
  recorded_pid="$(tr -d '[:space:]' < "$pid_file")"
  if [ "$recorded_pid" = "$pid" ]; then
    rm -f "$pid_file"
  fi
}

cleanup_on_failure() {
  local status="$?"
  if [ "$status" -ne 0 ]; then
    warn "启动失败，回收本次启动的服务进程"
    if [ -n "$STARTED_RAG_PID" ]; then
      stop_started_process "$STARTED_RAG_PID"
      remove_owned_pid_file "$RAG_PID_FILE" "$STARTED_RAG_PID"
    fi
    if [ -n "$STARTED_IDP_PID" ]; then
      stop_started_process "$STARTED_IDP_PID"
      remove_owned_pid_file "$IDP_PID_FILE" "$STARTED_IDP_PID"
    fi
    if [ -n "$STARTED_TAX_PID" ]; then
      stop_started_process "$STARTED_TAX_PID"
      remove_owned_pid_file "$TAX_PID_FILE" "$STARTED_TAX_PID"
    fi
    if [ -n "$STARTED_LOCAL_LLM_PID" ]; then
      stop_started_process "$STARTED_LOCAL_LLM_PID"
      remove_owned_pid_file "$LOCAL_LLM_PID_FILE" "$STARTED_LOCAL_LLM_PID"
    fi
  fi
  trap - EXIT
  exit "$status"
}
trap cleanup_on_failure EXIT

wait_for_local_llm() {
  [ "$LOCAL_LLM_ACTIVE" = true ] || return 0
  local deadline=$((SECONDS + LOCAL_LLM_STARTUP_TIMEOUT_SECONDS))
  local pid
  command -v curl >/dev/null 2>&1 || { warn "未找到 curl，本地 LLM 健康检查跳过（DEGRADED）"; return 1; }
  log "等待本地 llama.cpp 加载模型（最长 ${LOCAL_LLM_STARTUP_TIMEOUT_SECONDS}s）..."
  while [ "$SECONDS" -lt "$deadline" ]; do
    pid="$(tr -d '[:space:]' < "$LOCAL_LLM_PID_FILE" 2>/dev/null || :)"
    if ! pid_alive "$pid"; then
      warn "本地 llama-server 已退出；最近日志："
      tail -n 40 "$LOCAL_LLM_LOG_FILE" >&2 || :
      return 1
    fi
    if curl --silent --show-error --fail --connect-timeout 1 --max-time 3 \
      "http://${LOCAL_LLM_HOST}:${LOCAL_LLM_PORT}/health" >/dev/null 2>&1; then
      log "本地 llama.cpp 健康检查通过"
      return 0
    fi
    sleep 1
  done
  warn "本地 llama.cpp 健康检查超时；最近日志："
  tail -n 60 "$LOCAL_LLM_LOG_FILE" >&2 || :
  return 1
}

configure_rag_local_llm_fallback() {
  # The RAG model pool treats this as a non-persistent last-resort endpoint.
  # Publish it only after the launcher has proved that its managed process is
  # healthy; a stale .env value must not make RAG claim a stopped local model.
  if [ "$LOCAL_LLM_ACTIVE" != true ]; then
    unset RAG_LLM_LOCAL_BASE_URL RAG_LLM_LOCAL_MODEL RAG_LLM_LOCAL_TIMEOUT_SECONDS
    return 0
  fi
  if [ -z "${LOCAL_LLM_ALIAS:-}" ]; then
    unset RAG_LLM_LOCAL_BASE_URL RAG_LLM_LOCAL_MODEL RAG_LLM_LOCAL_TIMEOUT_SECONDS
    warn "本地 llama.cpp 未提供模型 alias；RAG 不注册本地保底端点"
    return 0
  fi

  local host_url
  case "$LOCAL_LLM_HOST" in
    *:*) host_url="http://[${LOCAL_LLM_HOST}]:${LOCAL_LLM_PORT}" ;;
    *) host_url="http://${LOCAL_LLM_HOST}:${LOCAL_LLM_PORT}" ;;
  esac
  export RAG_LLM_LOCAL_BASE_URL="${host_url}/v1"
  export RAG_LLM_LOCAL_MODEL="$LOCAL_LLM_ALIAS"
  export RAG_LLM_LOCAL_TIMEOUT_SECONDS="60"
  log "RAG 本地保底端点已注入（${host_url}/v1，model=${LOCAL_LLM_ALIAS}，仅进程内生效）"
}

wait_for_health() {
  local name="$1" pid_file="$2" port="$3" url="$4"
  local deadline=$((SECONDS + STARTUP_TIMEOUT_SECONDS))
  local body pid log_file
  command -v curl >/dev/null 2>&1 || die "未找到 curl，无法执行健康检查"
  case "$name" in
    Tax) log_file="$TAX_LOG_FILE" ;;
    RAG) log_file="$RAG_LOG_FILE" ;;
    IDP) log_file="$IDP_LOG_FILE" ;;
    *) die "未知健康检查服务：$name" ;;
  esac
  while [ "$SECONDS" -lt "$deadline" ]; do
    pid="$(tr -d '[:space:]' < "$pid_file" 2>/dev/null || :)"
    if ! pid_alive "$pid"; then
      warn "$name 进程已退出；最近日志："
      tail -n 40 "$log_file" >&2 || :
      die "$name 未能启动"
    fi
    if body="$(curl --silent --show-error --fail --connect-timeout 1 --max-time 3 "$url" 2>/dev/null)"; then
      if health_payload_acceptable "$name" "$body"; then
        log "$name 健康检查通过"
        return 0
      fi
    fi
    sleep 1
  done
  warn "$name 健康检查超时；最近日志："
  tail -n 60 "$log_file" >&2 || :
  die "$name 未在 ${STARTUP_TIMEOUT_SECONDS}s 内达到健康状态"
}

health_payload_acceptable() {
  local name="$1" body="$2"
  # A fresh, correctly migrated database has no project rows yet, so RAG may
  # legitimately report a degraded Facts component. Readiness still requires
  # PostgreSQL, local models, and the worker to be healthy.
  if [ "$name" = "RAG" ]; then
    printf '%s' "$body" | "$RAG_DIR/.venv/bin/python" -c '
import json, sys
data = json.load(sys.stdin)
components = data.get("components", {})
reranker = components.get("reranker", {})
reranker_ok = not reranker.get("enabled", False) or reranker.get("status") in ("ok", "degraded")
healthy = (
    data.get("status") != "down"
    and components.get("db", {}).get("status") == "ok"
    and components.get("embedding", {}).get("status") == "ok"
    and reranker_ok
    and components.get("worker", {}).get("status") != "down"
)
raise SystemExit(0 if healthy else 1)
'
    return $?
  fi
  if [ "$name" = "IDP" ]; then
    printf '%s' "$body" | python3 -c '
import json, sys
data = json.load(sys.stdin)
healthy = data.get("status") == "ok" and data.get("database_configured") is True and data.get("database_ok") is True
raise SystemExit(0 if healthy else 1)
'
    return $?
  fi
  printf '%s' "$body" | python3 -c '
import json, sys
data = json.load(sys.stdin)
components = data.get("components", {})
healthy = (
    data.get("status") != "down"
    and components.get("db", {}).get("status") == "ok"
)
raise SystemExit(0 if healthy else 1)
'
}

log "=== 成都建工 V3.0 启动 ==="
# Keep the caller's JWT override separate from dotenv loading.  When no
# caller value exists, clear the value between files so a missing key in one
# service cannot be accidentally inherited from the other service's .env.
if [ "$_JWT_SECRET_WAS_SET" != x ]; then
  unset JWT_SECRET_KEY
fi
load_env_file "$TAX_DIR/.env"
_TAX_JWT_SECRET_CONFIGURED="${JWT_SECRET_KEY-}"
if [ "$_JWT_SECRET_WAS_SET" = x ]; then
  export JWT_SECRET_KEY="$_JWT_SECRET_OVERRIDE"
else
  unset JWT_SECRET_KEY
fi
if [ "$_TAX_PORT_WAS_SET" = x ]; then TAX_PORT="$_TAX_PORT_OVERRIDE"; else TAX_PORT="${TAX_PORT:-8921}"; fi
load_env_file "$RAG_DIR/.env"
_RAG_JWT_SECRET_CONFIGURED="${JWT_SECRET_KEY-}"
resolve_shared_jwt_secret "$_TAX_JWT_SECRET_CONFIGURED" "$_RAG_JWT_SECRET_CONFIGURED"
if [ "$_RAG_PORT_WAS_SET" = x ]; then RAG_PORT="$_RAG_PORT_OVERRIDE"; else RAG_PORT="${PROJECT_RAG_PORT:-8922}"; fi
EFFECTIVE_TAX_PORT="$TAX_PORT"
EFFECTIVE_RAG_PORT="$RAG_PORT"
if [ "$_IDP_PORT_WAS_SET" = x ]; then IDP_PORT="$_IDP_PORT_OVERRIDE"; else IDP_PORT="${IDP_PORT:-8933}"; fi
EFFECTIVE_IDP_PORT="$IDP_PORT"
if [ "$_DATABASE_URL_WAS_SET" = x ]; then export DATABASE_URL="$_DATABASE_URL_OVERRIDE"; fi
if [ "$_RAG_DB_URL_WAS_SET" = x ]; then export PROJECT_RAG_DB_URL="$_RAG_DB_URL_OVERRIDE"; fi
if [ "$_RAG_HOST_WAS_SET" = x ]; then export PROJECT_RAG_HOST="$_RAG_HOST_OVERRIDE"; fi
if [ "$_RAG_WORKER_WAS_SET" = x ]; then export PROJECT_RAG_AUTO_START_WORKER="$_RAG_WORKER_OVERRIDE"; fi
if [ "$_TAX_RAG_URL_WAS_SET" = x ]; then export TAX_RAG_SERVICE_URL="$_TAX_RAG_URL_OVERRIDE"; fi
if [ "$_TAX_FACTS_URL_WAS_SET" = x ]; then export TAX_RAG_V1_FACTS_URL="$_TAX_FACTS_URL_OVERRIDE"; fi
if [ "$_APP_ENV_WAS_SET" = x ]; then export APP_ENV="$_APP_ENV_OVERRIDE"; fi
if [ "$_SHARED_KEY_WAS_SET" = x ]; then export RAG_SHARED_API_KEY="$_SHARED_KEY_OVERRIDE"; fi
if [ "$_IDP_ENABLED_WAS_SET" = x ]; then export IDP_ENABLED="$_IDP_ENABLED_OVERRIDE"; fi
if [ "$_IDP_HOST_WAS_SET" = x ]; then export IDP_HOST="$_IDP_HOST_OVERRIDE"; fi
if [ "$_IDP_PORT_WAS_SET" = x ]; then export IDP_PORT="$_IDP_PORT_OVERRIDE"; fi
resolve_shared_service_key

# Used by the repository's static/subprocess contract tests.  It validates
# the trust-domain contract and exits before any port, database, model, or
# service side effect.  It is intentionally opt-in and never used by normal
# startup.
if [ "${START_ALL_VALIDATE_JWT_ONLY:-0}" = "1" ]; then
  log "JWT trust-domain contract validated"
  exit 0
fi

if local_llm_is_enabled; then
  if ! start_local_llm; then
    LOCAL_LLM_ACTIVE=false
    warn "本地 llama.cpp 未启动；继续启动 IDP/Tax/RAG，AI 本地保底状态为 DEGRADED"
  elif ! wait_for_local_llm; then
    LOCAL_LLM_ACTIVE=false
    if [ -n "$STARTED_LOCAL_LLM_PID" ]; then
      stop_started_process "$STARTED_LOCAL_LLM_PID"
      remove_owned_pid_file "$LOCAL_LLM_PID_FILE" "$STARTED_LOCAL_LLM_PID"
      STARTED_LOCAL_LLM_PID=""
    fi
    warn "本地 llama.cpp 未达到健康状态；继续启动 IDP/Tax/RAG，AI 本地保底状态为 DEGRADED"
  fi
else
  log "LOCAL_LLM_ENABLED=0，跳过本地 llama.cpp"
fi
configure_rag_local_llm_fallback

prepare_service_slot "Tax" "$TAX_PID_FILE" "$EFFECTIVE_TAX_PORT"
prepare_service_slot "RAG" "$RAG_PID_FILE" "$EFFECTIVE_RAG_PORT"
if idp_is_enabled; then
  prepare_service_slot "IDP" "$IDP_PID_FILE" "$EFFECTIVE_IDP_PORT"
else
  log "IDP_ENABLED=0，跳过 IDP"
fi
start_postgres_and_wait
prepare_tax_runtime
prepare_rag_runtime
run_migrations_if_requested
start_tax "$EFFECTIVE_TAX_PORT"
start_rag "$EFFECTIVE_RAG_PORT"
if idp_is_enabled; then
  start_idp "$EFFECTIVE_IDP_PORT"
fi
wait_for_health "RAG" "$RAG_PID_FILE" "$EFFECTIVE_RAG_PORT" "http://127.0.0.1:${EFFECTIVE_RAG_PORT}/api/v1/health"
wait_for_health "Tax" "$TAX_PID_FILE" "$EFFECTIVE_TAX_PORT" "http://127.0.0.1:${EFFECTIVE_TAX_PORT}/healthz"
if idp_is_enabled; then
  wait_for_health "IDP" "$IDP_PID_FILE" "$EFFECTIVE_IDP_PORT" "http://127.0.0.1:${EFFECTIVE_IDP_PORT}/health"
fi

trap - EXIT
log "=== 全部服务已启动且健康 ==="
echo "  Tax : http://127.0.0.1:${EFFECTIVE_TAX_PORT}"
echo "  RAG : http://127.0.0.1:${EFFECTIVE_RAG_PORT}"
if idp_is_enabled; then
  echo "  IDP : http://${IDP_HOST}:${EFFECTIVE_IDP_PORT}"
else
  echo "  IDP : disabled"
fi
if [ "$LOCAL_LLM_ACTIVE" = true ]; then
  echo "  Local LLM : http://${LOCAL_LLM_HOST}:${LOCAL_LLM_PORT}/v1 (model=${LOCAL_LLM_ALIAS})"
else
  echo "  Local LLM : DEGRADED/disabled"
fi
echo "  PostgreSQL : 127.0.0.1:5432"
echo "停止命令：$PROJECT_DIR/stop_all.sh"
