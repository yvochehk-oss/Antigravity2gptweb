#!/usr/bin/env bash
set -Eeuo pipefail
TAX_PORT="${TAX_PORT:-8921}"
RAG_PORT="${PROJECT_RAG_PORT:-8922}"

command -v curl >/dev/null 2>&1 || { echo "[health] 缺少 curl" >&2; exit 1; }

check() {
  local name="$1" url="$2"
  if curl -fsS --connect-timeout 3 --max-time 10 "$url" >/dev/null; then
    echo "[health] $name OK - $url"
  else
    echo "[health] $name DOWN - $url" >&2
    return 1
  fi
}

status=0
check "Tax" "http://127.0.0.1:${TAX_PORT}/healthz" || status=1
check "RAG" "http://127.0.0.1:${RAG_PORT}/healthz" || status=1
exit "$status"
