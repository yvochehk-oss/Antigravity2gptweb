@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.1 - AI模型高速下载
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V3.1 - AI 模型极速自动下载器
echo   支持国内阿里魔搭 ModelScope 满速节点 - 自动下载 星火 Spark-X2.5-4B 与 BGE-M3 向量模型
echo ==============================================================================
echo.

set "UV_BIN=%SCRIPT_DIR%tools\uv.exe"
set "VENV_DIR=%SCRIPT_DIR%..\source_code\0.2_RAG系统\project-rag-v1.1\.venv"
set "VENV_PY=%SCRIPT_DIR%..\source_code\0.2_RAG系统\project-rag-v1.1\.venv\Scripts\python.exe"

if exist "%UV_BIN%" (
    echo [1/2] 正在使用 uv 安装下载器依赖 modelscope 与 huggingface_hub...
    if exist "%VENV_DIR%" (
        "%UV_BIN%" pip install --python "%VENV_DIR%" -i https://mirrors.aliyun.com/pypi/simple/ modelscope huggingface_hub
    ) else (
        "%UV_BIN%" pip install -i https://mirrors.aliyun.com/pypi/simple/ modelscope huggingface_hub
    )
)

echo.
echo [2/2] 正在启动国内模型下载器...
if exist "%VENV_PY%" (
    "%VENV_PY%" scripts\download_models.py
) else (
    if exist "%UV_BIN%" (
        "%UV_BIN%" run --default-index https://mirrors.aliyun.com/pypi/simple/ --with modelscope --with huggingface_hub scripts\download_models.py
    ) else (
        python scripts\download_models.py
    )
)

echo.
echo ==============================================================================
echo   模型下载流程执行完毕！
echo ==============================================================================
echo.
pause
