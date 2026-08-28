@echo off
@chcp 65001 >nul
title 成都建工 V2.0 - Windows 环境一键初始化 (Python 3.14)
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V2.0 - Windows 运行环境初始化工具
echo   运行内核: Python 3.14 (Free-Threaded 无GIL极速多线程)
echo   适用硬件: Intel i3-9100F (4核心满速并发) + 16GB 内存 + GT 730
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

echo [1/3] 正在配置 RAG 事实中台虚拟环境 (Python 3.14 Free-Threaded)...
cd "source_code\0.2_RAG系统\project-rag-v1.1"
"%UV_BIN%" venv --python 3.14 .venv
call .venv\Scripts\activate.bat
"%UV_BIN%" pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
call deactivate
cd /d "%SCRIPT_DIR%.."

echo.
echo [2/3] 正在配置 税务管理系统虚拟环境 (Python 3.14)...
cd "source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0"
"%UV_BIN%" venv --python 3.14 .venv
call .venv\Scripts\activate.bat
"%UV_BIN%" pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
call deactivate
cd /d "%SCRIPT_DIR%.."

echo.
echo [3/3] 检查前端生产构建包...
cd "source_code\0.3_老板端安卓App_天府掌舵"
if not exist "dist\index.html" (
    where npm >nul 2>nul
    if not errorlevel 1 (
        echo 正在安装前端依赖并编译静态资源...
        call npm install --registry=https://registry.npmmirror.com
        call npm run build
    ) else (
        echo [提示] 未检测到 Node.js，如有需要请安装 Node.js！
    )
) else (
    echo [OK] 前端 dist 生产包已就绪！
)
cd /d "%SCRIPT_DIR%.."

echo.
echo ==============================================================================
echo   🎉 Python 3.14 运行环境全部初始化成功！
echo   现在您可以直接运行 【00_START_ALL.bat】 或 【00_一键启动全部服务.bat】！
echo ==============================================================================
echo.
pause
