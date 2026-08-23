#!/bin/bash
# ==============================================================================
# 成都建工·天府掌舵 —— 一键构建 Android APK 安装包
# ==============================================================================

set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_MODE="${1:-debug}"

if [[ "$BUILD_MODE" != "debug" && "$BUILD_MODE" != "release" ]]; then
    echo "用法: $0 [debug|release]"
    exit 2
fi

GREEN='\033[0;32m'
GOLD='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${GOLD}==============================================================================${NC}"
echo -e "${GOLD}  📱 正在构建 成都建工·天府掌舵 Android 原生安装包${NC}"
echo -e "${GOLD}==============================================================================${NC}"

# 自动配置 JDK 21 环境变量，且拒绝把 JRE 或其他主版本误当作 JDK 21。
is_jdk_21() {
    local candidate="${1:-}"
    [[ -x "$candidate/bin/java" && -x "$candidate/bin/javac" ]] || return 1
    "$candidate/bin/java" -XshowSettings:properties -version 2>&1 \
        | grep -Eq '^[[:space:]]*java\.specification\.version = 21[[:space:]]*$'
}

if ! is_jdk_21 "${JAVA_HOME:-}"; then
    DETECTED_JAVA_HOME=""
    if [ -x "/Users/yvoche/.local/share/jdk-21/Contents/Home/bin/java" ]; then
        DETECTED_JAVA_HOME="/Users/yvoche/.local/share/jdk-21/Contents/Home"
    fi
    if ! is_jdk_21 "$DETECTED_JAVA_HOME" && [ -x "/usr/libexec/java_home" ]; then
        DETECTED_JAVA_HOME="$(/usr/libexec/java_home -v 21 2>/dev/null || true)"
    fi
    if is_jdk_21 "$DETECTED_JAVA_HOME"; then
        export JAVA_HOME="$DETECTED_JAVA_HOME"
    fi
fi
if ! is_jdk_21 "${JAVA_HOME:-}"; then
    echo -e "${GOLD}⚠️ 未找到 JDK 21，请先安装并设置 JAVA_HOME。${NC}"
    exit 1
fi
export PATH="$JAVA_HOME/bin:$PATH"

# 同时识别 ANDROID_HOME、ANDROID_SDK_ROOT 与 macOS/Linux 常见目录。
is_android_sdk() {
    local candidate="${1:-}"
    [[ -f "$candidate/platforms/android-36/android.jar" && -d "$candidate/build-tools" ]]
}

for SDK_CANDIDATE in \
    "${ANDROID_HOME:-}" \
    "${ANDROID_SDK_ROOT:-}" \
    "${HOME:-}/.local/share/android-sdk" \
    "${HOME:-}/Library/Android/sdk" \
    "${HOME:-}/Android/Sdk"; do
    if is_android_sdk "$SDK_CANDIDATE"; then
        RESOLVED_ANDROID_SDK="$SDK_CANDIDATE"
        break
    fi
done

if [ -z "$RESOLVED_ANDROID_SDK" ]; then
    echo -e "${GOLD}⚠️ 未找到含 Android 36 平台和 Build Tools 的 Android SDK，请安装后设置 ANDROID_HOME。${NC}"
    exit 1
fi
export ANDROID_HOME="$RESOLVED_ANDROID_SDK"
export ANDROID_SDK_ROOT="$ANDROID_HOME"

if [[ "$BUILD_MODE" == "release" ]]; then
    RELEASE_SIGNING_NAMES=(
        CDJG_RELEASE_STORE_FILE
        CDJG_RELEASE_STORE_PASSWORD
        CDJG_RELEASE_KEY_ALIAS
        CDJG_RELEASE_KEY_PASSWORD
    )
    RELEASE_SIGNING_VALUES=(
        "${CDJG_RELEASE_STORE_FILE:-}"
        "${CDJG_RELEASE_STORE_PASSWORD:-}"
        "${CDJG_RELEASE_KEY_ALIAS:-}"
        "${CDJG_RELEASE_KEY_PASSWORD:-}"
    )
    for index in "${!RELEASE_SIGNING_NAMES[@]}"; do
        if [ -z "${RELEASE_SIGNING_VALUES[$index]}" ]; then
            echo -e "${GOLD}⚠️ Release 构建缺少环境变量: ${RELEASE_SIGNING_NAMES[$index]}${NC}"
            exit 1
        fi
    done
    if [ ! -r "$CDJG_RELEASE_STORE_FILE" ]; then
        echo -e "${GOLD}⚠️ Release keystore 不存在或不可读: $CDJG_RELEASE_STORE_FILE${NC}"
        exit 1
    fi
fi

cd "$PROJECT_DIR"
echo -e "${CYAN}[1/3] 编译前端极速生产包 (Vite Production Build)...${NC}"
npm run build

echo -e "${CYAN}[2/3] 同步 Web 资源至 Android 原生工程 (Capacitor Sync)...${NC}"
npx cap sync android

echo -e "${CYAN}[3/3] 正在通过 Gradle 编译生成 Android ${BUILD_MODE} APK...${NC}"
cd "$PROJECT_DIR/android"

mkdir -p "$PROJECT_DIR/dist_apk"

if [ ! -f "./gradlew" ]; then
    echo -e "${GOLD}⚠️ 未找到 Gradle Wrapper，请先执行 npx cap sync android。${NC}"
    exit 1
fi

chmod +x ./gradlew

if [[ "$BUILD_MODE" == "release" ]]; then
    ./gradlew assembleRelease
    SOURCE_APK="app/build/outputs/apk/release/app-release.apk"
else
    ./gradlew assembleDebug
    SOURCE_APK="app/build/outputs/apk/debug/app-debug.apk"
fi

if [ ! -f "$SOURCE_APK" ]; then
    echo -e "${GOLD}⚠️ Gradle 已完成，但未找到预期安装包: $SOURCE_APK${NC}"
    exit 1
fi

APP_VERSION="$(node -p "require('../package.json').version")"
OUTPUT_APK="$PROJECT_DIR/dist_apk/ChengduConstruction_Boss_v${APP_VERSION}-${BUILD_MODE}.apk"
cp "$SOURCE_APK" "$OUTPUT_APK"

echo ""
echo -e "${GREEN}==============================================================================${NC}"
echo -e "${GREEN}  🎉 Android APK 构建成功！${NC}"
echo -e "${GOLD}  📁 安装包路径: $OUTPUT_APK${NC}"
echo -e "${GREEN}==============================================================================${NC}"

if [[ "$BUILD_MODE" == "debug" ]]; then
    echo -e "${CYAN}💡 当前为内部测试包；正式分发请配置签名环境变量并运行 ./build_apk.sh release。${NC}"
else
    echo -e "${CYAN}💡 请在分发前核验签名证书、版本号和安装升级路径。${NC}"
fi
