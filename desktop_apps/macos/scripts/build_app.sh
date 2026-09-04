#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="成都建工控制台.app"
APP_DIR="$APP_ROOT/build/$APP_NAME"

swift build --configuration release --package-path "$APP_ROOT"
BIN_DIR="$(swift build --configuration release --package-path "$APP_ROOT" --show-bin-path)"

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$BIN_DIR/ChengduConstructionConsole" "$APP_DIR/Contents/MacOS/ChengduConstructionConsole"
cp "$APP_ROOT/Resources/Info.plist" "$APP_DIR/Contents/Info.plist"
chmod 755 "$APP_DIR/Contents/MacOS/ChengduConstructionConsole"

echo "已构建：$APP_DIR"
