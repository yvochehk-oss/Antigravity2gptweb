@echo off
@chcp 65001 >nul
title 成都建工 V3.0 - 老板端Web驾驶舱 (Port 5173)
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V3.0 - 老板端移动驾驶舱 Web (Port 5173)
echo   访问入口: http://127.0.0.1:5173
echo ==============================================================================

set "PYTHON_EXE=source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" windows_scripts\serve_web.py 5173
pause
