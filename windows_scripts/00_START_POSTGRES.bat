@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.0 - PostgreSQL 守护
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "ROOT_DIR=%CD%"

:: 1. 检查便携版 PostgreSQL (54320) 是否已经在运行
set "PG_IS_UP=0"
netstat -ano | findstr /i ":54320 " | findstr /i "LISTENING" >nul && set "PG_IS_UP=1"

if "!PG_IS_UP!"=="1" (
    echo [PostgreSQL] 检测到便携版 PostgreSQL (54320) 已经在运行，无需重复启动。
    exit /b 0
)

echo [PostgreSQL] 正在检查并启动便携版 PostgreSQL 数据库 (Port 54320)...

:: 2. 寻找 pg_ctl.exe 路径
set "PG_CTL="
if exist "database\pgsql\bin\pg_ctl.exe" set "PG_CTL=%ROOT_DIR%\database\pgsql\bin\pg_ctl.exe"
if not defined PG_CTL if exist "pgsql\bin\pg_ctl.exe" set "PG_CTL=%ROOT_DIR%\pgsql\bin\pg_ctl.exe"
if not defined PG_CTL if exist "M:\database\pgsql\bin\pg_ctl.exe" set "PG_CTL=M:\database\pgsql\bin\pg_ctl.exe"

if not defined PG_CTL (
    echo [PostgreSQL] 提示: 未在项目中找到便携版 pg_ctl.exe。
    exit /b 0
)

:: 3. 寻找数据目录（避免 Windows 长路径）
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
        echo [PostgreSQL] 自动将项目挂载为 M 盘，规避 Windows 长路径限制。
    ) else (
        set "DATA_DIR=%ROOT_DIR%\database\data"
    )
)

if not defined DATA_DIR (
    echo [PostgreSQL] 错误: 未找到数据库数据目录，请确认数据库是否已初始化。
    exit /b 1
)

:: 4. 清理残留 postmaster.pid 文件
if exist "!DATA_DIR!\postmaster.pid" (
    echo [PostgreSQL] 发现残留 postmaster.pid，正在安全清理...
    del /f /q "!DATA_DIR!\postmaster.pid" >nul 2>&1
)

:: 5. 启动 PostgreSQL 实例
if not exist "logs" mkdir "logs" >nul 2>&1
echo [PostgreSQL] 启动数据库实例...
start "PostgreSQL_Daemon" /B "!PG_CTL!" start -D "!DATA_DIR!" -l "%ROOT_DIR%\logs\postgres.log"

:: 6. 轮询等待端口就绪 (最多等待 8 秒)
for /l %%k in (1,1,8) do (
    timeout /t 1 /nobreak >nul
    netstat -ano | findstr /i ":54320 " | findstr /i "LISTENING" >nul && (
        echo [PostgreSQL] 便携版数据库已成功就绪在端口 54320！
        exit /b 0
    )
)

echo [PostgreSQL] 请查看 logs\postgres.log 获取详细日志。
exit /b 0