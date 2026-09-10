@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.0 - 后台静默停止
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

taskkill /F /IM llama-server.exe >nul 2>nul

for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8921 :8922 :8933 :5173"') do (
    taskkill /F /PID %%a >nul 2>nul
)

exit /b 0
