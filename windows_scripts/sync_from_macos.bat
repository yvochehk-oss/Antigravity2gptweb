@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0.."

echo ==============================================================================
echo  成都建工 V3.0 - Windows 本地统一主体代码同步
echo  main / macos / windows 为同一主体代码的三个镜像引用
echo ==============================================================================

echo [1/4] 检查本地工作区...
set "DIRTY="
for /f "delims=" %%S in ('git status --porcelain') do set "DIRTY=1"
if defined DIRTY (
    echo [ERROR] 工作区存在未提交修改。请先提交或暂存，禁止自动覆盖本地工作。
    pause
    exit /b 1
)

echo [2/4] 拉取 main / macos / windows 镜像...
git fetch origin main macos windows --prune
if errorlevel 1 (
    echo [ERROR] 无法拉取远程仓库，请检查 GitHub 网络与凭据。
    pause
    exit /b 2
)

for /f %%S in ('git rev-parse origin/main') do set "MAIN_SHA=%%S"
for /f %%S in ('git rev-parse origin/macos') do set "MACOS_SHA=%%S"
for /f %%S in ('git rev-parse origin/windows') do set "WINDOWS_SHA=%%S"

if not "!MAIN_SHA!"=="!MACOS_SHA!" goto :remote_diverged
if not "!MAIN_SHA!"=="!WINDOWS_SHA!" goto :remote_diverged

echo [3/4] 快进本地 Windows 分支到统一主体代码...
git show-ref --verify --quiet refs/heads/windows
if errorlevel 1 (
    git switch --track -c windows origin/windows
) else (
    git switch windows
)
if errorlevel 1 goto :git_failed

git merge --ff-only origin/windows
if errorlevel 1 (
    echo [ERROR] 本地 Windows 分支与远程发生分叉，已停止；不会自动解冲突或覆盖文件。
    pause
    exit /b 3
)

echo [4/4] 前端由独立 Vite 服务运行，不生成或同步 Tax 后端静态资源。

echo ==============================================================================
echo [SUCCESS] Windows 已同步到统一主体提交：!MAIN_SHA!
echo [SUCCESS] main / macos / windows 远程代码一致。
echo ==============================================================================
pause
exit /b 0

:remote_diverged
echo [ERROR] 远程三个主体分支尚未镜像到同一提交，停止本地同步。
echo main    = !MAIN_SHA!
echo macos   = !MACOS_SHA!
echo windows = !WINDOWS_SHA!
echo 请先检查 GitHub 的 Canonical Branch Mirror 工作流；禁止本地自动解冲突。
pause
exit /b 6

:git_failed
echo [ERROR] Git 分支切换失败。
pause
exit /b 7
