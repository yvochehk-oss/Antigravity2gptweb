@echo off
@chcp 936 >nul 2>&1
title 卸载/停用本机系统版 PostgreSQL (5432)
echo ==============================================================================
echo   正在停用并清理本机的 Windows 系统安装版 PostgreSQL 16 (端口 5432)
echo   说明: 本操作仅清理系统版，不会影响 F 盘正牌绿色便携版 (54320)
echo ==============================================================================

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [提示] 正在请求管理员权限，请在弹出的窗口中点击“是”...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo [1/3] 正在强制停止系统服务 postgresql-x64-16...
net stop postgresql-x64-16 >nul 2>&1
sc stop postgresql-x64-16 >nul 2>&1

echo [2/3] 正在禁用并删除系统服务注册...
sc config postgresql-x64-16 start= disabled >nul 2>&1
sc delete postgresql-x64-16 >nul 2>&1

echo [3/3] 检查 5432 端口是否已完全释放...
netstat -ano | findstr ":5432 " >nul 2>&1
if %errorLevel% equ 0 (
    echo [警告] 端口 5432 仍有残留进程，正在清理残留 postgres.exe 进程...
    taskkill /F /IM postgres.exe >nul 2>&1
) else (
    echo [成功] 端口 5432 已彻底释放！
)

echo.
if exist "C:\Program Files\PostgreSQL\16\uninstall-postgresql.exe" (
    echo [提示] 官方卸载器路径: "C:\Program Files\PostgreSQL\16\uninstall-postgresql.exe"
    echo 如果您想彻底删除 C 盘的安装文件，可回车启动官方卸载器；如果仅需解除端口冲突，直接关闭此窗口即可。
    choice /c YN /m "是否立即启动官方卸载器彻底清理文件？[Y/N]"
    if errorlevel 2 goto done
    start "" "C:\Program Files\PostgreSQL\16\uninstall-postgresql.exe"
)

:done
echo.
echo ==============================================================================
echo [完成] 本机系统版 PostgreSQL 5432 已彻底移除！
echo 现在全系统将 100%% 独占使用 F 盘绿色便携版 (54320)。
echo ==============================================================================
pause
