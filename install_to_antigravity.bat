@echo off
@chcp 65001 >nul 2>&1
title 安装 safari-chatgpt-reasoner 技能到 Antigravity
echo ==============================================================================
echo   正在自动安装 safari-chatgpt-reasoner 技能到 Antigravity 全局技能库...
echo ==============================================================================
echo.

set "TARGET_DIR=%USERPROFILE%\.gemini\antigravity\skills\safari-chatgpt-reasoner"

if not exist "%TARGET_DIR%" (
    echo [创建目录] %TARGET_DIR%
    mkdir "%TARGET_DIR%"
)

echo [正在复制文件] ...
xcopy "%~dp0*" "%TARGET_DIR%\" /E /I /Y /Q /EXCLUDE:%~dp0.install_exclude 2>nul
if errorlevel 1 (
    xcopy "%~dp0*" "%TARGET_DIR%\" /E /I /Y /Q
)

echo.
echo ==============================================================================
echo   [安装成功] 技能已就绪！
echo   安装路径：%TARGET_DIR%
echo.
echo   【使用指引】：
echo   1. 双击 start_chrome_cdp.bat 或 start_edge_cdp.bat 启动浏览器
echo   2. 打开 ChatGPT 网页端并进入你的会话
echo   3. 在 Windows 版 Antigravity 对话框中输入：/safari-chatgpt-reasoner 即可唤起！
echo ==============================================================================
echo.
pause
