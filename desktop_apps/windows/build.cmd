@echo off
@chcp 936 >nul 2>&1
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo Windows 控制台构建失败，退出码 %EXIT_CODE%。
)
exit /b %EXIT_CODE%
