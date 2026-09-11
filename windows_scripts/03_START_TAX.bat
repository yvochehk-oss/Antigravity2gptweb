@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.0 - 税务系统后台 (Port 8921)
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "ROOT_DIR=%CD%"

echo ==============================================================================
echo   成都建工 V3.0 - 税务中台 (Port 8921)
echo   PostgreSQL: 127.0.0.1:54320 ^| RAG: http://127.0.0.1:8922
echo ==============================================================================

call "%SCRIPT_DIR%00_START_POSTGRES.bat"
if errorlevel 1 (
    echo [错误] PostgreSQL 54320 未就绪，税务中台禁止启动。
    exit /b 11
)

call :WAIT_PORT 8922 30 "RAG知识中台"
if errorlevel 1 (
    echo [错误] RAG 8922 未就绪。请先运行 02_START_RAG.bat。
    exit /b 12
)

set "DATABASE_URL=postgresql://postgres@127.0.0.1:54320/projectrag"
set "TAX_RAG_SERVICE_URL=http://127.0.0.1:8922"
set "TARGET_DIR=source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0"
if not exist "%ROOT_DIR%\%TARGET_DIR%" (
    echo [错误] 未找到当前完整税务中台目录: %TARGET_DIR%
    exit /b 13
)
cd /d "%ROOT_DIR%\%TARGET_DIR%"

set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [错误] 未找到税务中台虚拟环境，请先运行 06_SETUP_ENV.bat。
    exit /b 14
)

"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8921
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
