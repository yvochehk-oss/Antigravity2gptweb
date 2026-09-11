@echo off
@chcp 936 >nul 2>&1
setlocal
set "SCRIPT_DIR=%~dp0"
call "%SCRIPT_DIR%02_START_RAG.bat"
exit /b %ERRORLEVEL%
