@echo off
chcp 65001 >nul
title 成都建工 Cloudflare 远程安全穿透隧道 (Windows)
echo ==============================================================================
echo   🚀 正在启动 成都建工 Cloudflare 远程安全穿透隧道 (Windows)
echo ==============================================================================

where cloudflared >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [1/2] 未检测到 cloudflared.exe，正在自动下载官方客户端...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'cloudflared.exe'"
    set CLOUDFLARED_BIN=cloudflared.exe
) else (
    set CLOUDFLARED_BIN=cloudflared
)

echo [2/2] 正在创建公网安全隧道 (直连本地 8922 端口)...
echo.
echo ------------------------------------------------------------------------------
echo 💡 复制下方出现的 https://xxxx.trycloudflare.com 链接到手机 App 设置中即可！
echo ------------------------------------------------------------------------------
%CLOUDFLARED_BIN% tunnel --url http://127.0.0.1:8922
pause
