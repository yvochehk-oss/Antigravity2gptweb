@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.0 - RAG知识中台 (Port 8922)
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "ROOT_DIR=%CD%"

echo ==============================================================================
echo   成都建工 V3.0 - RAG 文档与知识中台 (Port 8922)
echo   PostgreSQL: 127.0.0.1:54320 ^| Local LLM: 127.0.0.1:8930/v1
echo ==============================================================================

call "%SCRIPT_DIR%00_START_POSTGRES.bat"
if errorlevel 1 (
    echo [错误] PostgreSQL 54320 未就绪，RAG 禁止启动。
    exit /b 11
)

call :WAIT_PORT 8930 30 "本地大模型"
if errorlevel 1 (
    echo [错误] 本地大模型 8930 未就绪。请先运行 01_START_LLM.bat。
    exit /b 12
)

set "DATABASE_URL=postgresql://postgres@127.0.0.1:54320/projectrag"
set "PROJECT_RAG_DB_URL=postgresql://postgres@127.0.0.1:54320/projectrag"
set "RAG_LLM_BASE_URL=http://127.0.0.1:8930/v1"
set "RAG_LLM_LOCAL_BASE_URL=http://127.0.0.1:8930/v1"
set "RAG_LLM_MODEL=spark-x2.5-4b"
set "RAG_LLM_LOCAL_MODEL=spark-x2.5-4b"
if not exist "%ROOT_DIR%\models\local-llm\Spark-X2.5-4B-Q4_K_M.gguf" if exist "%ROOT_DIR%\models\local-llm\Qwen3.5-2B-Q4_K_M.gguf" (
    set "RAG_LLM_MODEL=qwen3.5-2b"
    set "RAG_LLM_LOCAL_MODEL=qwen3.5-2b"
)

echo [契约] DB=54320, LLM=8930/!RAG_LLM_MODEL!
cd /d "%ROOT_DIR%\source_code\0.2_RAG系统\project-rag-v1.1"
set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [错误] 未找到 RAG 虚拟环境，请先运行 06_SETUP_ENV.bat。
    exit /b 13
)

"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8922
set "EXIT_CODE=%ERRORLEVEL%"
exit /b %EXIT_CODE%

:WAIT_PORT
set "_PORT=%~1"
set "_TRIES=%~2"
set "_NAME=%~3"
for /l %%I in (1,1,!_TRIES!) do (
    netstat -ano | findstr /i ":!_PORT! " | findstr /i "LISTENING" >nul 2>&1 && exit /b 0
    if %%I LEQ 10 (
        timeout /t 1 /nobreak >nul
    ) else (
        timeout /t 2 /nobreak >nul
    )
)
echo [错误] !_NAME! 在等待窗口内未监听 !_PORT!。
exit /b 1
