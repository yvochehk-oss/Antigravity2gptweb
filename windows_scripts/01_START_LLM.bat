@echo off
@chcp 65001 >nul
title 成都建工 V3.0 - 本地大模型服务 (Port 8930)
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V3.0 - 本地大模型服务 (llama-server CPU 模式)
echo   默认模型: Spark-X2.5-4B Q4_K_M (~2.6GB)
echo   硬件优化: Intel/AMD x64 CPU / AVX2 优先
echo   监听端口: http://127.0.0.1:8930
echo ==============================================================================

set "ROOT_DIR=%CD%"
set "SERVER_BIN=%ROOT_DIR%\models\local-llm\runtime-win-cpu-x64\llama-server.exe"
set "MIN_LLAMA_BUILD=10828"
set "MODEL_FILE=%ROOT_DIR%\models\local-llm\Spark-X2.5-4B-Q4_K_M.gguf"
set "MODEL_ALIAS=spark-x2.5-4b"
set "REQUIRES_SPARK25=1"

if not exist "%SERVER_BIN%" (
    echo [错误] 未找到 Windows llama-server 运行时: %SERVER_BIN%
    echo [修复] 请运行: python scripts\download_models.py --runtime-only
    pause
    exit /b 1
)

if not exist "%MODEL_FILE%" (
    echo [警告] 未找到 Spark-X2.5-4B 主模型文件: %MODEL_FILE%
    echo [提示] 尝试查找 Qwen3.5-2B 兜底模型...
    set "MODEL_FILE=%ROOT_DIR%\models\local-llm\Qwen3.5-2B-Q4_K_M.gguf"
    set "MODEL_ALIAS=qwen3.5-2b"
    set "REQUIRES_SPARK25=0"
)

if not exist "%MODEL_FILE%" (
    echo [错误] 未找到任何 GGUF 模型文件！
    echo [提示] 请先运行 05_一键下载AI模型_国内魔搭.bat 自动下载模型！
    pause
    exit /b 1
)

if "%REQUIRES_SPARK25%"=="1" (
    where powershell.exe >nul 2>&1
    if errorlevel 1 (
        echo [错误] 无法执行 Spark2_5 runtime 兼容性检查：系统未找到 powershell.exe。
        exit /b 2
    )

    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p=(Resolve-Path $env:SERVER_BIN).Path; $v=^& $p --version 2^>^&1 ^| Out-String; if ($v -notmatch 'build\s+(\d+)') { Write-Host '[错误] 无法识别 llama.cpp build 版本'; exit 11 }; $b=[int]$Matches[1]; Write-Host ('[信息] llama.cpp build: ' + $b); if ($b -lt [int]$env:MIN_LLAMA_BUILD) { exit 10 }"
    if errorlevel 1 (
        echo [错误] 当前 llama.cpp runtime 不支持 Spark2_5 架构。
        echo [要求] Spark-X2.5 至少需要 llama.cpp b10828 / build 10828。
        echo [修复] 请先停止本地模型服务，然后执行:
        echo        python scripts\download_models.py --runtime-only
        exit /b 2
    )
)

echo [1/1] 正在启动 llama-server (4 线程 CPU 并行加速，16K 上下文)...
"%SERVER_BIN%" --model "%MODEL_FILE%" --host 127.0.0.1 --port 8930 --alias "%MODEL_ALIAS%" --ctx-size 16384 --threads 4 --threads-batch 4 --batch-size 512 --ubatch-size 256 --gpu-layers 0 --reasoning off --parallel 1 --jinja

if errorlevel 1 (
    echo [错误] llama-server 启动失败，退出码 !ERRORLEVEL!。
    exit /b !ERRORLEVEL!
)

pause
