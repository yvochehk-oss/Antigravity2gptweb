@echo off
chcp 65001 >nul
title 启动 Microsoft Edge (CDP 9222)
echo ===========================================================
echo  正在启动 Microsoft Edge，开启 CDP 9222 远程调试端口...
echo  说明：Edge 内置于 Windows 10/11，无需额外安装！
echo ===========================================================
echo.

node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [警告] Node.js 未找到！bridge 脚本将无法运行。
    echo        请先运行 check_env_windows.bat 查看安装指引。
    echo.
)

set "EDGE_PATH="
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"      set "EDGE_PATH=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "EDGE_PATH=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"

if "%EDGE_PATH%"=="" (
    start "" msedge.exe --remote-debugging-port=9222 --remote-allow-origins=* "https://chatgpt.com"
) else (
    start "" "%EDGE_PATH%" --remote-debugging-port=9222 --remote-allow-origins=* "https://chatgpt.com"
)

echo [就绪] Edge 已在 9222 端口启动
echo.
echo 使用方法（在另一个 cmd 窗口运行）：
echo   node scripts\chrome_chatgpt.js ^
echo        --browser-name edge ^
echo        --target-url "https://chatgpt.com/c/你的会话ID" ^
echo        --prompt "你的问题"
echo.
