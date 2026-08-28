@echo off
@chcp 65001 >nul
title 成都建工 V2.0 - 老板端Web与移动驾驶舱 (Port 5173)
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V2.0 - 老板端前端 Web (Port 5173)
echo   本机浏览器访问: http://127.0.0.1:5173
echo ==============================================================================

cd "source_code\0.3_老板端安卓App_天府掌舵"

call npm run preview -- --port 5173 --host 0.0.0.0
if errorlevel 1 (
    echo [提示] 尝试以开发模式启动前端...
    call npm run dev -- --port 5173 --host 0.0.0.0
)
pause
