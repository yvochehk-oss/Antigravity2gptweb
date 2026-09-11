@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.1 - �?�?��动全部服�?
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "ROOT_DIR=%CD%"

echo ==============================================================================
echo   [成都建工] V3.1 Windows 全服务启动控制面
echo   PostgreSQL: runtime\state\postgres.json ^| LLM:8930 ^| RAG:8922 ^| Tax:8921 ^| Web:5173
echo ==============================================================================
echo.

rem ============================================================
rem [0/5] PostgreSQL �?��系统�?���?��失败时�?止继�?��起业务服务�??
rem �?��从运行事实�?取；00_START_POSTGRES.bat 不再�?���?54320�?
rem ============================================================
if not exist "%SCRIPT_DIR%00_START_POSTGRES.bat" (
    echo [错�?] 缺少 PostgreSQL canonical launcher: %SCRIPT_DIR%00_START_POSTGRES.bat
    exit /b 10
)
call "%SCRIPT_DIR%00_START_POSTGRES.bat"
if errorlevel 1 (
    echo [错�?] PostgreSQL �?��门�?�??�过，已�??后续服务�?
    exit /b 11
)

rem �?��、URL 都从 postgres.json 读取；�?果状态文件不存在�?fallback 到默�?54320�?
set "DB_PORT=54320"
set "DB_HOST=127.0.0.1"
if exist "%ROOT_DIR%\runtime\state\postgres.json" (
    for /f "usebackq tokens=*" %%L in ("%ROOT_DIR%\runtime\state\postgres.json") do (
        echo %%L | findstr /i "\"Port\"" >nul && (
            for /f "tokens=2 delims=:," %%P in ("%%L") do (
                set "DB_PORT=%%P"
                set "DB_PORT=!DB_PORT: =!"
                set "DB_PORT=!DB_PORT:,=!"
            )
        )
        echo %%L | findstr /i "\"Host\"" >nul && (
            for /f "tokens=2 delims=:, " %%P in ("%%L") do (
                if not defined DB_HOST_PARSED (
                    set "DB_HOST=%%P"
                    set "DB_HOST=!DB_HOST: =!"
                    set "DB_HOST=!DB_HOST:,=!"
                    set "DB_HOST_PARSED=1"
                )
            )
        )
    )
)

set "DATABASE_URL=postgresql://postgres@%DB_HOST%:%DB_PORT%/projectrag"
set "PROJECT_RAG_DB_URL=postgresql://postgres@%DB_HOST%:%DB_PORT%/projectrag"
set "TAX_RAG_SERVICE_URL=http://127.0.0.1:8922"
set "RAG_LLM_BASE_URL=http://127.0.0.1:8930/v1"
set "RAG_LLM_LOCAL_BASE_URL=http://127.0.0.1:8930/v1"
set "RAG_LLM_MODEL=spark-x2.5-4b"
set "RAG_LLM_LOCAL_MODEL=spark-x2.5-4b"
set "LING_BASE_URL=http://127.0.0.1:8930/v1"
set "LING_MODEL=spark-x2.5-4b"
if not exist "%ROOT_DIR%\models\local-llm\Spark-X2.5-4B-Q4_K_M.gguf" if exist "%ROOT_DIR%\models\local-llm\Qwen3.5-2B-Q4_K_M.gguf" (
    set "RAG_LLM_MODEL=qwen3.5-2b"
    set "RAG_LLM_LOCAL_MODEL=qwen3.5-2b"
    set "LING_MODEL=qwen3.5-2b"
)

echo [数据库] PostgreSQL 协商�?�� = %DB_PORT% (%DB_HOST%)
echo.

echo [1/5] 正在�?���?��大模型服�?(Port 8930)...
start "01_�?��大模型服�?(Port 8930)" cmd.exe /d /c call "%SCRIPT_DIR%01_START_LLM.bat"
call :WAIT_PORT 8930 45 "�?��大模�?
if errorlevel 1 exit /b 21

echo [2/5] 正在�?�� RAG 知识证据�?�� (Port 8922)...
start "02_RAG事实�?�� (Port 8922)" cmd.exe /d /c call "%SCRIPT_DIR%02_START_RAG.bat"
call :WAIT_PORT 8922 45 "RAG知识�?��"
if errorlevel 1 exit /b 22

echo [3/5] 正在�?�� IDP 文档录入引擎 (Port 8933)...
set "IDP_PORT=8933"
start "03_IDP文档录入引擎 (Port 8933)" cmd.exe /d /c call "%ROOT_DIR%\source_code\0.4_IDP文档录入引擎_V3.1\START_IDP_WINDOWS.bat"
call :WAIT_PORT 8933 90 "IDP文档录入引擎"
if errorlevel 1 exit /b 23

echo [4/5] 正在�?��税务�?�� (Port 8921)...
start "04_税务管理系统 (Port 8921)" cmd.exe /d /c call "%SCRIPT_DIR%03_START_TAX.bat"
call :WAIT_PORT 8921 45 "税务�?��"
if errorlevel 1 exit /b 24

echo [5/5] 正在�?��老板�?Web (Port 5173)...
start "05_老板端Web (Port 5173)" cmd.exe /d /c call "%SCRIPT_DIR%04_START_WEB.bat"
call :WAIT_PORT 5173 30 "老板端Web"
if errorlevel 1 exit /b 25

echo.
echo ==============================================================================
echo   [完成] 成都建工 V3.1 全服务�?口门禁已全部通过�?
echo   [移动端] 老板�?��动驾驶舱:    http://127.0.0.1:5173
echo   [建工]   税务管理�?��:        http://127.0.0.1:8921
echo   [智脑]   RAG 知识证据�?��:    http://127.0.0.1:8922
echo   [文档]   IDP 文档录入与�?�?  http://127.0.0.1:8933
echo   [AI]     �?��大模�?OpenAI API: http://127.0.0.1:8930/v1
echo   [数据库] PostgreSQL:           %DB_HOST%:%DB_PORT%/projectrag
echo ==============================================================================
start "" http://127.0.0.1:5173
start "" http://127.0.0.1:8921
exit /b 0

:WAIT_PORT
set "_WAIT_PORT=%~1"
set "_WAIT_TRIES=%~2"
set "_WAIT_NAME=%~3"
for /l %%I in (1,1,!_WAIT_TRIES!) do (
    netstat -ano | findstr /i ":!_WAIT_PORT! " | findstr /i "LISTENING" >nul 2>&1 && (
        echo [就绪] !_WAIT_NAME! 已监�??�?!_WAIT_PORT!�?
        exit /b 0
    )
    if %%I LEQ 10 (
        ping 127.0.0.1 -n 2 >nul
    ) else (
        ping 127.0.0.1 -n 3 >nul
    )
)
echo [错�?] !_WAIT_NAME! 在�??避等待窗口内�?���??�?!_WAIT_PORT!，停止启动链�?
exit /b 1
