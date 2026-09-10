@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.0 - 税务系统后端 (Port 8921)
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V3.0 - 税务管理中台 (Port 8921)
echo   业务逻辑: 计税确定性契约 - 销项发票台账 - 合规穿透审计
echo   服务状态: http://127.0.0.1:8921/healthz
echo ==============================================================================

:: 智能探测并适配活跃 PostgreSQL 端口 (5432 vs 54320)
set "ACTIVE_PG_PORT=5432"
netstat -ano | findstr /i ":54320 " | findstr /i "LISTENING" >nul && set "ACTIVE_PG_PORT=54320"
netstat -ano | findstr /i ":5432 " | findstr /i "LISTENING" >nul && set "ACTIVE_PG_PORT=5432"
set "DATABASE_URL=postgresql://postgres@127.0.0.1:!ACTIVE_PG_PORT!/projectrag"
echo [数据库] 自动对接 PostgreSQL 端口: !ACTIVE_PG_PORT!

set "TARGET_DIR=source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0"
if not exist "%TARGET_DIR%" set "TARGET_DIR=source_code\0.1_税务管理\chengdu_construction_tax_system_v1_0"
cd /d "%SCRIPT_DIR%..\%TARGET_DIR%"

set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [错误] 未找到虚拟环境，请先运行 06_SETUP_ENV.bat！
    pause
    exit /b 1
)

"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8921
pause
