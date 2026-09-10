@echo off
@chcp 936 >nul 2>&1
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0platform\windows\setup_and_start.ps1"
set EXIT_CODE=%ERRORLEVEL%
if not "%EXIT_CODE%"=="0" (
  echo.
  echo IDP startup failed with exit code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
