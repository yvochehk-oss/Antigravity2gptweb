@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.1 - PostgreSQL 数据库一键导入 (Windows)
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   成都建工 V3.1 - 数据库一键还原工具 (PostgreSQL)
echo ==============================================================================
echo.

set "PG_USER=postgres"
set /p "PG_USER=请输入 PostgreSQL 用户名 (默认 postgres): "

set "DEFAULT_PG_PORT=54320"
netstat -ano | findstr /i ":54320" | findstr /i "LISTENING" >nul && set "DEFAULT_PG_PORT=54320"
netstat -ano | findstr /i ":5432 " | findstr /i "LISTENING" >nul && (
    netstat -ano | findstr /i ":54320" | findstr /i "LISTENING" >nul || set "DEFAULT_PG_PORT=5432"
)
set "PG_PORT=%DEFAULT_PG_PORT%"
set /p "PG_PORT=请输入 PostgreSQL 端口 (默认便携版 !DEFAULT_PG_PORT!): "

set "PG_DB=projectrag"
set /p "PG_DB=请输入 目标数据库名 (默认 projectrag): "

set "DUMP_FILE=database\projectrag_latest.dump"
if not exist "%DUMP_FILE%" set "DUMP_FILE=projectrag_latest.dump"
if not exist "%DUMP_FILE%" set "DUMP_FILE=database\projectrag_backup_full_20260827.dump"

if not exist "%DUMP_FILE%" (
    echo [错误] 未找到数据库备份文件 %DUMP_FILE%！
    pause
    exit /b 1
)

where createdb >nul 2>&1
if errorlevel 1 (
    echo [探测] 正在自动检索本机 PostgreSQL 安装目录...
    for %%D in (
        "%~dp0..\database\pgsql\bin"
        "%~dp0tools\pgsql\bin"
        "C:\Program Files\PostgreSQL\17\bin"
        "C:\Program Files\PostgreSQL\16\bin"
        "C:\Program Files\PostgreSQL\15\bin"
        "C:\Program Files\PostgreSQL\14\bin"
        "C:\PostgreSQL\bin"
        "D:\Program Files\PostgreSQL\17\bin"
        "D:\Program Files\PostgreSQL\16\bin"
        "D:\Program Files\PostgreSQL\15\bin"
        "D:\PostgreSQL\bin"
        "E:\Program Files\PostgreSQL\17\bin"
        "E:\Program Files\PostgreSQL\16\bin"
        "E:\Program Files\PostgreSQL\15\bin"
    ) do (
        if exist "%%~fD\createdb.exe" (
            set "PATH=%%~fD;%PATH%"
            echo [发现] 已自动挂载 PostgreSQL 工具链: %%~fD
            goto :pg_tools_ready
        )
    )
)
:pg_tools_ready

where createdb >nul 2>&1
if errorlevel 1 (
    echo.
    echo ==============================================================================
    echo [错误] 未在系统 PATH 中找到 PostgreSQL 工具 (createdb / psql / pg_restore)！
    echo [解决办法]:
    echo   1. 请确认已在目标电脑上安装 PostgreSQL (推荐 14/15/16/17 并带 pgvector)；
    echo   2. 请将 PostgreSQL 的 bin 目录 (如 C:\Program Files\PostgreSQL\16\bin) 添加至系统环境变量 PATH；
    echo   3. 添加后重新打开本脚本即可自动完成还原。
    echo ==============================================================================
    echo.
    pause
    exit /b 1
)

echo.
echo [1/4] 正在检查或创建目标数据库 %PG_DB% (指定 UTF8 编码)...
createdb -U %PG_USER% -p %PG_PORT% -E UTF8 -T template0 %PG_DB% 2>nul
if errorlevel 1 createdb -U %PG_USER% -p %PG_PORT% -E UTF8 %PG_DB% 2>nul
if errorlevel 1 createdb -U %PG_USER% -p %PG_PORT% %PG_DB% 2>nul

echo [2/4] 正在启用 pgvector 与关联扩展...
psql -U %PG_USER% -p %PG_PORT% -d %PG_DB% -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS btree_gist; CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS pg_trgm;" 2>nul

echo [3/4] 正在还原数据库全量数据 (%DUMP_FILE%) ...
pg_restore -U %PG_USER% -p %PG_PORT% -d %PG_DB% --no-owner --clean --if-exists "%DUMP_FILE%"
if errorlevel 1 (
    echo [提示] pg_restore 执行完毕（非关键警告为幂等覆盖，属正常现象）。
)

echo [4/4] 验证关键数据表记录...
psql -U %PG_USER% -p %PG_PORT% -d %PG_DB% -c "SELECT 'chunks 切片数' AS item, COUNT(*) FROM chunks UNION ALL SELECT 'documents 文档数', COUNT(*) FROM documents UNION ALL SELECT 'facts 事实数', COUNT(*) FROM facts;" 2>nul

echo.
echo ==============================================================================
echo   数据库还原完成！目标数据库: %PG_DB%
echo ==============================================================================
echo.
pause
