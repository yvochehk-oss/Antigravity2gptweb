@echo off
@chcp 65001 >nul
title 成都建工 V2.0 - 税务管理系统后端 (Port 8921)
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V2.0 - 税务管理与风控中枢 (Port 8921)
echo   业务逻辑: 财税确定性计算 - 进销项发票台账 - 四流合规复核
echo   本机访问: http://127.0.0.1:8921/healthz
echo ==============================================================================

cd "source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0"

set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [错误] 未找到虚拟环境，请先运行 06_一键配置Python314环境.bat！
    pause
    exit /b 1
)

"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8921
pause
