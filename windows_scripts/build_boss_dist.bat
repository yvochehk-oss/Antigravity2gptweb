@echo off
REM ============================================================
REM 老板端前端 dist 静态打包脚本 (v3.1-windows)
REM 用途：在目标机上无需 Node.js 的前提下，一次性构建 dist 静态文件
REM 输出：source_code\0.3_老板端安卓App_天府掌舵\dist\
REM 调用：仅当 dist 目录缺失时，由桌面控制台自动调用
REM ============================================================

setlocal enabledelayedexpansion

set "BOSS_DIR=%~dp0..\source_code\0.3_老板端安卓App_天府掌舵"
set "DIST_DIR=%BOSS_DIR%\dist"

echo [BossBuild] 工作目录：%BOSS_DIR%

REM ---- 步骤 1：检查是否已经存在 dist，跳过构建 ----
if exist "%DIST_DIR%\index.html" (
    echo [BossBuild] dist 已存在，跳过构建。
    exit /b 0
)

echo [BossBuild] dist 不存在，开始构建...

REM ---- 步骤 2：检查 Node.js 是否可用 ----
where node >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Node.js。请先安装 Node.js 22.12.0 或更高版本。
    exit /b 1
)

REM ---- 步骤 3：检查 npm 是否可用 ----
where npm >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 npm。
    exit /b 1
)

REM ---- 步骤 4：检查 node_modules 是否存在，不存在则安装 ----
if not exist "%BOSS_DIR%\node_modules" (
    echo [BossBuild] node_modules 缺失，执行 npm install...
    cd /d "%BOSS_DIR%"
    call npm install --no-audit --no-fund --prefer-offline
    if errorlevel 1 (
        echo [错误] npm install 失败。
        exit /b 1
    )
)

REM ---- 步骤 5：执行 npm run build ----
echo [BossBuild] 执行 npm run build 生成 dist...
cd /d "%BOSS_DIR%"
call npm run build
if errorlevel 1 (
    echo [错误] npm run build 失败。
    exit /b 1
)

REM ---- 步骤 6：验证 dist 已生成 ----
if not exist "%DIST_DIR%\index.html" (
    echo [错误] 构建后仍未找到 dist\index.html。
    exit /b 1
)

echo [BossBuild] dist 构建成功：%DIST_DIR%
exit /b 0
