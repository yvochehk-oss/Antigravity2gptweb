@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "ROOT_DIR=%CD%"

echo ==============================================================================
echo   Chengdu Construction V3.1 - Windows service launcher
echo   PostgreSQL runtime state ^| LLM:8930 ^| RAG:8922 ^| IDP:8933 ^| Tax:8921 ^| Web:5173
echo ==============================================================================
echo.

rem [0/5] PostgreSQL is a hard prerequisite.
if not exist "%SCRIPT_DIR%00_START_POSTGRES.bat" (
    echo [ERROR] Missing PostgreSQL launcher: %SCRIPT_DIR%00_START_POSTGRES.bat
    exit /b 10
)
call "%SCRIPT_DIR%00_START_POSTGRES.bat"
if errorlevel 1 (
    echo [ERROR] PostgreSQL readiness gate failed. Aborting service chain.
    exit /b 11
)

rem Read the negotiated PostgreSQL port when available; default remains 54320.
set "DB_PORT=54320"
set "DB_HOST=127.0.0.1"
if exist "%ROOT_DIR%\runtime\state\postgres.json" (
    for /f "usebackq tokens=*" %%L in ("%ROOT_DIR%\runtime\state\postgres.json") do (
        echo %%L | findstr /i "\"Port\"" >nul && (
            for /f "tokens=2 delims=:," %%P in ("%%L") do (
                set "DB_PORT=%%P"
                set "DB_PORT=!DB_PORT: =!"
                set "DB_PORT=!DB_PORT:,=!"
            )
        )
    )
)

set "DATABASE_URL=postgresql://postgres@%DB_HOST%:%DB_PORT%/projectrag"
set "PROJECT_RAG_DB_URL=postgresql://postgres@%DB_HOST%:%DB_PORT%/projectrag"
set "TAX_RAG_SERVICE_URL=http://127.0.0.1:8922"
set "RAG_LLM_BASE_URL=http://127.0.0.1:8930/v1"
set "RAG_LLM_LOCAL_BASE_URL=http://127.0.0.1:8930/v1"
set "RAG_LLM_MODEL=spark-x2.5-4b"
set "RAG_LLM_LOCAL_MODEL=spark-x2.5-4b"
set "LING_BASE_URL=http://127.0.0.1:8930/v1"
set "LING_MODEL=spark-x2.5-4b"
if not exist "%ROOT_DIR%\models\local-llm\Spark-X2.5-4B-Q4_K_M.gguf" if exist "%ROOT_DIR%\models\local-llm\Qwen3.5-2B-Q4_K_M.gguf" (
    set "RAG_LLM_MODEL=qwen3.5-2b"
    set "RAG_LLM_LOCAL_MODEL=qwen3.5-2b"
    set "LING_MODEL=qwen3.5-2b"
)

echo [DB] PostgreSQL endpoint = %DB_HOST%:%DB_PORT%
echo.

echo [1/5] Starting local LLM service on port 8930...
start "01_LLM_8930" cmd.exe /d /c call "%SCRIPT_DIR%01_START_LLM.bat"
call :WAIT_PORT 8930 45 "LLM"
if errorlevel 1 exit /b 21

echo [2/5] Starting RAG service on port 8922...
start "02_RAG_8922" cmd.exe /d /c call "%SCRIPT_DIR%02_START_RAG.bat"
call :WAIT_PORT 8922 45 "RAG"
if errorlevel 1 exit /b 22

echo [3/5] Resolving and starting IDP service on port 8933...
set "IDP_PORT=8933"
set "IDP_SCRIPT="

rem Canonical physical directory is V3.0. Keep V3.1 as a compatibility fallback.
for /d %%D in ("%ROOT_DIR%\source_code\0.4_IDP*_V3.0") do (
    if not defined IDP_SCRIPT if exist "%%~fD\START_IDP_WINDOWS.bat" set "IDP_SCRIPT=%%~fD\START_IDP_WINDOWS.bat"
)
if not defined IDP_SCRIPT (
    for /d %%D in ("%ROOT_DIR%\source_code\0.4_IDP*_V3.1") do (
        if not defined IDP_SCRIPT if exist "%%~fD\START_IDP_WINDOWS.bat" set "IDP_SCRIPT=%%~fD\START_IDP_WINDOWS.bat"
    )
)
if not defined IDP_SCRIPT (
    echo [ERROR] IDP launcher not found under source_code\0.4_IDP*_V3.0 or V3.1.
    exit /b 23
)

echo [IDP] Using launcher: !IDP_SCRIPT!
start "03_IDP_8933" cmd.exe /d /c call "!IDP_SCRIPT!"
call :WAIT_PORT 8933 90 "IDP"
if errorlevel 1 exit /b 23

echo [4/5] Starting Tax service on port 8921...
start "04_TAX_8921" cmd.exe /d /c call "%SCRIPT_DIR%03_START_TAX.bat"
call :WAIT_PORT 8921 90 "Tax"
if errorlevel 1 exit /b 24

echo [5/5] Starting Boss Web on port 5173...
start "05_WEB_5173" cmd.exe /d /c call "%SCRIPT_DIR%04_START_WEB.bat"
call :WAIT_PORT 5173 30 "Boss Web"
if errorlevel 1 exit /b 25

echo.
echo ==============================================================================
echo   All V3.1 Windows service readiness gates passed.
echo   Boss Web: http://127.0.0.1:5173
echo   Tax:      http://127.0.0.1:8921
echo   RAG:      http://127.0.0.1:8922
echo   IDP:      http://127.0.0.1:8933
echo   LLM:      http://127.0.0.1:8930/v1
echo ==============================================================================
start "" http://127.0.0.1:5173
start "" http://127.0.0.1:8921
exit /b 0

:WAIT_PORT
set "_WAIT_PORT=%~1"
set "_WAIT_TRIES=%~2"
set "_WAIT_NAME=%~3"
for /l %%I in (1,1,!_WAIT_TRIES!) do (
    netstat -ano | findstr /i ":!_WAIT_PORT! " | findstr /i "LISTENING" >nul 2>&1 && (
        echo [READY] !_WAIT_NAME! is listening on port !_WAIT_PORT!.
        exit /b 0
    )
    if %%I LEQ 10 (
        ping 127.0.0.1 -n 2 >nul
    ) else (
        ping 127.0.0.1 -n 3 >nul
    )
)
echo [ERROR] !_WAIT_NAME! did not listen on port !_WAIT_PORT! before timeout.
exit /b 1
