@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.1 - 停止全部后台服务
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   [停止] 正在停止成都建工 V3.1 全部运行服务...
echo ==============================================================================

echo [1/4] 正在关闭 llama-server (Port 8930)...
taskkill /F /IM llama-server.exe >nul 2>nul

echo [2/4] 正在关闭 Python Uvicorn 进程 (Port 8921, 8922, 8933)...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8921 :8922 :8933"') do (
    taskkill /F /PID %%a >nul 2>nul
)

echo [3/4] 正在关闭 Node/Vite 前端进程 (Port 5173, 3000)...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5173 :3000"') do (
    taskkill /F /PID %%a >nul 2>nul
)

echo.
echo ==============================================================================
echo   [成功] [完成] 所有业务后台进程已安全终止，端口已释放！
echo   [数据库] 注：PostgreSQL (5432) 保持运行，保障数据库安全与外部复用。
echo ==============================================================================
echo.
pause
