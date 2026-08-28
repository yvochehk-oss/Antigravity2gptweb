#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ROOT_ENV_FILE="$PROJECT_DIR/.env"
_TAX_PORT_WAS_SET="${TAX_PORT+x}"
_TAX_PORT_OVERRIDE="${TAX_PORT-}"
_RAG_PORT_WAS_SET="${PROJECT_RAG_PORT+x}"
_RAG_PORT_OVERRIDE="${PROJECT_RAG_PORT-}"
_IDP_ENABLED_WAS_SET="${IDP_ENABLED+x}"
_IDP_ENABLED_OVERRIDE="${IDP_ENABLED-}"
_IDP_HOST_WAS_SET="${IDP_HOST+x}"
_IDP_HOST_OVERRIDE="${IDP_HOST-}"
_IDP_PORT_WAS_SET="${IDP_PORT+x}"
_IDP_PORT_OVERRIDE="${IDP_PORT-}"
if [ -f "$ROOT_ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ROOT_ENV_FILE"
  set +a
fi
if [ "$_TAX_PORT_WAS_SET" = x ]; then TAX_PORT="$_TAX_PORT_OVERRIDE"; fi
if [ "$_RAG_PORT_WAS_SET" = x ]; then PROJECT_RAG_PORT="$_RAG_PORT_OVERRIDE"; fi
if [ "$_IDP_ENABLED_WAS_SET" = x ]; then IDP_ENABLED="$_IDP_ENABLED_OVERRIDE"; fi
if [ "$_IDP_HOST_WAS_SET" = x ]; then IDP_HOST="$_IDP_HOST_OVERRIDE"; fi
if [ "$_IDP_PORT_WAS_SET" = x ]; then IDP_PORT="$_IDP_PORT_OVERRIDE"; fi
TAX_PORT="${TAX_PORT:-8921}"
RAG_PORT="${PROJECT_RAG_PORT:-8922}"
IDP_ENABLED="${IDP_ENABLED:-1}"
IDP_HOST="${IDP_HOST:-127.0.0.1}"
IDP_PORT="${IDP_PORT:-8933}"

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
case "$IDP_ENABLED" in
  1|true|TRUE|yes|YES|on|ON)
    check "IDP" "http://${IDP_HOST}:${IDP_PORT}/health" || status=1
    ;;
  *)
    echo "[health] IDP disabled"
    ;;
esac
exit "$status"
