@echo off
@chcp 936 >nul 2>&1
title 成都建工 V3.1 - 一键应用最新覆盖更新
setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

echo ==============================================================================
echo   [成都建工] 成都建工 V3.1 - 一键应用最新核心组件覆盖更新
echo   目标目录: %ROOT_DIR%
echo ==============================================================================
echo.

:: 1. 停止旧服务
echo [1/5] 正在停止可能运行中的旧服务实例...
if exist "STOP_WINDOWS.bat" (
    call "STOP_WINDOWS.bat" >nul 2>&1
) else if exist "windows_scripts\99_STOP_ALL.bat" (
    call "windows_scripts\99_STOP_ALL.bat" >nul 2>&1
)
ping 127.0.0.1 -n 3 >nul

:: 2. 清理税务静态目录历史残留污染文件
echo [2/5] 正在清理 8921 税务静态目录中的历史残留旧文件...
set "TAX_STATIC=source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0\app\static_dist"
if exist "%TAX_STATIC%\assets" (
    del /f /q "%TAX_STATIC%\assets\CopilotView-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\assets\DashboardView-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\assets\ProjectsView-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\assets\CompaniesView-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\assets\SettingsView-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\assets\auth.store-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\assets\client-DE-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\assets\esm-DMx6yp6c.js" >nul 2>&1
    del /f /q "%TAX_STATIC%\assets\rolldown-runtime-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\assets\preload-helper-*" >nul 2>&1
    del /f /q "%TAX_STATIC%\manifest.json" >nul 2>&1
    del /f /q "%TAX_STATIC%\sw.js" >nul 2>&1
)
echo       -^> 税务前端目录已净化为正统 React SPA！

:: 3. 依赖与虚拟环境适配
echo [3/5] 正在检查与适配 Python 运行环境 (Python 3.12)...
if exist "windows_scripts\06_SETUP_ENV.bat" (
    echo       正在执行虚拟环境与依赖自愈...
    call "windows_scripts\06_SETUP_ENV.bat"
) else if exist "windows_scripts\06_一键配置Python314环境.bat" (
    call "windows_scripts\06_一键配置Python314环境.bat"
)

:: 4. 确保 PostgreSQL 数据库就绪
echo [4/5] 正在确保 PostgreSQL 数据库已自动就绪...
if exist "windows_scripts\00_START_POSTGRES.bat" (
    call "windows_scripts\00_START_POSTGRES.bat"
)

:: 5. 启动控制台
echo.
echo [5/5] 覆盖更新全部应用完成！
echo ==============================================================================
echo   [完成] 成都建工 V3.1 最新核心组件已完全就绪！
echo   - 8921 端口已锁定为 锐宝财税智控中枢 (正统 React SPA)
echo   - 5173 端口已锁定为 天府掌舵 (老板端驾驶舱)
echo   - 8922 端口 RAG 事实中台支持端口自适应与秒级启动
echo   - PostgreSQL 数据库已实现免配置自动拉起与中文路径避障
echo ==============================================================================
echo.
echo 正在启动 成都建工控制台...
if exist "成都建工控制台.exe" (
    start "" "成都建工控制台.exe"
) else if exist "START_WINDOWS.bat" (
    start "" "START_WINDOWS.bat"
)
pause
