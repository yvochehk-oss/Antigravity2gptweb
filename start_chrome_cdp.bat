@echo off
chcp 65001 >nul
title 启动 Google Chrome (CDP 原生调试模式 9222)
echo ===========================================================
echo  正在启动 Google Chrome (原生 CDP 9222 远程调试模式)...
echo  特性：0 依赖、0 浏览器扩展、独立环境不冲突、登录态持久化
echo ===========================================================
echo.

:: ── 1. 检查 9222 端口是否已被占用 ─────────────────────────────
netstat -ano | findstr /R /C:":9222 " >nul 2>&1
if %errorlevel% equ 0 (
    echo [提示] 9222 调试端口已在监听中！
    echo        如果之前已打开过调试浏览器，可直接运行 bridge 脚本。
    echo.
)

:: ── 2. 创建持久化用户数据目录（确保登录态保存且不与日常 Chrome 冲突）──
set "DATA_DIR=%LOCALAPPDATA%\Antigravity2gptweb\chrome_cdp_profile"
if not exist "%DATA_DIR%" (
    mkdir "%DATA_DIR%" >nul 2>&1
)

:: ── 3. 查找 Google Chrome 路径 ──────────────────────────────
set "CHROME_EXE="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if "%CHROME_EXE%"=="" if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if "%CHROME_EXE%"=="" if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%LocalAppData%\Google\Chrome\Application\chrome.exe"

:: ── 4. 启动 Chrome ───────────────────────────────────────────
set "CHROME_CMD=chrome.exe"
if not "%CHROME_EXE%"=="" set "CHROME_CMD=%CHROME_EXE%"

echo 正在拉起 Chrome 进程...
start "" "%CHROME_CMD%" --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir="%DATA_DIR%" --no-first-run --no-default-browser-check "https://chatgpt.com"

:: ── 5. 校验端口是否成功监听 ──────────────────────────────────
timeout /t 2 /nobreak >nul 2>&1
netstat -ano | findstr /R /C:":9222 " >nul 2>&1
if %errorlevel% equ 0 (
    echo ===========================================================
    echo  [✓ 成功] Chrome 原生 CDP 调试端口 (9222) 已成功启动！
    echo ===========================================================
) else (
    echo [提示] Chrome 已发送启动指令，请在弹出的窗口中确认。
)

echo.
echo 【使用说明】：
echo  1. 请在弹出的 Chrome 浏览器窗口中登录您的 ChatGPT 账号并进入目标会话。
echo  2. 打开新的 CMD 命令行窗口，直接运行原生驱动（无需 bsk，0 npm 依赖）：
echo.
echo     node scripts\chrome_chatgpt.js --browser-name chrome --target-url "https://chatgpt.com/c/你的会话ID" --prompt "需求描述"
echo.
echo  或者在任务编排器中直接指定 --browser chrome：
echo     python scripts\orchestrate.py init --browser chrome --target-url "https://chatgpt.com/c/你的会话ID" ...
echo ===========================================================
echo 按任意键关闭此窗口（Chrome 会在后台保持运行）...
pause >nul
