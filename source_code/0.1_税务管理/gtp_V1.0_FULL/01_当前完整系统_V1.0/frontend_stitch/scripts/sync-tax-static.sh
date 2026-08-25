#!/usr/bin/env bash
set -euo pipefail

# Build and publish only this frontend's Vite output to the directory served by
# the Tax FastAPI app. The manifest makes stale-file cleanup explicit and
# prevents this script from deleting or overwriting unrelated static assets.

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
FRONTEND_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
TAX_ROOT="$(CDPATH= cd -- "$FRONTEND_DIR/.." && pwd)"
BUILD_DIR="$FRONTEND_DIR/dist"
BACKEND_STATIC_DIR="${TAX_STATIC_DIST_DIR:-$TAX_ROOT/chengdu_construction_tax_system_v1_0/app/static_dist}"
MANIFEST_FILE="$BACKEND_STATIC_DIR/.tax-frontend-build-manifest"
MODE="${1:-sync}"

die() {
  printf 'sync-tax-static: %s\n' "$*" >&2
  exit 1
}

hash_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    die '需要 shasum 或 sha256sum 才能执行一致性校验。'
  fi
}

build_files() {
  (
    cd "$BUILD_DIR"
    find . -type f -print | sed 's#^\./##' | sort
  )
}

is_safe_build_path() {
  case "$1" in
    index.html|assets/*) return 0 ;;
    *) return 1 ;;
  esac
}

manifest_contains() {
  [[ -f "$MANIFEST_FILE" ]] && grep -F -x -q -- "$1" "$MANIFEST_FILE"
}

verify_sync() {
  [[ -f "$BUILD_DIR/index.html" ]] || die "构建目录不存在：$BUILD_DIR；先运行 npm run build。"
  [[ -f "$MANIFEST_FILE" ]] || die "同步清单不存在：$MANIFEST_FILE；先运行 npm run sync:tax-static。"

  local rel source target expected actual
  while IFS= read -r rel; do
    [[ -n "$rel" ]] || continue
    is_safe_build_path "$rel" || die "同步清单包含不受支持的路径：$rel"
    source="$BUILD_DIR/$rel"
    target="$BACKEND_STATIC_DIR/$rel"
    [[ -f "$source" ]] || die "构建文件缺失：$source"
    [[ -f "$target" ]] || die "后端静态文件缺失：$target"
    expected="$(hash_file "$source")"
    actual="$(hash_file "$target")"
    [[ "$expected" == "$actual" ]] || die "SHA-256 不一致：$rel（构建 $expected，服务目录 $actual）"
  done < "$MANIFEST_FILE"

  printf 'static verification: PASS\n'
  printf 'source: %s\n' "$BUILD_DIR"
  printf 'target: %s\n' "$BACKEND_STATIC_DIR"
}

case "$MODE" in
  --check)
    verify_sync
    exit 0
    ;;
  sync)
    ;;
  *)
    die "用法：bash scripts/sync-tax-static.sh [--check]"
    ;;
esac

(
  cd "$FRONTEND_DIR"
  npm run build
)

[[ -d "$BUILD_DIR/assets" ]] || die "Vite 构建未生成 assets 目录：$BUILD_DIR"
mkdir -p "$BACKEND_STATIC_DIR"

BUILD_FILE_LIST="$(build_files)"
[[ -n "$BUILD_FILE_LIST" ]] || die "构建目录为空：$BUILD_DIR"

MANIFEST_TMP="$(mktemp "$BACKEND_STATIC_DIR/.tax-frontend-build-manifest.XXXXXX")"
trap 'rm -f "$MANIFEST_TMP"' EXIT
printf '%s\n' "$BUILD_FILE_LIST" > "$MANIFEST_TMP"

local_rel=''
while IFS= read -r local_rel; do
  [[ -n "$local_rel" ]] || continue
  is_safe_build_path "$local_rel" || die "Vite 输出包含未允许同步的路径：$local_rel"
  target="$BACKEND_STATIC_DIR/$local_rel"
  if [[ -e "$target" && "$local_rel" != 'index.html' ]] && ! manifest_contains "$local_rel"; then
    if ! cmp -s "$BUILD_DIR/$local_rel" "$target"; then
      die "拒绝覆盖未由本脚本管理的静态资产：$target"
    fi
  fi
done < "$MANIFEST_TMP"

# Copy assets first so an existing index keeps pointing at a complete bundle if
# a later operation fails. The root index is copied last.
while IFS= read -r local_rel; do
  [[ -n "$local_rel" ]] || continue
  [[ "$local_rel" == 'index.html' ]] && continue
  target="$BACKEND_STATIC_DIR/$local_rel"
  mkdir -p "$(dirname "$target")"
  cp "$BUILD_DIR/$local_rel" "$target"
done < "$MANIFEST_TMP"

if [[ -f "$MANIFEST_FILE" ]]; then
  while IFS= read -r local_rel; do
    [[ -n "$local_rel" ]] || continue
    if ! grep -F -x -q -- "$local_rel" "$MANIFEST_TMP"; then
      is_safe_build_path "$local_rel" || die "旧同步清单包含不受支持的路径：$local_rel"
      rm -f "$BACKEND_STATIC_DIR/$local_rel"
    fi
  done < "$MANIFEST_FILE"
fi

cp "$BUILD_DIR/index.html" "$BACKEND_STATIC_DIR/index.html"
mv "$MANIFEST_TMP" "$MANIFEST_FILE"
trap - EXIT

verify_sync
