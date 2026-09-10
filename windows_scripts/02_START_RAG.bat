@echo off
@chcp 65001 >nul
title 成都建工 V3.0 - RAG事实中台 (Port 8922)
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V3.0 - RAG 智能文档与事实中台 (Port 8922)
echo   向量检索: BGE-M3 (CPU加速) - 4并发 Worker (Python 3.14)
echo   本机访问: http://127.0.0.1:8922/docs
echo ==============================================================================

cd "source_code\0.2_RAG系统\project-rag-v1.1"

set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [错误] 未找到虚拟环境，请先运行 06_一键配置Python314环境.bat！
    pause
    exit /b 1
)

"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8922
pause
