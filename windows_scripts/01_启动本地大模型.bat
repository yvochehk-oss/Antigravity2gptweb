@echo off
@chcp 65001 >nul
call "%~dp001_START_LLM.bat"
exit /b %ERRORLEVEL%
