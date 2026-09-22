@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title Antigravity2gptweb Windows 环境自检

echo ===========================================================
echo  Antigravity2gptweb Windows 原生运行环境自检
echo  架构：纯原生 Node.js CDP (0 npm 依赖 · 0 浏览器扩展)
echo ===========================================================
echo.

set "NODE_FOUND=0"
set "EDGE_FOUND=0"
set "CHROME_FOUND=0"
set "PORT_9222_OPEN=0"

echo [1/4] 检查 Node.js 运行时 (需要 Node >= 18)...
where node >nul 2>&1
if not errorlevel 1 (
    set "NODE_FOUND=1"
    for /f "tokens=*" %%v in ('node --version 2^>nul') do echo     [✓ OK] 已安装 Node.js %%v
) else (
    echo     [✗ 警告] 未找到 Node.js！
    echo             请前往 https://nodejs.org 下载安装 Node.js (推荐 LTS 版本)。
)
echo.

echo [2/4] 检查原生浏览器 (Edge / Chrome)...
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "EDGE_FOUND=1"
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "EDGE_FOUND=1"
if exist "%LocalAppData%\Microsoft\Edge\Application\msedge.exe" set "EDGE_FOUND=1"

if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"

if "%EDGE_FOUND%"=="1" (
    echo     [✓ OK] 检测到系统内置 Microsoft Edge (推荐使用)
)
if "%CHROME_FOUND%"=="1" (
    echo     [✓ OK] 检测到 Google Chrome
)
if "%EDGE_FOUND%"=="0" if "%CHROME_FOUND%"=="0" (
    echo     [✗ 警告] 未在默认路径检测到 Edge 或 Chrome，请确认浏览器已安装。
)
echo.

echo [3/4] 检查 CDP 9222 远程调试端口...
netstat -ano | findstr /R /C:":9222 " >nul 2>&1
if %errorlevel% equ 0 (
    set "PORT_9222_OPEN=1"
    echo     [✓ OK] 9222 调试端口正在监听，原生 CDP 桥接已可直接连接！
) else (
    echo     [提示] 9222 调试端口当前未启动。
    echo            使用前请双击运行 start_edge_cdp.bat 或 start_chrome_cdp.bat 即可一键拉起！
)
echo.

echo [4/4] 检查 Python 运行环境 (可选，供高级编排器使用)...
where py >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%v in ('py -3 --version 2^>^&1') do echo     [✓ 可选] 已安装 Python (%%v)
) else (
    where python >nul 2>&1
    if not errorlevel 1 (
        for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo     [✓ 可选] 已安装 Python (%%v)
    ) else (
        echo     [提示] 未检测到 Python（普通 Node.js 原生模式无需 Python，不影响使用）
    )
)
echo.

echo ===========================================================
if "%NODE_FOUND%"=="1" (
    if "%PORT_9222_OPEN%"=="1" (
        echo  [状态：已就绪] Windows 原生环境已完全就绪！
        echo  可直接在命令行运行测试：
        echo    node scripts\chrome_chatgpt.js --target-url "https://chatgpt.com/c/你的会话ID" --prompt "你好"
    ) else (
        echo  [状态：基础就绪] Node.js 与浏览器已检测通过！
        echo  下一步：
        echo  1. 双击运行 start_edge_cdp.bat 启动调试浏览器；
        echo  2. 登录 ChatGPT 账号并打开目标对话；
        echo  3. 即可开始使用桌面 Agent 发送指令并自动化闭环！
    )
) else (
    echo  [状态：缺少依赖] 请先安装 Node.js (https://nodejs.org) 后重试。
)
echo ===========================================================
echo.
pause
