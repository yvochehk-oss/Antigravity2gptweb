@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.0 - PostgreSQL 智能启动守护
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "ROOT_DIR=%CD%"

:: 1. 检查 PostgreSQL 是否已在运行
set "PG_IS_UP=0"
netstat -ano | findstr /i "54320" | findstr /i "LISTENING" >nul && set "PG_IS_UP=1"
netstat -ano | findstr /i "5432" | findstr /i "LISTENING" >nul && set "PG_IS_UP=1"

if "!PG_IS_UP!"=="1" (
    echo [PostgreSQL] 本地 PostgreSQL 端口 5432 或 54320 已经在正常监听中，无需重复启动。
    exit /b 0
)

echo [PostgreSQL] 正在检测并启动本地 PostgreSQL 数据库...

:: 2. 寻找 pg_ctl.exe 路径
set "PG_CTL="
if exist "database\pgsql\bin\pg_ctl.exe" set "PG_CTL=%ROOT_DIR%\database\pgsql\bin\pg_ctl.exe"
if not defined PG_CTL if exist "pgsql\bin\pg_ctl.exe" set "PG_CTL=%ROOT_DIR%\pgsql\bin\pg_ctl.exe"
if not defined PG_CTL if exist "M:\database\pgsql\bin\pg_ctl.exe" set "PG_CTL=M:\database\pgsql\bin\pg_ctl.exe"

if not defined PG_CTL (
    echo [PostgreSQL] 提示: 未在项目中找到便携版 pg_ctl.exe，将依赖系统已安装的 PostgreSQL 服务。
    exit /b 0
)

:: 3. 寻找数据目录，并处理 Windows 中文路径避障
set "DATA_DIR="
if exist "%~d0\projectrag_pgdata" set "DATA_DIR=%~d0\projectrag_pgdata"
if not defined DATA_DIR if exist "C:\projectrag_pgdata" set "DATA_DIR=C:\projectrag_pgdata"
if not defined DATA_DIR if exist "M:\database\data" set "DATA_DIR=M:\database\data"
if not defined DATA_DIR if exist "%ROOT_DIR%\database\data" (
    subst M: /d >nul 2>&1
    subst M: "%ROOT_DIR%" >nul 2>&1
    if exist "M:\database\data" (
        set "DATA_DIR=M:\database\data"
        set "PG_CTL=M:\database\pgsql\bin\pg_ctl.exe"
        echo [PostgreSQL] 已自动将项目根映射为 M 盘，规避 Windows 中文路径编码问题。
    ) else (
        set "DATA_DIR=%ROOT_DIR%\database\data"
    )
)

if not defined DATA_DIR (
    echo [PostgreSQL] 警告: 未找到 database\data 数据目录，请确认数据库是否已初始化。
    exit /b 1
)

:: 4. 清除可能残留的 postmaster.pid 锁文件
if exist "!DATA_DIR!\postmaster.pid" (
    echo [PostgreSQL] 发现残留的 postmaster.pid，正在安全清理...
    del /f /q "!DATA_DIR!\postmaster.pid" >nul 2>&1
)

:: 5. 启动 PostgreSQL 实例
if not exist "logs" mkdir "logs" >nul 2>&1
echo [PostgreSQL] 正在拉起数据库实例...
start "PostgreSQL_Daemon" /B "!PG_CTL!" start -D "!DATA_DIR!" -l "%ROOT_DIR%\logs\postgres.log"

:: 6. 轮询等待端口就绪 (最多等待 6 秒)
for /l %%k in (1,1,6) do (
    timeout /t 1 /nobreak >nul
    netstat -ano | findstr /i "54320" | findstr /i "LISTENING" >nul && (
        echo [PostgreSQL] 数据库已成功在端口 54320 监听就绪！
        exit /b 0
    )
    netstat -ano | findstr /i "5432" | findstr /i "LISTENING" >nul && (
        echo [PostgreSQL] 数据库已成功在端口 5432 监听就绪！
        exit /b 0
    )
)

echo [PostgreSQL] 启动命令已下发，请查看 logs\postgres.log 获取详细日志。
exit /b 0
