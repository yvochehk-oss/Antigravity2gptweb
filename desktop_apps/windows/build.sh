#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
python3 "$APP_DIR/tests/static_smoke.py"

if ! command -v dotnet >/dev/null 2>&1; then
  echo "静态自检通过。当前主机未安装 .NET 10 SDK；请在 Windows 上运行 build.cmd 生成 win-x64 发布包。"
  exit 0
fi

PUBLISH_DIR="$APP_DIR/publish/win-x64"
mkdir -p "$PUBLISH_DIR"
dotnet restore "$APP_DIR/ChengduConstructionController.csproj" -r win-x64 --ignore-failed-sources
dotnet publish "$APP_DIR/ChengduConstructionController.csproj" --configuration Release --runtime win-x64 --self-contained true --no-restore --output "$PUBLISH_DIR" -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -p:DebugType=None
echo "已生成 Windows 发布目录：$PUBLISH_DIR"
