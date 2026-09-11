@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"
cd /d "%ROOT_DIR%"

set "PYTHON_EXE=D:\python3\python.exe"
if not exist "%PYTHON_EXE%" (
    for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYTHON_FALLBACK set "PYTHON_FALLBACK=%%P"
    if defined PYTHON_FALLBACK set "PYTHON_EXE=!PYTHON_FALLBACK!"
)
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python 3.12 not found. Expected D:\python3\python.exe.
    exit /b 10
)

"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
if errorlevel 1 (
    echo [ERROR] Python 3.12 is required: %PYTHON_EXE%
    exit /b 11
)

set "PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/"
set "TAX_DIR=%ROOT_DIR%\source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0"
set "RAG_DIR=%ROOT_DIR%\source_code\0.2_RAG系统\project-rag-v1.1"
set "IDP_DIR=%ROOT_DIR%\source_code\0.4_IDP文档录入引擎_V3.0"
set "BOSS_DIST=%ROOT_DIR%\models\boss-dist"

if not exist "%TAX_DIR%\requirements.txt" (
    echo [ERROR] TAX requirements.txt not found: %TAX_DIR%\requirements.txt
    exit /b 20
)
if not exist "%TAX_DIR%\alembic.ini" (
    echo [ERROR] TAX alembic.ini not found: %TAX_DIR%\alembic.ini
    exit /b 21
)
if not exist "%RAG_DIR%\requirements.txt" (
    echo [ERROR] RAG requirements.txt not found: %RAG_DIR%\requirements.txt
    exit /b 22
)
if not exist "%RAG_DIR%\alembic.ini" (
    echo [ERROR] RAG alembic.ini not found: %RAG_DIR%\alembic.ini
    exit /b 23
)
if not exist "%IDP_DIR%\requirements-v3.txt" (
    echo [ERROR] IDP requirements-v3.txt not found: %IDP_DIR%\requirements-v3.txt
    exit /b 24
)

echo [1/8] TAX venv
if not exist "%TAX_DIR%\.venv\Scripts\python.exe" (
    "%PYTHON_EXE%" -m venv "%TAX_DIR%\.venv"
    if errorlevel 1 exit /b 30
)

echo [2/8] TAX dependencies
"%TAX_DIR%\.venv\Scripts\python.exe" -m pip install -i "%PIP_INDEX%" -r "%TAX_DIR%\requirements.txt"
if errorlevel 1 exit /b 31

echo [3/8] TAX migrations
pushd "%TAX_DIR%"
set "DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:54320/projectrag"
"%TAX_DIR%\.venv\Scripts\python.exe" -m alembic upgrade head
set "RC=!ERRORLEVEL!"
popd
if not "!RC!"=="0" exit /b 32

echo [4/8] RAG venv
if not exist "%RAG_DIR%\.venv\Scripts\python.exe" (
    "%PYTHON_EXE%" -m venv "%RAG_DIR%\.venv"
    if errorlevel 1 exit /b 40
)

echo [5/8] RAG dependencies
"%RAG_DIR%\.venv\Scripts\python.exe" -m pip install -i "%PIP_INDEX%" -r "%RAG_DIR%\requirements.txt"
if errorlevel 1 exit /b 41

echo [6/8] RAG migrations
pushd "%RAG_DIR%"
set "PROJECT_RAG_DB_URL=postgresql://postgres@127.0.0.1:54320/projectrag"
set "DATABASE_URL=postgresql://postgres@127.0.0.1:54320/projectrag"
"%RAG_DIR%\.venv\Scripts\python.exe" -m alembic upgrade head
set "RC=!ERRORLEVEL!"
popd
if not "!RC!"=="0" exit /b 42

echo [7/8] IDP venv and dependencies
if not exist "%IDP_DIR%\.venv\Scripts\python.exe" (
    "%PYTHON_EXE%" -m venv "%IDP_DIR%\.venv"
    if errorlevel 1 exit /b 50
)
"%IDP_DIR%\.venv\Scripts\python.exe" -m pip install -i "%PIP_INDEX%" -r "%IDP_DIR%\requirements-v3.txt"
if errorlevel 1 exit /b 51
if not exist "%IDP_DIR%\.env" if exist "%IDP_DIR%\.env.example" copy /y "%IDP_DIR%\.env.example" "%IDP_DIR%\.env" >nul

echo [8/8] Boss WEB packaged dist
if not exist "%BOSS_DIST%\index.html" (
    echo [ERROR] Boss WEB fallback missing: %BOSS_DIST%\index.html
    exit /b 60
)
if not exist "%BOSS_DIST%\assets" (
    echo [ERROR] Boss WEB fallback assets missing: %BOSS_DIST%\assets
    exit /b 61
)

echo [OK] TAX, RAG and IDP environments are ready; TAX then RAG migrations completed; boss-dist is present.
exit /b 0
