@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.1 - PostgreSQL 守护
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "ROOT_DIR=%CD%"

rem ============================================================
rem 端口与路径 SSOT:
rem   - 端口、data dir、postmaster pid 全部由 PostgreSqlPortNegotiator 写入
rem     runtime\state\postgres.json。BAT 严禁自行硬编码端口号。
rem   - 这里只读取并按运行事实决定是否启动 pg_ctl。
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
                if defined DATA_DIR goto :DATA_DONE
            )
        )
    )
    :DATA_DONE
)

if "%PORT%"=="0" (
    rem 没有协商结果。让 C# 控制器后续拉起；这里只做兜底等待并退出。
    echo [PostgreSQL] 未发现运行事实 runtime\state\postgres.json；等待控制台调用 PostgreSqlPortNegotiator 进行协商。
    exit /b 0
)

echo [PostgreSQL] 读取运行事实：端口 %PORT%；数据目录 %DATA_DIR%

set "PG_CTL="
if exist "%ROOT_DIR%\database\pgsql\bin\pg_ctl.exe" set "PG_CTL=%ROOT_DIR%\database\pgsql\bin\pg_ctl.exe"

if not defined PG_CTL (
    echo [PostgreSQL] 错误: 未找到便携版 pg_ctl.exe，数据库不能被判定为成功启动。
    exit /b 2
)

for %%I in ("!PG_CTL!") do set "PG_BIN=%%~dpI"
if not defined DATA_DIR set "DATA_DIR=%ROOT_DIR%\database\data"

if exist "!PG_BIN!pg_isready.exe" set "PG_ISREADY=!PG_BIN!pg_isready.exe"

if not exist "logs" mkdir "logs" >nul 2>&1

call :IS_READY
if not errorlevel 1 (
    echo [PostgreSQL] 便携版 PostgreSQL 已在 127.0.0.1:%PORT% 就绪。
    exit /b 0
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

echo [PostgreSQL] 启动数据库实例并绑定 127.0.0.1:%PORT%...
"!PG_CTL!" start -D "!DATA_DIR!" -l "%ROOT_DIR%\logs\postgres.log" -o "-h 127.0.0.1 -p %PORT%" >nul 2>&1
if errorlevel 1 (
    echo [PostgreSQL] 错误: pg_ctl 启动失败，请查看 logs\postgres.log。
    exit /b 6
)

for /l %%K in (1,1,30) do (
    call :IS_READY
    if not errorlevel 1 (
        echo [PostgreSQL] 便携版数据库已在 127.0.0.1:%PORT% 就绪。
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
