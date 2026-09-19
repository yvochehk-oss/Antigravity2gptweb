@echo off
chcp 65001 >nul
title 环境检查 — Antigravity2gptweb Windows 环境自检
echo ===========================================================
echo  Antigravity2gptweb (Windows) 运行环境全方位自检
echo ===========================================================
echo.

set "BSK_FOUND=0"
set "PYTHON_FOUND=0"
set "NODE_FOUND=0"

:: ── 1. Browser-Skill (bsk CLI) ──────────────────────────────
echo [1/4] 检查 Browser-Skill (bsk CLI，推荐引擎)...
bsk --version >nul 2>&1
if %errorlevel%==0 (
    for /f "tokens=*" %%v in ('bsk --version') do echo     ✅ bsk CLI 已就绪 (%%v)
    set "BSK_FOUND=1"
) else (
    echo     ⚠  bsk CLI 未找到（若需免调试端口直连日常浏览器，请安装 bsk）
)
echo.

:: ── 2. Python 运行环境 ─────────────────────────────────────
echo [2/4] 检查 Python 运行环境...
python --version >nul 2>&1
if %errorlevel%==0 (
    for /f "tokens=*" %%v in ('python --version') do echo     ✅ Python %%v 已就绪
    set "PYTHON_FOUND=1"
) else (
    echo     ⚠  Python 未找到
)
echo.

:: ── 3. Node.js 运行环境 (CDP 备用引擎) ──────────────────────
echo [3/4] 检查 Node.js 运行环境 (CDP 备选)...
node --version >nul 2>&1
if %errorlevel%==0 (
    for /f "tokens=*" %%v in ('node --version') do echo     ✅ Node.js %%v 已就绪 (0 npm 外部依赖)
    set "NODE_FOUND=1"
) else (
    echo     ⚠  Node.js 未找到
)
echo.

:: ── 4. 浏览器检测 ──────────────────────────────────────────
echo [4/4] 检查可用浏览器...
set "EDGE_FOUND=0"
set "CHROME_FOUND=0"

if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"      set "EDGE_FOUND=1"
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "EDGE_FOUND=1"
if "%EDGE_FOUND%"=="1" (
    echo     ✅ Microsoft Edge 已就绪 (Windows 内置)
)

if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe"      set "CHROME_FOUND=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe"      set "CHROME_FOUND=1"
if "%CHROME_FOUND%"=="1" (
    echo     ✅ Google Chrome 已就绪
)
echo.

:: ── 诊断与建议 ──────────────────────────────────────────────
echo ===========================================================
if "%BSK_FOUND%"=="1" (
    echo  🌟 【推荐】已检测到 Browser-Skill (bsk) 引擎！
    echo     无需启动任何调试端口或杀掉浏览器，直接使用日常已登录 Edge/Chrome 即可：
    echo.
    echo     标准运行命令：
    echo       python scripts\bsk_chatgpt.py --target-url "https://chatgpt.com/c/..." --prompt "任务需求"
    echo.
) else (
    if "%NODE_FOUND%"=="1" (
        echo  ⚡ 【备选】已检测到 Node.js CDP 引擎：
        echo     1. 双击运行 start_edge_cdp.bat 或 start_chrome_cdp.bat 启动端口调试
        echo     2. 运行命令：
        echo        node scripts\chrome_chatgpt.js --target-url "https://chatgpt.com/c/..." --prompt "任务需求"
        echo.
    ) else (
        echo  ❌ 未检测到可用的执行环境！
        echo     建议方案 A (推荐)：安装 bsk 与 Python (支持日常浏览器免端口直连)
        echo     建议方案 B (极简)：安装 Node.js LTS (通过 start_edge_cdp.bat 调试运行)
        echo.
    )
)
echo ===========================================================
echo.
pause
