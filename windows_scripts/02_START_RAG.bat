@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.0 - RAG事实中台 (Port 8922)
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V3.0 - RAG 智能文档与事实中台 (Port 8922)
echo   向量检索: BGE-M3 (CPU加速) - 4并发 Worker
echo   本机访问: http://127.0.0.1:8922/docs
echo ==============================================================================

:: 智能探测并适配活跃 PostgreSQL 端口 (5432 vs 54320)
set "ACTIVE_PG_PORT=5432"
netstat -ano | findstr /i ":54320 " | findstr /i "LISTENING" >nul && set "ACTIVE_PG_PORT=54320"
netstat -ano | findstr /i ":5432 " | findstr /i "LISTENING" >nul && set "ACTIVE_PG_PORT=5432"
set "DATABASE_URL=postgresql://postgres@127.0.0.1:!ACTIVE_PG_PORT!/projectrag"
set "PROJECT_RAG_DB_URL=postgresql://postgres@127.0.0.1:!ACTIVE_PG_PORT!/projectrag"
echo [数据库] 自动对接 PostgreSQL 端口: !ACTIVE_PG_PORT!

cd /d "%SCRIPT_DIR%..\source_code\0.2_RAG系统\project-rag-v1.1"

set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [错误] 未找到虚拟环境，请先运行 06_SETUP_ENV.bat！
    pause
    exit /b 1
)

"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8922
pause
