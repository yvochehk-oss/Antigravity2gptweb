@echo off
@chcp 65001 >nul
title 成都建工 V3.0 - 托盘控制中心
cd /d "%~dp0"

if exist "成都建工V3.0托盘控制台.exe" (
    start "" "成都建工V3.0托盘控制台.exe"
) else if exist "desktop_apps\windows\ChengduConstructionTray.exe" (
    start "" "desktop_apps\windows\ChengduConstructionTray.exe"
) else (
    echo [提示] 正在自动编译托盘控制台...
    call "desktop_apps\windows_tray\build_tray.bat"
    start "" "成都建工V3.0托盘控制台.exe"
)
exit /b 0
