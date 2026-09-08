@echo off
@chcp 65001 >nul
title 成都建工 V3.0 - 本地大模型服务 (Port 8930)
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V3.0 - 本地大模型服务 (llama-server CPU 模式)
echo   默认模型: Spark-X2.5-4B Q4_K_M (~2.6GB)
echo   硬件优化: Intel i3-9100F 4核心 AVX2加速
echo   监听端口: http://127.0.0.1:8930
echo ==============================================================================

set "SERVER_BIN=models\local-llm\runtime-win-cpu-x64\llama-server.exe"
set "MODEL_FILE=models\local-llm\Spark-X2.5-4B-Q4_K_M.gguf"

if not exist "%SERVER_BIN%" (
    echo [错误] 未找到 Windows llama-server 运行时: %SERVER_BIN%
    pause
    exit /b 1
)

if not exist "%MODEL_FILE%" (
    echo [警告] 未找到 Spark-X2.5-4B 主模型文件: %MODEL_FILE%
    echo [提示] 尝试查找 Qwen3.5-2B 兜底模型...
    set "MODEL_FILE=models\local-llm\Qwen3.5-2B-Q4_K_M.gguf"
)

if not exist "%MODEL_FILE%" (
    echo [错误] 未找到任何 GGUF 模型文件！
    echo [提示] 请先运行 05_一键下载AI模型_国内魔搭.bat 自动下载模型！
    pause
    exit /b 1
)

echo [1/1] 正在启动 llama-server (4 线程 CPU 并行加速，16K 上下文)...
"%SERVER_BIN%" --model "%MODEL_FILE%" --host 127.0.0.1 --port 8930 --alias spark-x2.5-4b --ctx-size 16384 --threads 4 --threads-batch 4 --batch-size 512 --ubatch-size 256 --gpu-layers 0 --reasoning off --parallel 1 --jinja

pause
