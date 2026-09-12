@echo off
chcp 65001 >nul
title 环境检查 — Chrome ChatGPT Bridge
echo ===========================================================
echo  检查 Chrome ChatGPT Bridge (Node.js) 运行环境
echo ===========================================================
echo.

set "ALL_OK=1"

:: ── 1. Node.js ──────────────────────────────────────────────
echo [1/3] 检查 Node.js...
node --version >nul 2>&1
if %errorlevel%==0 (
    for /f "tokens=*" %%v in ('node --version') do echo     ✅ Node.js %%v 已安装
) else (
    echo     ❌ Node.js 未找到！
    echo        请访问 https://nodejs.org 下载安装 LTS 版本
    echo        或执行：winget install OpenJS.NodeJS.LTS
    set "ALL_OK=0"
)
echo.

:: ── 2. Chrome ───────────────────────────────────────────────
echo [2/3] 检查 Google Chrome...
set "CHROME_FOUND=0"
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe"      set "CHROME_FOUND=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe"      set "CHROME_FOUND=1"
if "%CHROME_FOUND%"=="1" (
    echo     ✅ Google Chrome 已安装
) else (
    echo     ⚠  Google Chrome 未找到（可改用 Edge，已内置于 Windows 11）
)
echo.

:: ── 3. Microsoft Edge ───────────────────────────────────────
echo [3/3] 检查 Microsoft Edge...
set "EDGE_FOUND=0"
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"      set "EDGE_FOUND=1"
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "EDGE_FOUND=1"
if "%EDGE_FOUND%"=="1" (
    echo     ✅ Microsoft Edge 已安装
) else (
    echo     ⚠  Microsoft Edge 未找到
)
echo.

:: ── 结果 ────────────────────────────────────────────────────
echo ===========================================================
if "%ALL_OK%"=="1" (
    echo  ✅ 环境就绪！
    echo.
    echo  下一步：
    echo    1. 双击 start_chrome_cdp.bat 或 start_edge_cdp.bat 启动浏览器
    echo    2. 在浏览器中打开 ChatGPT 并进入目标会话
    echo    3. 运行命令：
    echo       node scripts\chrome_chatgpt.js --target-url "https://chatgpt.com/c/..." --prompt "你好"
) else (
    echo  ❌ 存在未满足的依赖，请按上方提示安装后重新检查
)
echo ===========================================================
echo.
pause
