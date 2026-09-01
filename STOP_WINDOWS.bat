@echo off
@chcp 65001 >nul
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
call "%SCRIPT_DIR%windows_scripts\99_STOP_ALL.bat"
