@echo off
@chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."

echo ==============================================================================
echo   🏗️ 正在编译 成都建工 V3.0 Windows 托盘控制中心 (Native WinExe)...
echo ==============================================================================

set "CSC_EXE=C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC_EXE%" (
    set "CSC_EXE=C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)

if not exist "%CSC_EXE%" (
    echo [错误] 未找到 Windows 内置 C# 编译器 csc.exe！
    pause
    exit /b 1
)

"%CSC_EXE%" /target:winexe /win32icon:"desktop_apps\windows\app.ico" /out:"desktop_apps\windows_tray\ChengduConstructionTray.exe" /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.dll /optimize+ "desktop_apps\windows_tray\ChengduConstructionTray.cs"

if errorlevel 1 (
    echo [错误] 编译失败！
    exit /b 2
)

copy /y "desktop_apps\windows_tray\ChengduConstructionTray.exe" "desktop_apps\windows\ChengduConstructionTray.exe" >nul
copy /y "desktop_apps\windows_tray\ChengduConstructionTray.exe" "成都建工V3.0托盘控制台.exe" >nul
copy /y "desktop_apps\windows_tray\ChengduConstructionTray.exe" "ChengduConstructionTray.exe" >nul

echo [成功] 托盘控制台编译就绪:
echo        - 成都建工V3.0托盘控制台.exe
echo ==============================================================================
exit /b 0
