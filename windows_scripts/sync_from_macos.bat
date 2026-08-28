@echo off
chcp 65001 >nul
echo ==============================================================================
echo  成都建工 V2.0 - Windows 本地一键从 macOS 分支同步最新功能、排版与样式
echo ==============================================================================

echo [1/3] 正在拉取远程最新分支与提交...
git fetch origin macos
if %errorlevel% neq 0 (
    echo [ERROR] 无法连接远程 GitHub 仓库，请检查网络与代理配置。
    pause
    exit /b %errorlevel%
)

echo [2/3] 正在将 macOS 分支的功能、前端排版、字体与样式合并到 Windows 分支...
git merge origin/macos --no-edit -m "sync: 从 macos 分支同步最新功能、UI排版与样式规范"
if %errorlevel% neq 0 (
    echo [INFO] 检测到平台特异性差异，自动优先采纳跨平台源码并保留本地 Windows 启动脚本...
    git checkout --theirs source_code/
    git checkout --ours windows_scripts/ START_WINDOWS.bat 一键启动_Windows.bat
    git add -A
    git commit -m "sync: 自动完成跨平台功能与排版合并（保留 Windows 启动配置）"
)

echo [3/3] 正在重新编译前端以生效最新排版与样式...
if exist "source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\frontend_stitch" (
    cd source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\frontend_stitch
    call npm run build
    call npm run sync:tax-static
    cd ..\..\..\..\..
)

echo ==================================================================
echo [SUCCESS] macOS 分支最新功能、排版、字体与样式已成功同步至 Windows 分支！
echo ==================================================================
pause
