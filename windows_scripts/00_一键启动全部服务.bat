@echo off
@chcp 936 >nul 2>&1
setlocal
set "SCRIPT_DIR=%~dp0"
call "%SCRIPT_DIR%00_START_ALL.bat"
exit /b %ERRORLEVEL%
