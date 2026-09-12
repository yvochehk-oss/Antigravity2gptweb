@echo off
@chcp 65001 >nul 2>&1
title 成都建工 V3.1 - 托盘控制中心
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
    exit /b 2
)

start "" "%CONTROLLER_EXE%"
exit /b 0
