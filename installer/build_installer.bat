@echo off
@chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "INSTALLER_DIR=%ROOT%\installer"
set "PUBLISHED_EXE=%ROOT%\desktop_apps\windows\publish\win-x64\成都建工控制台3.1.exe"
set "ISCC_PATH="

echo [Build] 查找 Inno Setup 编译器 (ISCC.exe)...
for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
) do if exist "%%~P" set "ISCC_PATH=%%~P"

if not defined ISCC_PATH (
    echo [错误] 未找到 Inno Setup 6 编译器 ISCC.exe。
    exit /b 1
)

if not exist "%PUBLISHED_EXE%" (
    echo [Build] 未找到 canonical 发布产物，先构建 Windows 控制台...
    call "%ROOT%\desktop_apps\windows\build.cmd" -RunSmokeTests
    if errorlevel 1 exit /b %ERRORLEVEL%
)

if not exist "%PUBLISHED_EXE%" (
    echo [错误] 构建后仍未找到：%PUBLISHED_EXE%
    exit /b 2
)

if not exist "%ROOT%\runtime\python\Scripts\python.exe" echo [警告] runtime\python 缺失，安装包将不含嵌入式 Python。
if not exist "%ROOT%\source_code\0.3_老板端安卓App_天府掌舵\dist\index.html" echo [警告] Boss dist 缺失，安装包将不含预编译前端。
if not exist "%ROOT%\models\local-llm\runtime-win-cpu-x64\llama-server.exe" echo [警告] llama-server runtime 缺失。

echo [Build] 使用：%ISCC_PATH%
"%ISCC_PATH%" "%INSTALLER_DIR%\setup.iss" /DMyAppVersion=3.1.0
if errorlevel 1 exit /b %ERRORLEVEL%

set "OUTPUT=%INSTALLER_DIR%\output\ChengduConstructionConsole-v3.1.0-Setup.exe"
if not exist "%OUTPUT%" (
    echo [错误] Inno Setup 返回成功，但未找到预期产物：%OUTPUT%
    exit /b 3
)

for %%F in ("%OUTPUT%") do echo [Build] 安装包完成：%%~fF (%%~zF bytes)
exit /b 0
