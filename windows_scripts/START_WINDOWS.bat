@echo off
@chcp 936 >nul 2>&1
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
call "%SCRIPT_DIR%windows_scripts\00_START_ALL.bat"
