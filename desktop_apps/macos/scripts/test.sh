#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
swift test --package-path "$APP_ROOT"

if [ -d "$APP_ROOT/build/成都建工控制台.app" ]; then
  /usr/bin/plutil -lint "$APP_ROOT/build/成都建工控制台.app/Contents/Info.plist"
fi
