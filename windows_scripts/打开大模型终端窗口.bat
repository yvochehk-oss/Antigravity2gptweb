@echo off
@chcp 936 >nul 2>&1
cd /d "%~dp0"
call windows_scripts\OPEN_LLM_CONSOLE.bat
