@echo off
@chcp 65001 >nul 2>&1
title 成都建工 V3.1 - PostgreSQL Runtime State
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"
cd /d "%ROOT_DIR%"

rem PostgreSQL SSOT is runtime\state\postgres.json written by the C# negotiator.
rem This compatibility launcher never invents or hard-codes a database port.
set "STATE_FILE=%ROOT_DIR%\runtime\state\postgres.json"
set "PORT="
set "DATA_DIR="

if not exist "%STATE_FILE%" (
    echo [PostgreSQL] 未找到 runtime\state\postgres.json。
    echo [PostgreSQL] 请通过 START_WINDOWS.bat 或控制台启动，让 C# 协商端口并建立运行事实。
    exit /b 10
)

for /f "delims=" %%P in ('powershell.exe -NoProfile -NonInteractive -Command "$s=ConvertFrom-Json ([IO.File]::ReadAllText($env:STATE_FILE)); [Console]::Write([string]$s.Port)"') do set "PORT=%%P"
for /f "delims=" %%P in ('powershell.exe -NoProfile -NonInteractive -Command "$s=ConvertFrom-Json ([IO.File]::ReadAllText($env:STATE_FILE)); [Console]::Write([string]$s.DataDir)"') do set "DATA_DIR=%%P"

if not defined PORT (
    echo [PostgreSQL] postgres.json 缺少 Port，拒绝启动。
    exit /b 11
)
if not defined DATA_DIR set "DATA_DIR=%ROOT_DIR%\database\data"

set "PG_CTL=%ROOT_DIR%\database\pgsql\bin\pg_ctl.exe"
set "PG_ISREADY=%ROOT_DIR%\database\pgsql\bin\pg_isready.exe"
if not exist "%PG_CTL%" (
    echo [PostgreSQL] 未找到 pg_ctl.exe：%PG_CTL%
    exit /b 12
)
if not exist "%DATA_DIR%" (
    echo [PostgreSQL] 未找到数据目录：%DATA_DIR%
    exit /b 13
)

if not exist "%ROOT_DIR%\logs" mkdir "%ROOT_DIR%\logs" >nul 2>&1

call :IS_READY
if not errorlevel 1 (
    echo [PostgreSQL] 已就绪：127.0.0.1:%PORT%
    exit /b 0
)

if exist "%DATA_DIR%\postmaster.pid" (
    "%PG_CTL%" status -D "%DATA_DIR%" >nul 2>&1
    if not errorlevel 1 (
        echo [PostgreSQL] 数据目录已有 PostgreSQL 进程，但状态端口未就绪；拒绝重复启动。
        exit /b 14
    )
    del /f /q "%DATA_DIR%\postmaster.pid" >nul 2>&1
    if exist "%DATA_DIR%\postmaster.pid" exit /b 15
)

echo [PostgreSQL] 按运行事实启动 127.0.0.1:%PORT% ...
"%PG_CTL%" start -D "%DATA_DIR%" -l "%ROOT_DIR%\logs\postgres.log" -o "-h 127.0.0.1 -p %PORT%" >nul 2>&1
if errorlevel 1 exit /b 16

for /l %%K in (1,1,30) do (
    call :IS_READY
    if not errorlevel 1 (
        echo [PostgreSQL] 已就绪：127.0.0.1:%PORT%
        exit /b 0
    )
    ping 127.0.0.1 -n 2 >nul
)

echo [PostgreSQL] 启动超时，请检查 logs\postgres.log。
exit /b 17

:IS_READY
if exist "%PG_ISREADY%" (
    "%PG_ISREADY%" -h 127.0.0.1 -p %PORT% -t 1 >nul 2>&1
    exit /b !ERRORLEVEL!
)
netstat -ano | findstr /i ":%PORT% " | findstr /i "LISTENING" >nul 2>&1
exit /b !ERRORLEVEL!
