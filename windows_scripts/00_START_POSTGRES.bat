@echo off
@chcp 936 >nul 2>&1
title ɶ V3.1 - PostgreSQL ػ
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "ROOT_DIR=%CD%"

rem ============================================================
rem ˿· SSOT:
rem   - ˿ڡdata dirpostmaster pid ȫ PostgreSqlPortNegotiator д
rem     runtime\state\postgres.jsonBAT ϽӲ˿ںš
rem   - ֻȡʵǷ pg_ctl
rem ============================================================

set "STATE_FILE=%ROOT_DIR%\runtime\state\postgres.json"
set "PORT=0"
set "DATA_DIR="

if exist "%STATE_FILE%" (
    for /f "usebackq tokens=*" %%L in ("%STATE_FILE%") do (
        echo %%L | findstr /i "\"Port\"" >nul && (
            for /f "tokens=2 delims=:," %%P in ("%%L") do (
                set "PORT=%%P"
                set "PORT=!PORT: =!"
                set "PORT=!PORT:,=!"
            )
        )
        echo %%L | findstr /i "\"DataDir\"" >nul && (
            for /f "tokens=2 delims=:, " %%P in ("%%L") do (
                if not defined DATA_DIR set "DATA_DIR=!DATA_DIR!%%P"
                
            )
        )
    )
)

if "%PORT%"=="0" (
    rem ûЭ̽ C# ֻ׵ȴ˳
    echo [PostgreSQL] δʵ runtime\state\postgres.jsonȴ̨ PostgreSqlPortNegotiator Э̡
    exit /b 0
)

echo [PostgreSQL] ȡʵ˿ %PORT%Ŀ¼ %DATA_DIR%

set "PG_CTL="
if exist "%ROOT_DIR%\database\pgsql\bin\pg_ctl.exe" set "PG_CTL=%ROOT_DIR%\database\pgsql\bin\pg_ctl.exe"

if not defined PG_CTL (
    echo [PostgreSQL] : δҵЯ pg_ctl.exeݿⲻܱжΪɹ
    exit /b 2
)

for %%I in ("!PG_CTL!") do set "PG_BIN=%%~dpI"
if not defined DATA_DIR set "DATA_DIR=%ROOT_DIR%\database\data"

if exist "!PG_BIN!pg_isready.exe" set "PG_ISREADY=!PG_BIN!pg_isready.exe"

if not exist "logs" mkdir "logs" >nul 2>&1

call :IS_READY
if not errorlevel 1 (
    echo [PostgreSQL] Я PostgreSQL  127.0.0.1:%PORT% 
    exit /b 0
)

if exist "!DATA_DIR!\postmaster.pid" (
    "!PG_CTL!" status -D "!DATA_DIR!" >nul 2>&1
    if not errorlevel 1 (
        echo [PostgreSQL] : Ŀ¼Ӧ PostgreSQL Դ %PORT% δ
        echo [PostgreSQL] Ϊɾʵ postmaster.pidѰȫֹ
        exit /b 4
    )
    echo [PostgreSQL] ⵽ʧЧ postmaster.pid...
    del /f /q "!DATA_DIR!\postmaster.pid" >nul 2>&1
    if exist "!DATA_DIR!\postmaster.pid" (
        echo [PostgreSQL] : ޷ʧЧ postmaster.pid
        exit /b 5
    )
)

echo [PostgreSQL] ݿʵ 127.0.0.1:%PORT%...
"!PG_CTL!" start -D "!DATA_DIR!" -l "%ROOT_DIR%\logs\postgres.log" -o "-h 127.0.0.1 -p %PORT%" >nul 2>&1
if errorlevel 1 (
    echo [PostgreSQL] : pg_ctl ʧܣ鿴 logs\postgres.log
    exit /b 6
)

for /l %%K in (1,1,30) do (
    call :IS_READY
    if not errorlevel 1 (
        echo [PostgreSQL] Яݿ 127.0.0.1:%PORT% 
        exit /b 0
    )
    ping 127.0.0.1 -n 2 >nul
)

echo [PostgreSQL] :  30 δ %PORT% ִаȫع
"!PG_CTL!" status -D "!DATA_DIR!" >nul 2>&1
if not errorlevel 1 "!PG_CTL!" stop -D "!DATA_DIR!" -m fast >nul 2>&1
echo [PostgreSQL] 鿴 logs\postgres.log ȡϸ־
exit /b 8

:IS_READY
if defined PG_ISREADY if exist "!PG_ISREADY!" (
    "!PG_ISREADY!" -h 127.0.0.1 -p %PORT% -t 1 >nul 2>&1
    exit /b !ERRORLEVEL!
)
netstat -ano | findstr /i ":%PORT% " | findstr /i "LISTENING" >nul 2>&1
exit /b !ERRORLEVEL!
