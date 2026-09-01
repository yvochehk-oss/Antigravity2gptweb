@echo off
@chcp 65001 >nul
title 成都建工 V3.0 - 一键启动全部服务
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   🏗️ 成都建工 V3.0 财税智控与 IDP 穿透中枢 [Windows 一键启动]
echo   硬件适配: Intel/AMD CPU (AVX2加速) + 16GB+ 内存 / 支持离线推理
echo   本地大模型: Ling-3.0-tiny (CPU 离线推理模式)
echo ==============================================================================
echo.

echo [1/5] 正在启动本地大模型服务 (Port 8930)...
start "01_本地大模型服务 (Port 8930)" cmd /k "call "%SCRIPT_DIR%01_START_LLM.bat""
timeout /t 3 /nobreak >nul

echo [2/5] 正在启动 RAG 知识证据中枢 (Port 8922)...
start "02_RAG事实中台 (Port 8922)" cmd /k "call "%SCRIPT_DIR%02_START_RAG.bat""
timeout /t 2 /nobreak >nul

echo [3/5] 正在启动 IDP 穿透式录入引擎 (Port 8933)...
set "IDP_PORT=8933"
start "03_IDP文档录入引擎 (Port 8933)" cmd /k "call "%SCRIPT_DIR%..\source_code\0.4_IDP文档录入引擎_V3.0\START_IDP_WINDOWS.bat""
timeout /t 2 /nobreak >nul

echo [4/5] 正在启动 税务管理与 Native V3 Boss API (Port 8921)...
start "04_税务管理系统 (Port 8921)" cmd /k "call "%SCRIPT_DIR%03_START_TAX.bat""
timeout /t 2 /nobreak >nul

echo [5/5] 正在启动 老板端 Web 移动驾驶舱 (Port 5173)...
start "05_老板端Web (Port 5173)" cmd /k "call "%SCRIPT_DIR%04_START_WEB.bat""
timeout /t 3 /nobreak >nul

echo.
echo ==============================================================================
echo   🎉 成都建工 V3.0 全系统已成功拉起！
echo   【核心业务访问入口】
echo   📱 老板端移动驾驶舱:    http://127.0.0.1:5173 (推荐)
echo   🏢 税务管理中台 Web:    http://127.0.0.1:8921
echo   🧠 RAG 知识证据中枢:    http://127.0.0.1:8922
echo   📄 IDP 文档录入与审计:  http://127.0.0.1:8933
echo   🤖 Ling-3.0 办事员接口: http://127.0.0.1:8930/v1
echo   🗄️ PostgreSQL 数据库:   127.0.0.1:5432 (projectrag)
echo ------------------------------------------------------------------------------
echo   🔑 默认演示登录账号：admin  /  密码：密码同用户名
echo   📚 OpenAPI 接口文档：http://127.0.0.1:8921/docs
echo ==============================================================================
echo.
echo 正在自动打开浏览器...
start http://127.0.0.1:5173
start http://127.0.0.1:8921
pause
