@echo off
@chcp 65001 >nul
title 修复 VCRUNTIME140_1.dll 缺失问题
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"

echo ==============================================================================
echo   正在修复 Windows 缺少 VCRUNTIME140_1.dll (C++ 运行库) 的问题
echo ==============================================================================
echo.

set "TOOLS_DIR=%SCRIPT_DIR%tools"
set "REDIST_EXE=%TOOLS_DIR%\vc_redist.x64.exe"
if not exist "%TOOLS_DIR%" mkdir "%TOOLS_DIR%"

:: 检查文件是否已存在且大小大于 10MB (避免下到 0 字节损坏文件)
set "NEED_DOWNLOAD=1"
if exist "%REDIST_EXE%" (
    for %%A in ("%REDIST_EXE%") do (
        if %%~zA GTR 10000000 set "NEED_DOWNLOAD=0"
    )
)

if "!NEED_DOWNLOAD!"=="1" (
    echo [1/2] 正在从微软官方下载 C++ 运行库 (约 24MB)...
    echo.
    if exist "%REDIST_EXE%" del "%REDIST_EXE%"
    
    :: 使用 PowerShell 替代 curl，避免在部分旧版 Windows 10 上 curl 报错或 aka.ms 被墙导致 0 字节
    powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile '%REDIST_EXE%'"
    
    if not exist "%REDIST_EXE%" (
        echo [错误] 下载失败！请检查你的网络连接。
        echo 你也可以手动在浏览器中打开网址下载：https://aka.ms/vs/17/release/vc_redist.x64.exe
        echo 然后把下载的 vc_redist.x64.exe 放到 %TOOLS_DIR% 目录下。
        pause
        exit /b 1
    )
) else (
    echo [1/2] 已发现下载好的完整安装包。
)

echo.
echo [2/2] 正在启动微软官方安装程序...
echo -----------------------------------------------------------
echo 注意：接下来会弹出安装界面。
echo 请勾选【我同意】，然后点击【安装】。
echo 如果提示是否允许修改，请点击【是】。
echo 如果提示重启，请先选【否】。
echo -----------------------------------------------------------
echo.
"%REDIST_EXE%"

echo.
echo ==============================================================================
echo   修复完成！请现在重新运行原本报错的脚本。
echo ==============================================================================
echo.
pause
