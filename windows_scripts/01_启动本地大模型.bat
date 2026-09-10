@echo off
@chcp 936 >nul 2>&1
call "%~dp001_START_LLM.bat"
exit /b %ERRORLEVEL%
