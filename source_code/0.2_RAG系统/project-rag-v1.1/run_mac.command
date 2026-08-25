#!/usr/bin/env bash
# macOS double-click launcher for the direct PostgreSQL-only RAG service.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

HOST="${PROJECT_RAG_HOST:-127.0.0.1}"
PORT="${PROJECT_RAG_PORT:-8922}"
URL="http://${HOST}:${PORT}/api/v1/health"

"$SCRIPT_DIR/run.sh" &
launcher_pid="$!"
cleanup() {
  if kill -0 "$launcher_pid" >/dev/null 2>&1; then
    kill -TERM "$launcher_pid" >/dev/null 2>&1 || :
  fi
}
trap cleanup INT TERM EXIT

for _ in $(seq 1 "${STARTUP_TIMEOUT_SECONDS:-180}"); do
  if ! kill -0 "$launcher_pid" >/dev/null 2>&1; then
    wait "$launcher_pid"
    exit $?
  fi
  if command -v curl >/dev/null 2>&1; then
    if body="$(curl --silent --show-error --fail --connect-timeout 1 --max-time 3 "$URL" 2>/dev/null)" \
      && printf '%s' "$body" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
      if command -v open >/dev/null 2>&1; then
        open "http://${HOST}:${PORT}"
      fi
      break
    fi
  fi
  sleep 1
done

wait "$launcher_pid"
