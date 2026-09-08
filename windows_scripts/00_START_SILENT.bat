@echo off
@chcp 65001 >nul
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

if not exist "logs" mkdir "logs"

:: 1. PostgreSQL (54320 / 5432)
netstat -ano | findstr ":54320 :5432" >nul
if errorlevel 1 (
    if exist "database\pgsql\bin\pg_ctl.exe" (
        start "00_PG" /B "database\pgsql\bin\pg_ctl.exe" start -D "F:\projectrag_pgdata" -l "%CD%\logs\postgres.log"
    )
)

:: 2. LLM (8930)
netstat -ano | findstr ":8930" >nul
if errorlevel 1 (
    set "SERVER_BIN=models\local-llm\runtime-win-cpu-x64\llama-server.exe"
    set "MODEL_FILE=models\local-llm\Ling-3.0-tiny-Q4_K_M.gguf"
    if not exist "!MODEL_FILE!" set "MODEL_FILE=models\local-llm\Qwen3.5-2B-Q4_K_M.gguf"
    if exist "!SERVER_BIN!" (
        start "01_LLM" /B "!SERVER_BIN!" --model "!MODEL_FILE!" --host 127.0.0.1 --port 8930 --alias ling-3.0-tiny --ctx-size 16384 --threads 4 --threads-batch 4 --batch-size 512 --ubatch-size 256 --gpu-layers 0 --reasoning off --parallel 1 --jinja > "logs\llm.log" 2>&1
    )
)

:: 3. RAG (8922)
netstat -ano | findstr ":8922" >nul
if errorlevel 1 (
    set "PYTHON_RAG=%CD%\source_code\0.2_RAG系统\project-rag-v1.1\.venv\Scripts\python.exe"
    start "02_RAG" /D "%CD%\source_code\0.2_RAG系统\project-rag-v1.1" /B "!PYTHON_RAG!" -m uvicorn app.main:app --host 127.0.0.1 --port 8922 > "%CD%\logs\rag.log" 2>&1
)

:: 4. IDP (8933)
netstat -ano | findstr ":8933" >nul
if errorlevel 1 (
    set "PYTHON_IDP=%CD%\source_code\0.4_IDP文档录入引擎_V3.0\.venv\Scripts\python.exe"
    start "03_IDP" /D "%CD%\source_code\0.4_IDP文档录入引擎_V3.0" /B "!PYTHON_IDP!" -m uvicorn app.main:app --host 127.0.0.1 --port 8933 > "%CD%\logs\idp.log" 2>&1
)

:: 5. TAX (8921)
netstat -ano | findstr ":8921" >nul
if errorlevel 1 (
    set "PYTHON_TAX=%CD%\source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0\.venv\Scripts\python.exe"
    start "04_TAX" /D "%CD%\source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0" /B "!PYTHON_TAX!" -m uvicorn app.main:app --host 127.0.0.1 --port 8921 > "%CD%\logs\tax.log" 2>&1
)

:: 6. WEB (5173)
netstat -ano | findstr ":5173" >nul
if errorlevel 1 (
    set "PYTHON_TAX=%CD%\source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0\.venv\Scripts\python.exe"
    start "05_WEB" /B "!PYTHON_TAX!" "%CD%\windows_scripts\serve_web.py" 5173 > "%CD%\logs\web.log" 2>&1
)

exit /b 0
