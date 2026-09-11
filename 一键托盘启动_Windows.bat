@echo off

@chcp 936 >nul 2>&1

title 成都建工 V3.1 - 托盘控制中心

cd /d "%~dp0"



:: 启动控制台前，先确保后台便携版数据库 (Port 54320) 已静默拉起

if exist "windows_scripts\00_START_POSTGRES.bat" (

    call "windows_scripts\00_START_POSTGRES.bat"

)



if exist "成都建工控制台.exe" (

    start "" "成都建工控制台.exe"

) else if exist "成都建工V3.1托盘控制台.exe" (

    start "" "成都建工V3.1托盘控制台.exe"

) else if exist "desktop_apps\windows\ChengduConstructionTray.exe" (

    start "" "desktop_apps\windows\ChengduConstructionTray.exe"

) else (

    echo [提示] 正在自动编译托盘控制台...

    call "desktop_apps\windows_tray\build_tray.bat"

    start "" "成都建工V3.1托盘控制台.exe"

)

exit /b 0

