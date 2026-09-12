@echo off
@chcp 65001 >nul 2>&1
title 成都建工 V3.1 - 启动全部服务
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"
cd /d "%ROOT_DIR%"

set "CONTROLLER_EXE=%ROOT_DIR%\成都建工控制台3.1.exe"
if not exist "%CONTROLLER_EXE%" set "CONTROLLER_EXE=%ROOT_DIR%\desktop_apps\windows\publish\win-x64\成都建工控制台3.1.exe"

if not exist "%CONTROLLER_EXE%" if exist "%ROOT_DIR%\desktop_apps\windows\build.cmd" (
    echo [Build] 未找到控制台发布产物，正在执行 build.cmd...
    call "%ROOT_DIR%\desktop_apps\windows\build.cmd"
    if errorlevel 1 exit /b %ERRORLEVEL%
    set "CONTROLLER_EXE=%ROOT_DIR%\desktop_apps\windows\publish\win-x64\成都建工控制台3.1.exe"
)

if not exist "%CONTROLLER_EXE%" (
    echo [错误] 未找到 成都建工控制台3.1.exe。
    echo [提示] 请先运行 desktop_apps\windows\build.cmd。
    exit /b 10
)

echo ==============================================================================
echo   成都建工 V3.1 - Windows Runtime SSOT
echo   PostgreSQL 端口由 C# PostgreSqlPortNegotiator 动态协商
echo   LLM:8930 ^| RAG:8922 ^| IDP:8933 ^| Tax:8921 ^| Boss:5173
echo ==============================================================================

"%CONTROLLER_EXE%" --start-all
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo [错误] C# 控制面启动全部失败，退出码 %RC%。
    exit /b %RC%
)

echo [READY] 全部服务已通过 C# 控制面的启动与健康门禁。
start "" http://127.0.0.1:5173
start "" http://127.0.0.1:8921
exit /b 0
