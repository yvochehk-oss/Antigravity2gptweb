@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.0 - PostgreSQL 54320 守护
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "ROOT_DIR=%CD%"
set "PORT=54320"
set "WATCHDOG=%ROOT_DIR%\scripts\database\windows\postgres_watchdog.ps1"
set "PG_CTL="
set "PG_ISREADY="

call :IS_READY
if not errorlevel 1 (
    echo [PostgreSQL] 便携版 PostgreSQL 已在 127.0.0.1:%PORT% 就绪。
    call :START_WATCHDOG
    if errorlevel 1 exit /b 7
    exit /b 0
)

echo [PostgreSQL] 正在检查并启动便携版 PostgreSQL (Port %PORT%)...

if exist "database\pgsql\bin\pg_ctl.exe" set "PG_CTL=%ROOT_DIR%\database\pgsql\bin\pg_ctl.exe"
if not defined PG_CTL if exist "pgsql\bin\pg_ctl.exe" set "PG_CTL=%ROOT_DIR%\pgsql\bin\pg_ctl.exe"
if not defined PG_CTL if exist "M:\database\pgsql\bin\pg_ctl.exe" set "PG_CTL=M:\database\pgsql\bin\pg_ctl.exe"

if not defined PG_CTL (
    echo [PostgreSQL] 错误: 未找到便携版 pg_ctl.exe，数据库不能被判定为成功启动。
    exit /b 2
)

for %%I in ("!PG_CTL!") do set "PG_BIN=%%~dpI"
if exist "!PG_BIN!pg_isready.exe" set "PG_ISREADY=!PG_BIN!pg_isready.exe"

set "DATA_DIR="
if exist "%~d0\projectrag_pgdata" set "DATA_DIR=%~d0\projectrag_pgdata"
if not defined DATA_DIR if exist "C:\projectrag_pgdata" set "DATA_DIR=C:\projectrag_pgdata"
if not defined DATA_DIR if exist "M:\database\data" set "DATA_DIR=M:\database\data"
if not defined DATA_DIR if exist "%ROOT_DIR%\database\data" (
    subst M: /d >nul 2>&1
    subst M: "%ROOT_DIR%" >nul 2>&1
    if exist "M:\database\data" (
        set "DATA_DIR=M:\database\data"
        if exist "M:\database\pgsql\bin\pg_ctl.exe" (
            set "PG_CTL=M:\database\pgsql\bin\pg_ctl.exe"
            set "PG_BIN=M:\database\pgsql\bin\"
            if exist "M:\database\pgsql\bin\pg_isready.exe" set "PG_ISREADY=M:\database\pgsql\bin\pg_isready.exe"
        )
        echo [PostgreSQL] 已将项目临时映射为 M 盘以规避 Windows 长路径限制。
    ) else (
        set "DATA_DIR=%ROOT_DIR%\database\data"
    )
)

if not defined DATA_DIR (
    echo [PostgreSQL] 错误: 未找到数据库数据目录，请确认便携数据库已经初始化。
    exit /b 3
)

if exist "!DATA_DIR!\postmaster.pid" (
    "!PG_CTL!" status -D "!DATA_DIR!" >nul 2>&1
    if not errorlevel 1 (
        echo [PostgreSQL] 错误: 数据目录对应的 PostgreSQL 进程仍存活，但 %PORT% 未就绪。
        echo [PostgreSQL] 为避免误删活动实例的 postmaster.pid，本次启动已安全中止。
        exit /b 4
    )
    echo [PostgreSQL] 检测到失效的 postmaster.pid，正在清理...
    del /f /q "!DATA_DIR!\postmaster.pid" >nul 2>&1
    if exist "!DATA_DIR!\postmaster.pid" (
        echo [PostgreSQL] 错误: 无法清理失效的 postmaster.pid。
        exit /b 5
    )
)

if not exist "logs" mkdir "logs" >nul 2>&1
echo [PostgreSQL] 启动数据库实例并强制绑定 127.0.0.1:%PORT%...
"!PG_CTL!" start -D "!DATA_DIR!" -l "%ROOT_DIR%\logs\postgres.log" -o "-h 127.0.0.1 -p %PORT%" >nul 2>&1
if errorlevel 1 (
    echo [PostgreSQL] 错误: pg_ctl 启动失败，请查看 logs\postgres.log。
    exit /b 6
)

for /l %%K in (1,1,30) do (
    call :IS_READY
    if not errorlevel 1 (
        echo [PostgreSQL] 便携版数据库已在 127.0.0.1:%PORT% 就绪。
        call :START_WATCHDOG
        if errorlevel 1 exit /b 7
        exit /b 0
    )
    timeout /t 1 /nobreak >nul
)

echo [PostgreSQL] 错误: 启动后 30 秒内仍未在 %PORT% 就绪，执行安全回滚。
"!PG_CTL!" status -D "!DATA_DIR!" >nul 2>&1
if not errorlevel 1 "!PG_CTL!" stop -D "!DATA_DIR!" -m fast >nul 2>&1
echo [PostgreSQL] 请查看 logs\postgres.log 获取详细日志。
exit /b 8

:IS_READY
if defined PG_ISREADY if exist "!PG_ISREADY!" (
    "!PG_ISREADY!" -h 127.0.0.1 -p %PORT% -t 1 >nul 2>&1
    exit /b !ERRORLEVEL!
)
netstat -ano | findstr /i ":%PORT% " | findstr /i "LISTENING" >nul 2>&1
exit /b !ERRORLEVEL!

:START_WATCHDOG
if not exist "%WATCHDOG%" (
    echo [PostgreSQL] 错误: 缺少自愈守护脚本 %WATCHDOG%。
    exit /b 1
)
where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo [PostgreSQL] 错误: 未找到 powershell.exe，无法启动数据库自愈守护。
    exit /b 1
)
start "" /B powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%WATCHDOG%" -RootDir "%ROOT_DIR%" -Port %PORT% >nul 2>&1
exit /b 0
