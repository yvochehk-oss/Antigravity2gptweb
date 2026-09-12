@echo off
@chcp 65001 >nul 2>&1
title 成都建工 V3.1 - 税务系统后台 (Port 8921)
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"
cd /d "%ROOT_DIR%"

call "%SCRIPT_DIR%00_START_POSTGRES.bat"
if errorlevel 1 (
    echo [错误] PostgreSQL 未就绪，税务后台禁止启动。
    exit /b 11
)

set "STATE_FILE=%ROOT_DIR%\runtime\state\postgres.json"
set "DB_PORT="
for /f "delims=" %%P in ('powershell.exe -NoProfile -NonInteractive -Command "$s=ConvertFrom-Json ([IO.File]::ReadAllText($env:STATE_FILE)); [Console]::Write([string]$s.Port)"') do set "DB_PORT=%%P"
if not defined DB_PORT (
    echo [错误] 无法从 postgres.json 读取协商端口。
    exit /b 12
)

call :WAIT_PORT 8922 90 "RAG知识中台"
if errorlevel 1 (
    echo [错误] RAG 8922 未就绪，请先启动控制面。
    exit /b 13
)

set "DATABASE_URL=postgresql://postgres@127.0.0.1:%DB_PORT%/projectrag"
set "TAX_RAG_SERVICE_URL=http://127.0.0.1:8922"
set "TARGET_DIR=source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0"
if not exist "%ROOT_DIR%\%TARGET_DIR%" (
    echo [错误] 未找到税务中台目录：%TARGET_DIR%
    exit /b 14
)
cd /d "%ROOT_DIR%\%TARGET_DIR%"

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [错误] 未找到税务中台虚拟环境，请先运行 06_SETUP_ENV.bat。
    exit /b 15
)

echo [契约] DB=127.0.0.1:%DB_PORT%, RAG=8922
"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8921
exit /b %ERRORLEVEL%

:WAIT_PORT
set "_PORT=%~1"
set "_TRIES=%~2"
set "_NAME=%~3"
for /l %%I in (1,1,!_TRIES!) do (
    netstat -ano | findstr /i ":!_PORT! " | findstr /i "LISTENING" >nul 2>&1 && exit /b 0
    if %%I LEQ 10 (ping 127.0.0.1 -n 2 >nul) else (ping 127.0.0.1 -n 3 >nul)
)
echo [错误] !_NAME! 在等待窗口内未监听 !_PORT!。
exit /b 1
