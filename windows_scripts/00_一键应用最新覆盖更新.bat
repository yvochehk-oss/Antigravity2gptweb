@echo off
@chcp 65001 >nul 2>&1
title 成都建工 V3.1 - 一键应用最新覆盖更新
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"
cd /d "%ROOT_DIR%"

echo ==============================================================================
echo   成都建工 V3.1 - 一键应用最新覆盖更新
echo   项目根目录: %ROOT_DIR%
echo ==============================================================================

echo [1/5] 停止旧业务服务...
call "%SCRIPT_DIR%STOP_WINDOWS.bat" >nul 2>&1
ping 127.0.0.1 -n 3 >nul

echo [2/5] 清理税务静态目录历史残留...
set "TAX_STATIC=%ROOT_DIR%\source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0\app\static_dist"
if exist "%TAX_STATIC%\assets" (
    del /f /q "%TAX_STATIC%\assets\CopilotView-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\assets\DashboardView-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\assets\ProjectsView-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\assets\CompaniesView-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\assets\SettingsView-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\assets\auth.store-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\assets\client-DE-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\manifest.json" >nul 2>&1
    del /f /q "%TAX_STATIC%\sw.js" >nul 2>&1
)

echo [3/5] 检查 Python 环境...
if exist "%SCRIPT_DIR%06_SETUP_ENV.bat" call "%SCRIPT_DIR%06_SETUP_ENV.bat"
if errorlevel 1 exit /b %ERRORLEVEL%

echo [4/5] 构建/确认 V3.1 控制台...
if exist "%ROOT_DIR%\desktop_apps\windows\build.cmd" call "%ROOT_DIR%\desktop_apps\windows\build.cmd" -RunSmokeTests
if errorlevel 1 exit /b %ERRORLEVEL%

echo [5/5] 通过 C# Runtime SSOT 启动全部服务，并启动托盘...
call "%SCRIPT_DIR%START_WINDOWS.bat"
if errorlevel 1 exit /b %ERRORLEVEL%
call "%SCRIPT_DIR%START_TRAY_WINDOWS.bat"
exit /b %ERRORLEVEL%
