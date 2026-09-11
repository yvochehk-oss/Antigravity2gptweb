@echo off
@chcp 936 >nul 2>&1
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_services_hidden.ps1"
exit /b %ERRORLEVEL%
