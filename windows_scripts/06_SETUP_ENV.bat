@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.1 - Windows 环境一键初始化 (Python 3.12)
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V3.1 - Windows 运行环境初始化工具
echo   运行内核: Python 3.12 (稳定版, 全依赖兼容)
echo   适用硬件: Intel/AMD x64 CPU (AVX2加速) + 16GB+ 内存
echo ==============================================================================
echo.

set "UV_BIN=%SCRIPT_DIR%tools\uv.exe"

if not exist "%UV_BIN%" (
    where uv >nul 2>nul
    if not errorlevel 1 set "UV_BIN=uv"
)

if "%UV_BIN%"=="" (
    echo [错误] 未找到 uv.exe！
    pause
    exit /b 1
)

echo [1/3] 正在配置 RAG 事实中台虚拟环境 (Python 3.12)...
cd "source_code\0.2_RAG系统\project-rag-v1.1"
"%UV_BIN%" venv --python 3.12 .venv
call .venv\Scripts\activate.bat
"%UV_BIN%" pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
call deactivate
cd /d "%SCRIPT_DIR%.."

echo.
echo [2/3] 正在配置 税务管理系统虚拟环境 (Python 3.12)...
cd "source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0"
"%UV_BIN%" venv --python 3.12 .venv
call .venv\Scripts\activate.bat
"%UV_BIN%" pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
call deactivate
cd /d "%SCRIPT_DIR%.."

echo.
echo [3/3] 正在配置 IDP 文档录入引擎虚拟环境 (Python 3.12)...
cd "source_code\0.4_IDP文档录入引擎_V3.1"
"%UV_BIN%" venv --python 3.12 .venv
call .venv\Scripts\activate.bat
"%UV_BIN%" pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements-v3.txt
call deactivate
cd /d "%SCRIPT_DIR%.."

echo.
echo ==============================================================================
echo   Python 3.12 运行环境全部初始化成功！
echo   已为 RAG中台、税务管理、IDP文档录入引擎 配置独立虚拟环境！
echo   现在您可以直接运行 【00_START_ALL.bat】 或 【00_一键启动全部服务.bat】！
echo ==============================================================================
echo.
pause
