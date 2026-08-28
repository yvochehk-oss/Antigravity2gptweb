#!/usr/bin/env bash
set -Eeuo pipefail
RUNTIME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$RUNTIME_DIR/start_services_impl.sh" --migrate "$@"
