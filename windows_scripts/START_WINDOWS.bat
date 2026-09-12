@echo off
@chcp 65001 >nul 2>&1
set "SCRIPT_DIR=%~dp0"
call "%SCRIPT_DIR%00_START_ALL.bat"
exit /b %ERRORLEVEL%
