@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title Antigravity2gptweb Windows 环境自检

echo ===========================================================
echo  Antigravity2gptweb Windows 环境自检
echo  默认：BrowserSkill (bsk)；回退：Node.js CDP
echo ===========================================================
echo.

set "BSK_FOUND=0"
set "BSK_CONNECTED=0"
set "PYTHON_FOUND=0"
set "NODE_FOUND=0"
set "BROWSER_FOUND=0"

echo [1/5] 检查 BrowserSkill CLI...
where bsk.exe >nul 2>&1
if errorlevel 1 where bsk >nul 2>&1
if not errorlevel 1 (
    set "BSK_FOUND=1"
    for /f "tokens=*" %%v in ('bsk --version 2^>nul') do echo     [OK] %%v
) else (
    echo     [WARN] 未找到 bsk.exe
)
echo.

echo [2/5] 检查 BrowserSkill daemon / 扩展连通性...
if "%BSK_FOUND%"=="1" (
    bsk status --json > "%TEMP%\antigravity_bsk_status.json" 2> "%TEMP%\antigravity_bsk_status.err"
    if not errorlevel 1 (
        findstr /i /c:"instance_id" /c:"browser_name" "%TEMP%\antigravity_bsk_status.json" >nul 2>&1
        if not errorlevel 1 (
            set "BSK_CONNECTED=1"
            echo     [OK] bsk daemon 正常，已检测到浏览器扩展连接
        ) else (
            echo     [WARN] bsk daemon 可访问，但当前没有 Edge/Chrome 扩展实例连接
            echo            请启动日常 Edge/Chrome 并确认 BrowserSkill 扩展已启用
        )
    ) else (
        echo     [WARN] bsk status 失败
        type "%TEMP%\antigravity_bsk_status.err"
    )
) else (
    echo     [SKIP] 未安装 bsk
)
echo.

echo [3/5] 检查 Python...
where py >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_FOUND=1"
    for /f "tokens=*" %%v in ('py -3 --version 2^>^&1') do echo     [OK] %%v
) else (
    where python >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_FOUND=1"
        for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo     [OK] %%v
    ) else (
        echo     [WARN] 未找到 Python 3
    )
)
echo.

echo [4/5] 检查 Node.js（CDP 回退通道）...
where node >nul 2>&1
if not errorlevel 1 (
    set "NODE_FOUND=1"
    for /f "tokens=*" %%v in ('node --version') do echo     [OK] Node.js %%v
) else (
    echo     [INFO] 未找到 Node.js；若 bsk 主通道正常可忽略
)
echo.

echo [5/5] 检查 Edge / Chrome...
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "BROWSER_FOUND=1"
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "BROWSER_FOUND=1"
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "BROWSER_FOUND=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "BROWSER_FOUND=1"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "BROWSER_FOUND=1"
if "%BROWSER_FOUND%"=="1" (
    echo     [OK] 已检测到 Edge 或 Chrome
) else (
    echo     [WARN] 未在常见安装路径检测到 Edge/Chrome
)
echo.

echo ===========================================================
if "%BSK_FOUND%"=="1" if "%BSK_CONNECTED%"=="1" if "%PYTHON_FOUND%"=="1" (
    echo  [READY] BrowserSkill 主通道已就绪
    echo.
    echo  无需 --remote-debugging-port。保持日常 Edge/Chrome 打开目标
    echo  ChatGPT 会话，然后运行：
    echo    py -3 scripts\bsk_chatgpt.py --target-url "https://chatgpt.com/c/..." --prompt "你好"
    goto :done
)

if "%NODE_FOUND%"=="1" (
    echo  [FALLBACK] BrowserSkill 主通道尚未完全就绪，但 Node.js CDP 可回退
    echo  回退时才需要 start_edge_cdp.bat / start_chrome_cdp.bat
    goto :done
)

echo  [NOT READY] 当前既没有完整 bsk 主通道，也没有 Node.js CDP 回退
echo  请根据上面的 WARN 补齐环境。

:done
echo ===========================================================
del /q "%TEMP%\antigravity_bsk_status.json" >nul 2>&1
del /q "%TEMP%\antigravity_bsk_status.err" >nul 2>&1
echo.
endlocal
