@echo off
@chcp 65001 >nul
title 成都建工 V2.0 - 停止全部后台服务
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   正在停止成都建工 V2.0 全部运行服务...
echo ==============================================================================

echo [1/4] 正在关闭 llama-server (Port 8930)...
taskkill /F /IM llama-server.exe >nul 2>nul

echo [2/4] 正在关闭 Python Uvicorn 进程 (Port 8921, 8922, 8933)...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8921 :8922 :8933"') do (
    taskkill /F /PID %%a >nul 2>nul
)

echo [3/4] 正在关闭 Node/Vite 前端进程 (Port 5173)...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5173"') do (
    taskkill /F /PID %%a >nul 2>nul
)

echo.
echo [完成] 所有后台进程已安全终止！
echo.
pause
