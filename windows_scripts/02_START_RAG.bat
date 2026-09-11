@echo off
@chcp 936 >nul 2>&1
title æˆéƒ½å»ºå·¥ V3.1 - RAGçŸ¥è¯†ä¸?° (Port 8922)
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "ROOT_DIR=%CD%"

echo ==============================================================================
echo   æˆéƒ½å»ºå·¥ V3.1 - RAG æ–‡æ¡£ä¸ŽçŸ¥è¯†ä¸­å?(Port 8922)
echo   PostgreSQL: 127.0.0.1:54320 ^| Local LLM: 127.0.0.1:8930/v1
echo ==============================================================================

call "%SCRIPT_DIR%00_START_POSTGRES.bat"
if errorlevel 1 (
    echo [é”™è?] PostgreSQL 54320 æœ?°±ç»?¼ŒRAG ç¦æ?å?Š¨ã€?
    exit /b 11
)

call :WAIT_PORT 8930 30 "æœ?œ°å¤§æ¨¡åž?
if errorlevel 1 (
    echo [é”™è?] æœ?œ°å¤§æ¨¡åž?8930 æœ?°±ç»??‚è?å…ˆè¿è¡?01_START_LLM.batã€?
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

echo [å¥‘çº¦] DB=54320, LLM=8930/!RAG_LLM_MODEL!
cd /d "%ROOT_DIR%\source_code\0.2_RAGÏµÍ³\project-rag-v1.1"
set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [é”™è?] æœ?‰¾åˆ?RAG è™šæ‹ŸçŽ??ï¼Œè?å…ˆè¿è¡?06_SETUP_ENV.batã€?
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
        ping 127.0.0.1 -n 2 >nul
    ) else (
        ping 127.0.0.1 -n 3 >nul
    )
)
echo [é”™è?] !_NAME! åœ¨ç­‰å¾…çª—å£å†…æœ?›‘å?!_PORT!ã€?
exit /b 1
