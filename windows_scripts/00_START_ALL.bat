@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.0 - 一键启动全部服务
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."

echo ==============================================================================
echo   [成都建工] 成都建工 V3.0 财税智控与 IDP 穿透中枢 [Windows 一键启动]
echo   硬件适配: Intel/AMD CPU (AVX2加速) + 16GB+ 内存 / 支持离线推理
echo   本地大模型: 星火 Spark-X2.5-4B (CPU AVX2加速离线推理)
echo ==============================================================================
echo.

:: [0/5] 检查并启动 PostgreSQL 数据库
if exist "%SCRIPT_DIR%00_START_POSTGRES.bat" (
    call "%SCRIPT_DIR%00_START_POSTGRES.bat"
)

:: 智能探测并适配活跃 PostgreSQL 端口 (5432 vs 54320)
set "ACTIVE_PG_PORT=54320"`r`nnetstat -ano | findstr /i ":54320 " | findstr /i "LISTENING" >nul && set "ACTIVE_PG_PORT=54320"
set "DATABASE_URL=postgresql://postgres@127.0.0.1:!ACTIVE_PG_PORT!/projectrag"
set "PROJECT_RAG_DB_URL=postgresql://postgres@127.0.0.1:!ACTIVE_PG_PORT!/projectrag"
echo [数据库] 全局自动绑定活跃 PostgreSQL 端口: !ACTIVE_PG_PORT!

echo [1/5] 正在启动本地大模型服务 (Port 8930)...
start "01_本地大模型服务 (Port 8930)" cmd /k "call "%SCRIPT_DIR%01_START_LLM.bat""
timeout /t 3 /nobreak >nul

echo [2/5] 正在启动 RAG 知识证据中枢 (Port 8922)...
start "02_RAG事实中台 (Port 8922)" cmd /k "call "%SCRIPT_DIR%02_START_RAG.bat""
timeout /t 2 /nobreak >nul

echo [3/5] 正在启动 IDP 穿透式录入引擎 (Port 8933)...
set "IDP_PORT=8933"
start "03_IDP文档录入引擎 (Port 8933)" cmd /k "call "%SCRIPT_DIR%..\source_code\0.4_IDP文档录入引擎_V3.0\START_IDP_WINDOWS.bat""
timeout /t 2 /nobreak >nul

echo [4/5] 正在启动 税务管理与 Native V3 Boss API (Port 8921)...
start "04_税务管理系统 (Port 8921)" cmd /k "call "%SCRIPT_DIR%03_START_TAX.bat""
timeout /t 2 /nobreak >nul

echo [5/5] 正在启动 老板端 Web 移动驾驶舱 (Port 5173)...
start "05_老板端Web (Port 5173)" cmd /k "call "%SCRIPT_DIR%04_START_WEB.bat""
timeout /t 3 /nobreak >nul

echo.
echo ==============================================================================
echo   [完成] 成都建工 V3.0 全系统已成功拉起！
echo   【核心业务访问入口】
echo   [移动端] 老板端移动驾驶舱:    http://127.0.0.1:5173 (推荐)
echo   [建工]   税务管理中台 Web:    http://127.0.0.1:8921
echo   [智脑]   RAG 知识证据中枢:    http://127.0.0.1:8922
echo   [文档]   IDP 文档录入与审计:  http://127.0.0.1:8933
echo   [AI]     星火 Spark-X2.5-4B:  http://127.0.0.1:8930/v1
echo   [数据库] PostgreSQL 数据库:   127.0.0.1:!ACTIVE_PG_PORT! (projectrag)
echo ------------------------------------------------------------------------------
echo   [秘钥] 默认演示登录账号：admin  /  密码：密码同用户名
echo   [知识库] OpenAPI 接口文档：http://127.0.0.1:8921/docs
echo ==============================================================================
echo.
echo 正在自动打开浏览器...
start http://127.0.0.1:5173
start http://127.0.0.1:8921
pause
