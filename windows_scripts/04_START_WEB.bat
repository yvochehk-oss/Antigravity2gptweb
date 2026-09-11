@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.0 - 老板端Web驾驶舱 (Port 5173)
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "ROOT_DIR=%CD%"

echo ==============================================================================
echo   成都建工 V3.0 - 老板端移动驾驶舱 Web (Port 5173)
echo   Backend: http://127.0.0.1:8921
echo ==============================================================================

call :WAIT_PORT 8921 30 "税务中台"
if errorlevel 1 (
    echo [错误] 税务中台 8921 未就绪，老板端 Web 禁止启动。
    exit /b 11
)

set "VITE_API_BASE_URL=http://127.0.0.1:8921"
set "PYTHON_EXE=%ROOT_DIR%\source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" "%ROOT_DIR%\windows_scripts\serve_web.py" 5173
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
