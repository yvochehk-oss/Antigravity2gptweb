@echo off
REM ============================================================
REM V3.1 Inno Setup 打包脚本 (Windows)
REM 用途：一键编译 Setup.exe，自动包含 runtime/python 与 boss/dist
REM 前置：Inno Setup 6.4+ 已安装
REM ============================================================

setlocal enabledelayedexpansion

set "ROOT=%~dp0.."
set "INSTALLER_DIR=%ROOT%\installer"
set "ISCC_PATH="

echo [Build] 查找 Inno Setup 编译器 (iscc.exe)...

REM ---- 步骤 1：定位 Inno Setup 编译器 ----
for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
) do (
    if exist "%%~P" set "ISCC_PATH=%%~P"
)

if "%ISCC_PATH%"=="" (
    echo [错误] 未找到 ISCC.exe。请安装 Inno Setup 6.4+：
    echo        https://jrsoftware.org/isdl.php
    exit /b 1
)

echo [Build] 使用编译器：%ISCC_PATH%

REM ---- 步骤 2：检查前置产物 ----
echo [Build] 检查前置构建产物...

set "MISSING="

if not exist "%ROOT%\desktop_apps\windows\bin\Release\成都建工控制台.exe" (
    set "MISSING=!MISSING!  - desktop_apps\windows\bin\Release\成都建工控制台.exe (C# 主程序未编译)" & echo.
)

if not exist "%ROOT%\runtime\python\Scripts\python.exe" (
    echo [Build] 警告：runtime\python 缺失，安装包将不含嵌入式 Python。
)

if not exist "%ROOT%\source_code\0.3_老板端安卓App_天府掌舵\dist\index.html" (
    echo [Build] 警告：boss dist 缺失，安装包将不含预编译前端。
)

if not exist "%ROOT%\models\local-llm\runtime-win-cpu-x64\llama-server.exe" (
    if not exist "%ROOT%\models\local-llm\runtime-win-cpu-x64\llama-server-avx2.exe" (
        echo [Build] 警告：llama-server 二进制缺失，安装包将不含大模型运行时。
    )
)

if not "%MISSING%"=="" (
    echo.
    echo [错误] 以下关键产物缺失：
    echo %MISSING%
    echo.
    echo 请先完成以下步骤：
    echo   1. cd desktop_apps\windows ^&^& dotnet publish -c Release
    echo   2. python windows_scripts\build_embedded_python.py --source ... --output runtime\python
    echo   3. cd source_code\0.3_老板端安卓App_天府掌舵 ^&^& npm run build
    exit /b 1
)

REM ---- 步骤 3：执行 Inno Setup 编译 ----
echo.
echo [Build] 开始编译 Setup.exe...
"%ISCC_PATH%" "%INSTALLER_DIR%\setup.iss" ^
    /DMyAppVersion=3.1.0

if errorlevel 1 (
    echo [错误] Inno Setup 编译失败。
    exit /b 1
)

REM ---- 步骤 4：验证产物 ----
if exist "%INSTALLER_DIR%\output\ChengduConstructionConsole-v3.1.0-Setup.exe" (
    for %%F in ("%INSTALLER_DIR%\output\ChengduConstructionConsole-v3.1.0-Setup.exe") do (
        echo [Build] 编译成功：%%~nxF (%%~zF 字节)
    )
) else (
    echo [警告] 未在预期位置找到 Setup.exe，请检查 output 目录。
)

echo.
echo [Build] 完成。
exit /b 0
