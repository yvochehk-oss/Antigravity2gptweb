#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3 not found. Install Python 3.11+ and retry." >&2
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
MARKER="$ROOT_DIR/.venv/.idp-v3-ready"
REQ="$ROOT_DIR/requirements-v3.txt"

if [ ! -f "$MARKER" ] || [ "$REQ" -nt "$MARKER" ]; then
  "$VENV_PYTHON" -m pip install --upgrade pip
  "$VENV_PYTHON" -m pip install -r "$REQ"
  touch "$MARKER"
fi

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Review DATABASE_URL and model endpoints before production use."
fi

case "${IDP_INSTALL_OCR:-0}" in
  1|true|TRUE|yes|YES|on|ON)
    "$VENV_PYTHON" -m pip install paddleocr paddlepaddle
    ;;
esac

IDP_HOST="${IDP_HOST:-127.0.0.1}"
IDP_PORT="${IDP_PORT:-8930}"

echo "Starting Chengdu Construction IDP V3 on http://${IDP_HOST}:${IDP_PORT}"
echo "Ling is optional at runtime failure boundaries; Granite remains disabled unless GRANITE_ENABLED=1."
exec "$VENV_PYTHON" -m uvicorn app.main:app --host "$IDP_HOST" --port "$IDP_PORT"
