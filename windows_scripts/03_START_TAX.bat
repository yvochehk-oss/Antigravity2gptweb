@echo off
@chcp 936 >nul 2>&1
title æˆéƒ½å»ºå·¥ V3.1 - ç¨ŽåŠ¡ç³»ç»ŸåŽå° (Port 8921)
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "ROOT_DIR=%CD%"

echo ==============================================================================
echo   æˆéƒ½å»ºå·¥ V3.1 - ç¨ŽåŠ¡ä¸?° (Port 8921)
echo   PostgreSQL: 127.0.0.1:54320 ^| RAG: http://127.0.0.1:8922
echo ==============================================================================

call "%SCRIPT_DIR%00_START_POSTGRES.bat"
if errorlevel 1 (
    echo [é”™è?] PostgreSQL 54320 æœ?°±ç»?¼Œç¨ŽåŠ¡ä¸?°ç¦æ?å?Š¨ã€?
    exit /b 11
)

rem Keep the Tax startup dependency budget aligned with the 90-second 8921 readiness gate in 00_START_ALL.bat.
call :WAIT_PORT 8922 90 "RAGçŸ¥è¯†ä¸?°"
if errorlevel 1 (
    echo [é”™è?] RAG 8922 æœ?°±ç»??‚è?å…ˆè¿è¡?02_START_RAG.batã€?
    exit /b 12
)

set "DATABASE_URL=postgresql://postgres@127.0.0.1:54320/projectrag"
set "TAX_RAG_SERVICE_URL=http://127.0.0.1:8922"
set "TARGET_DIR=source_code\0.1_ç¨ŽåŠ¡ç®¡ç†\gtp_V1.0_FULL\01_å½“å‰å®Œæ•´ç³»ç»Ÿ_V1.0\chengdu_construction_tax_system_v1_0"
if not exist "%ROOT_DIR%\%TARGET_DIR%" (
    echo [é”™è?] æœ?‰¾åˆ°å½“å‰å®Œæ•´ç¨ŽåŠ¡ä¸­å°ç›®å½? %TARGET_DIR%
    exit /b 13
)
cd /d "%ROOT_DIR%\%TARGET_DIR%"

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [é”™è?] æœ?‰¾åˆ°ç¨ŽåŠ¡ä¸­å°è™šæ‹ŸçŽ¯å¢ƒï¼Œè¯·å…ˆè¿è? 06_SETUP_ENV.batã€?
    exit /b 14
)

"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8921
set "EXIT_CODE=%ERRORLEVEL%"
exit /b %EXIT_CODE%

:WAIT_PORT
set "_PORT=%~1"
set "_TRIES=%~2"
set "_NAME=%~3"
for /l %%I in (1,1,!_TRIES!) do (
    netstat -ano | findstr /i ":!_PORT! " | findstr /i "LISTENING" >nul 2>&1 && exit /b 0
    if %%I LEQ 10 (
        ping 127.0.0.1 -n 2 >nul
    ) else (
        ping 127.0.0.1 -n 3 >nul
    )
)
echo [é”™è?] !_NAME! åœ¨ç­‰å¾…çª—å£å†…æœ?›‘å?!_PORT!ã€?
exit /b 1
