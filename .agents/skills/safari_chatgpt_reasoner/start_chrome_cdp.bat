@echo off
chcp 65001 >nul
title 启动 Google Chrome (CDP 9222)
echo ===========================================================
echo  正在启动 Google Chrome，开启 CDP 9222 远程调试端口...
echo  说明：请在弹出的浏览器中登录 ChatGPT 并打开目标会话
echo ===========================================================
echo.

:: ── 检查 Node.js 是否可用 ──────────────────────────────────
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [警告] Node.js 未找到！bridge 脚本将无法运行。
    echo        请先运行 check_env_windows.bat 查看安装指引。
    echo.
)

:: ── 查找并启动 Chrome ──────────────────────────────────────
set "CHROME_PATH="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe"      set "CHROME_PATH=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_PATH=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe"       set "CHROME_PATH=%LocalAppData%\Google\Chrome\Application\chrome.exe"

if "%CHROME_PATH%"=="" (
    start "" chrome.exe --remote-debugging-port=9222 --remote-allow-origins=* "https://chatgpt.com"
) else (
    start "" "%CHROME_PATH%" --remote-debugging-port=9222 --remote-allow-origins=* "https://chatgpt.com"
)

echo [就绪] Chrome 已在 9222 端口启动
echo.
echo 使用方法（在另一个 cmd 窗口运行）：
echo   node scripts\chrome_chatgpt.js ^
echo        --target-url "https://chatgpt.com/c/你的会话ID" ^
echo        --prompt "你的问题"
echo.
