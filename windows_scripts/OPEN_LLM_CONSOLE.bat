@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.1 - 本地大模型实时输出控制台 (Port 8930)
cd /d "%~dp0.."

echo ==============================================================================
echo   [AI] 成都建工 V3.1 - 本地大模型实时监控窗口
echo   模型架构: 星火 Spark-X2.5-4B (CPU AVX2)  ^|  端口: http://127.0.0.1:8930
echo   状态: 正在挂接日志输出流 (显示最近 50 行，实时刷新)...
echo ==============================================================================
echo.

if exist "logs\llm.log" (
    powershell -NoExit -Command "$host.UI.RawUI.WindowTitle = '成都建工 V3.1 - 本地大模型实时输出'; Get-Content -Wait -Tail 50 -Encoding UTF8 'logs\llm.log'"
) else (
    echo [提示] 尚未检测到后台日志，正在直接启动大模型控制台...
    call windows_scripts\01_START_LLM.bat
)
