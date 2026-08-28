@echo off
@chcp 65001 >nul
title 成都建工 V2.0 - 一键启动全部服务
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V2.0 财税事实智能中台系统 [Windows 一键启动]
echo   硬件适配: Intel i3-9100F (4核心 AVX2加速) + 16GB 内存 + GT 730
echo   本地大模型: Ling-3.0-tiny (CPU 离线推理模式)
echo ==============================================================================
echo.

echo [1/4] 正在启动本地大模型服务 (Port 8930)...
start "01_本地大模型服务 (Port 8930)" cmd /k "call "%SCRIPT_DIR%01_START_LLM.bat""
timeout /t 3 /nobreak >nul

echo [2/4] 正在启动 RAG 智能文档与事实中台 (Port 8922)...
start "02_RAG事实中台 (Port 8922)" cmd /k "call "%SCRIPT_DIR%02_START_RAG.bat""
timeout /t 2 /nobreak >nul

echo [3/4] 正在启动 税务管理与风控后端 (Port 8921)...
start "03_税务管理系统 (Port 8921)" cmd /k "call "%SCRIPT_DIR%03_START_TAX.bat""
timeout /t 2 /nobreak >nul

echo [4/4] 正在启动 老板端 Web 移动驾驶舱 (Port 5173)...
start "04_老板端Web (Port 5173)" cmd /k "call "%SCRIPT_DIR%04_START_WEB.bat""
timeout /t 3 /nobreak >nul

echo.
echo ==============================================================================
echo   全部 4 个系统子服务已成功拉起！
echo   老板端驾驶舱访问入口: http://127.0.0.1:5173
echo   税务管理后台接口:     http://127.0.0.1:8921/docs
echo   RAG 事实中台接口:      http://127.0.0.1:8922/docs
echo   本地大模型状态接口:    http://127.0.0.1:8930/v1/models
echo ==============================================================================
echo.
echo 正在自动打开浏览器...
start http://127.0.0.1:5173
pause
