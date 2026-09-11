@echo off
@chcp 936 >nul 2>&1
setlocal
set "SCRIPT_DIR=%~dp0"
call "%SCRIPT_DIR%04_START_WEB.bat"
exit /b %ERRORLEVEL%
