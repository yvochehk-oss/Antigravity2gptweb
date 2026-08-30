@echo off
@chcp 65001 >nul
title 成都建工 V3.0 - Tax 前端开发服务 (Port 5173)
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V3.0 - Tax 前端开发服务 (Port 5173)
echo   本机浏览器访问: http://127.0.0.1:5173
echo ==============================================================================

cd "source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\frontend_stitch"

call npm run dev -- --port 5173 --host 0.0.0.0
pause
