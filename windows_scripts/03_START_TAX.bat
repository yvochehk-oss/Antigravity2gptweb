@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.0 - 税务系统后台 (Port 8921)
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V3.0 - 税务中台 (Port 8921)
echo   业务逻辑: 计税确认合约 - 预缴发票台账 - 穿透穿透合规
echo   健康状态: http://127.0.0.1:8921/healthz
echo ==============================================================================

:: 智能探测并适配 PostgreSQL 端口 (优先 54320 便携版，其次 5432)
set "ACTIVE_PG_PORT=54320"
netstat -ano | findstr /i ":54320 " | findstr /i "LISTENING" >nul && set "ACTIVE_PG_PORT=54320"
netstat -ano | findstr /i ":5432 " | findstr /i "LISTENING" >nul && (
    netstat -ano | findstr /i ":54320 " | findstr /i "LISTENING" >nul || set "ACTIVE_PG_PORT=5432"
)
set "DATABASE_URL=postgresql://postgres@127.0.0.1:!ACTIVE_PG_PORT!/projectrag"
echo [数据库] 自动对接 PostgreSQL 端口: !ACTIVE_PG_PORT!

set "TARGET_DIR=source_code\0.1_税务管理\gtp_V1.0_FULL\01_前期系统_V1.0\chengdu_construction_tax_system_v1_0"
if not exist "%TARGET_DIR%" set "TARGET_DIR=source_code\0.1_税务管理\chengdu_construction_tax_system_v1_0"
cd /d "%SCRIPT_DIR%..\%TARGET_DIR%"

set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [错误] 未找到虚拟环境，请先运行 06_SETUP_ENV.bat。
    pause
    exit /b 1
)

"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8921
pause