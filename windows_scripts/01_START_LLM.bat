@echo off
@chcp 65001 >nul 2>&1
title 成都建工 V3.1 - 本地大模型 (Port 8930)
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"
cd /d "%ROOT_DIR%"

set "SERVER_BIN=%ROOT_DIR%\models\local-llm\runtime-win-cpu-x64\llama-server.exe"
set "MIN_LLAMA_BUILD=10828"
set "MODEL_FILE=%ROOT_DIR%\models\local-llm\Spark-X2.5-4B-Q4_K_M.gguf"
set "MODEL_ALIAS=spark-x2.5-4b"
set "REQUIRES_SPARK25=1"

if not exist "%SERVER_BIN%" (
    echo [错误] 未找到 Windows llama-server：%SERVER_BIN%
    echo [提示] 运行 python scripts\python\download_models.py --runtime-only
    exit /b 1
)

if not exist "%MODEL_FILE%" (
    echo [提示] 未找到 Spark-X2.5-4B，尝试 Qwen3.5-2B 回退模型。
    set "MODEL_FILE=%ROOT_DIR%\models\local-llm\Qwen3.5-2B-Q4_K_M.gguf"
    set "MODEL_ALIAS=qwen3.5-2b"
    set "REQUIRES_SPARK25=0"
)

if not exist "%MODEL_FILE%" (
    echo [错误] 未找到可用 GGUF 模型。
    exit /b 1
)

if "%REQUIRES_SPARK25%"=="1" (
    set "VERSION_FILE=%TEMP%\cdjg_llama_version_%RANDOM%_%RANDOM%.txt"
    "%SERVER_BIN%" --version >"!VERSION_FILE!" 2>&1
    set "LLAMA_BUILD="
    for /f "delims=" %%B in ('powershell.exe -NoProfile -NonInteractive -Command "$t=[IO.File]::ReadAllText($env:VERSION_FILE); $m=[regex]::Match($t,'build\s+(\d+)','IgnoreCase'); if($m.Success){[Console]::Write($m.Groups[1].Value)}"') do set "LLAMA_BUILD=%%B"
    del /f /q "!VERSION_FILE!" >nul 2>&1
    if not defined LLAMA_BUILD (
        echo [错误] 无法识别 llama.cpp build 版本。
        exit /b 2
    )
    set /a LLAMA_BUILD_NUM=!LLAMA_BUILD!+0
    if !LLAMA_BUILD_NUM! LSS %MIN_LLAMA_BUILD% (
        echo [错误] llama.cpp build !LLAMA_BUILD_NUM! 低于 Spark-X2.5 最低要求 %MIN_LLAMA_BUILD%。
        echo [提示] 运行 python scripts\python\download_models.py --runtime-only
        exit /b 2
    )
    echo [Runtime] llama.cpp build !LLAMA_BUILD_NUM! ^>= %MIN_LLAMA_BUILD%：通过。
)

echo [Start] %MODEL_ALIAS% on http://127.0.0.1:8930
"%SERVER_BIN%" --model "%MODEL_FILE%" --host 127.0.0.1 --port 8930 --alias "%MODEL_ALIAS%" --ctx-size 16384 --threads 4 --threads-batch 4 --batch-size 512 --ubatch-size 256 --gpu-layers 0 --reasoning off --parallel 1 --jinja
exit /b %ERRORLEVEL%
