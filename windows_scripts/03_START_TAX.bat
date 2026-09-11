@echo off
@chcp 936 >nul 2>&1
title 鎴愰兘寤哄伐 V3.1 - 绋庡姟绯荤粺鍚庡彴 (Port 8921)
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "ROOT_DIR=%CD%"

echo ==============================================================================
echo   鎴愰兘寤哄伐 V3.1 - 绋庡姟涓彴 (Port 8921)
echo   PostgreSQL: 127.0.0.1:54320 ^| RAG: http://127.0.0.1:8922
echo ==============================================================================

call "%SCRIPT_DIR%00_START_POSTGRES.bat"
if errorlevel 1 (
    echo [閿欒] PostgreSQL 54320 鏈氨缁紝绋庡姟涓彴绂佹鍚姩銆?
    exit /b 11
)

call :WAIT_PORT 8922 30 "RAG鐭ヨ瘑涓彴"
if errorlevel 1 (
    echo [閿欒] RAG 8922 鏈氨缁€傝鍏堣繍琛?02_START_RAG.bat銆?
    exit /b 12
)

set "DATABASE_URL=postgresql://postgres@127.0.0.1:54320/projectrag"
set "TAX_RAG_SERVICE_URL=http://127.0.0.1:8922"
set "TARGET_DIR=source_code\0.1_绋庡姟绠＄悊\gtp_V1.0_FULL\01_褰撳墠瀹屾暣绯荤粺_V1.0\chengdu_construction_tax_system_v1_0"
if not exist "%ROOT_DIR%\%TARGET_DIR%" (
    echo [閿欒] 鏈壘鍒板綋鍓嶅畬鏁寸◣鍔′腑鍙扮洰褰? %TARGET_DIR%
    exit /b 13
)
cd /d "%ROOT_DIR%\%TARGET_DIR%"

set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [閿欒] 鏈壘鍒扮◣鍔′腑鍙拌櫄鎷熺幆澧冿紝璇峰厛杩愯 06_SETUP_ENV.bat銆?
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
        ping 127.0.0.1 -n 2 >nul
    ) else (
        ping 127.0.0.1 -n 3 >nul
    )
)
echo [閿欒] !_NAME! 鍦ㄧ瓑寰呯獥鍙ｅ唴鏈洃鍚?!_PORT!銆?
exit /b 1
