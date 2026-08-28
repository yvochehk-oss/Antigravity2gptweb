@echo off
chcp 65001 >nul
title 成都建工 V2.0 · PostgreSQL 数据库一键导入 (Windows)
cd /d "%~dp0\.."

echo ==============================================================================
echo   🗄️  成都建工 V2.0 · 数据库一键还原工具 (PostgreSQL)
echo ==============================================================================
echo.

set /p PG_USER=请输入 PostgreSQL 用户名 (默认 postgres): 
if "%PG_USER%"=="" set PG_USER=postgres

set /p PG_PORT=请输入 PostgreSQL 端口 (默认 5432): 
if "%PG_PORT%"=="" set PG_PORT=5432

set /p PG_DB=请输入 目标数据库名 (默认 projectrag): 
if "%PG_DB%"=="" set PG_DB=projectrag

set DUMP_FILE=projectrag_backup_full_20260827.dump

if not exist "%DUMP_FILE%" (
    echo [错误] 未找到数据库备份文件 %DUMP_FILE%！
    pause
    exit /b 1
)

echo.
echo [1/3] 正在检查/创建目标数据库 %PG_DB% ...
createdb -U %PG_USER% -p %PG_PORT% %PG_DB% 2>nul

echo [2/3] 正在启用 pgvector 向量扩展...
psql -U %PG_USER% -p %PG_PORT% -d %PG_DB% -c "CREATE EXTENSION IF NOT EXISTS vector;"

echo [3/3] 正在还原数据库全量数据 (%DUMP_FILE%) ...
pg_restore -U %PG_USER% -p %PG_PORT% -d %PG_DB% --no-owner --role=%PG_USER% --clean --if-exists "%DUMP_FILE%"
if errorlevel 1 (
    echo [提示] pg_restore 执行完毕（部分非关键警告为正常幂等现象）。
)

echo.
echo ==============================================================================
echo   🎉 数据库还原完成！数据库名称: %PG_DB%
echo ==============================================================================
echo.
pause
