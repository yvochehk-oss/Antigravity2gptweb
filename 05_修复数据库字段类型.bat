@echo off

@chcp 936 >nul 2>&1

title 修复数据库字段类型 (ingest_jobs)

cd /d "%~dp0"

echo ==============================================================================

echo   成都建工 V3.1 - 自动数据库自愈与字段修复

echo ==============================================================================

echo.

set "PYTHON_EXE=source_code\0.2_RAG系统\project-rag-v1.1\.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (

    set "PYTHON_EXE=source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0\.venv\Scripts\python.exe"

)

if not exist "%PYTHON_EXE%" (

    set "PYTHON_EXE=python"

)

"%PYTHON_EXE%" windows_scripts\fix_ingest_jobs_schema.py

echo.

pause

