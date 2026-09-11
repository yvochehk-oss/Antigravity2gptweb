@echo off
@chcp 936 >nul 2>&1
title 鎴愰兘寤哄伐 V3.1 - 涓€閿惎鍔ㄥ叏閮ㄦ湇鍔?
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "ROOT_DIR=%CD%"

echo ==============================================================================
echo   [鎴愰兘寤哄伐] V3.1 Windows 鍏ㄦ湇鍔″惎鍔ㄦ帶鍒堕潰
echo   PostgreSQL: runtime\state\postgres.json ^| LLM:8930 ^| RAG:8922 ^| Tax:8921 ^| Web:5173
echo ==============================================================================
echo.

rem ============================================================
rem [0/5] PostgreSQL 鏄叏绯荤粺纭墠缃紝澶辫触鏃剁姝㈢户缁媺璧蜂笟鍔℃湇鍔°€?
rem 绔彛浠庤繍琛屼簨瀹炶鍙栵紱00_START_POSTGRES.bat 涓嶅啀纭紪鐮?54320銆?
rem ============================================================
if not exist "%SCRIPT_DIR%00_START_POSTGRES.bat" (
    echo [閿欒] 缂哄皯 PostgreSQL canonical launcher: %SCRIPT_DIR%00_START_POSTGRES.bat
    exit /b 10
)
call "%SCRIPT_DIR%00_START_POSTGRES.bat"
if errorlevel 1 (
    echo [閿欒] PostgreSQL 鍚姩闂ㄧ鏈€氳繃锛屽凡涓鍚庣画鏈嶅姟銆?
    exit /b 11
)

rem 绔彛銆乁RL 閮戒粠 postgres.json 璇诲彇锛涘鏋滅姸鎬佹枃浠朵笉瀛樺湪鍒?fallback 鍒伴粯璁?54320銆?
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

echo [鏁版嵁搴揮 PostgreSQL 鍗忓晢绔彛 = %DB_PORT% (%DB_HOST%)
echo.

echo [1/5] 姝ｅ湪鍚姩鏈湴澶фā鍨嬫湇鍔?(Port 8930)...
start "01_鏈湴澶фā鍨嬫湇鍔?(Port 8930)" cmd.exe /d /c call "%SCRIPT_DIR%01_START_LLM.bat"
call :WAIT_PORT 8930 45 "鏈湴澶фā鍨?
if errorlevel 1 exit /b 21

echo [2/5] 姝ｅ湪鍚姩 RAG 鐭ヨ瘑璇佹嵁涓灑 (Port 8922)...
start "02_RAG浜嬪疄涓彴 (Port 8922)" cmd.exe /d /c call "%SCRIPT_DIR%02_START_RAG.bat"
call :WAIT_PORT 8922 45 "RAG鐭ヨ瘑涓彴"
if errorlevel 1 exit /b 22

echo [3/5] 姝ｅ湪鍚姩 IDP 鏂囨。褰曞叆寮曟搸 (Port 8933)...
set "IDP_PORT=8933"
start "03_IDP鏂囨。褰曞叆寮曟搸 (Port 8933)" cmd.exe /d /c call "%ROOT_DIR%\source_code\0.4_IDP鏂囨。褰曞叆寮曟搸_V3.1\START_IDP_WINDOWS.bat"
call :WAIT_PORT 8933 90 "IDP鏂囨。褰曞叆寮曟搸"
if errorlevel 1 exit /b 23

echo [4/5] 姝ｅ湪鍚姩绋庡姟涓彴 (Port 8921)...
start "04_绋庡姟绠＄悊绯荤粺 (Port 8921)" cmd.exe /d /c call "%SCRIPT_DIR%03_START_TAX.bat"
call :WAIT_PORT 8921 45 "绋庡姟涓彴"
if errorlevel 1 exit /b 24

echo [5/5] 姝ｅ湪鍚姩鑰佹澘绔?Web (Port 5173)...
start "05_鑰佹澘绔疻eb (Port 5173)" cmd.exe /d /c call "%SCRIPT_DIR%04_START_WEB.bat"
call :WAIT_PORT 5173 30 "鑰佹澘绔疻eb"
if errorlevel 1 exit /b 25

echo.
echo ==============================================================================
echo   [瀹屾垚] 鎴愰兘寤哄伐 V3.1 鍏ㄦ湇鍔＄鍙ｉ棬绂佸凡鍏ㄩ儴閫氳繃銆?
echo   [绉诲姩绔痌 鑰佹澘绔Щ鍔ㄩ┚椹惰埍:    http://127.0.0.1:5173
echo   [寤哄伐]   绋庡姟绠＄悊涓彴:        http://127.0.0.1:8921
echo   [鏅鸿剳]   RAG 鐭ヨ瘑璇佹嵁涓灑:    http://127.0.0.1:8922
echo   [鏂囨。]   IDP 鏂囨。褰曞叆涓庡璁?  http://127.0.0.1:8933
echo   [AI]     鏈湴澶фā鍨?OpenAI API: http://127.0.0.1:8930/v1
echo   [鏁版嵁搴揮 PostgreSQL:           %DB_HOST%:%DB_PORT%/projectrag
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
        echo [灏辩华] !_WAIT_NAME! 宸茬洃鍚鍙?!_WAIT_PORT!銆?
        exit /b 0
    )
    if %%I LEQ 10 (
        timeout /t 1 /nobreak >nul
    ) else (
        timeout /t 2 /nobreak >nul
    )
)
echo [閿欒] !_WAIT_NAME! 鍦ㄩ€€閬跨瓑寰呯獥鍙ｅ唴鏈洃鍚鍙?!_WAIT_PORT!锛屽仠姝㈠惎鍔ㄩ摼銆?
exit /b 1
