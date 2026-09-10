# ============================================================
# 成都建工 V3.0 Windows 最小化部署包生成脚本
# 全部路径使用 ASCII 兼容的临时目录（避开 PowerShell GBK/UTF-8 编码问题）
# 目标环境：支持免安装便携版 PostgreSQL (54320) 或系统 PostgreSQL (5432)
# ============================================================
# 强制 PowerShell 输出 UTF-8，避免中文乱码
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 关键修复：PowerShell 默认按 GBK 解码脚本里的 UTF-8 中文，导致 Test-Path 失败
# 把所有中文路径字面量先按 GBK 字节解码再按 UTF-8 还原
function Fix-Path($p) {
    if ([string]::IsNullOrEmpty($p)) { return $p }
    $gbk = [System.Text.Encoding]::GetEncoding("GBK")
    $utf8 = [System.Text.Encoding]::UTF8
    return $utf8.GetString($gbk.GetBytes($p))
}

$RepoRoot = Fix-Path "F:\073_成都建工_V3.0_Windows"
$Ts = Get-Date -Format "yyyyMMdd_HHmmss"
# 全部使用 ASCII 路径（无中文）
$Stage = "C:\Temp\v3_stage"
$ZipTmp = "C:\Temp\v3_deploy.zip"

Write-Host "=== 成都建工 V3.0 Minimal Deploy Package ===" -ForegroundColor Cyan
Write-Host "Stage : $Stage"
Write-Host "Repo  : $RepoRoot"

# 清理
if (Test-Path $Stage)   { Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue }
if (Test-Path $ZipTmp)  { Remove-Item $ZipTmp -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Path $Stage -Force | Out-Null
New-Item -ItemType Directory -Path "C:\Temp" -Force | Out-Null

function Copy-Excluding($src, $dst, $skipDirs, $skipExts) {
    if (-not (Test-Path $src)) { return }
    New-Item -ItemType Directory -Path $dst -Force | Out-Null
    Get-ChildItem $src -Force -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.PSIsContainer) {
            if ($skipDirs -notcontains $_.Name) {
                Copy-Excluding $_.FullName (Join-Path $dst $_.Name) $skipDirs $skipExts
            }
        } else {
            if ($skipExts -notcontains $_.Extension.ToLower()) {
                Copy-Item $_.FullName (Join-Path $dst $_.Name) -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

# ----- 1. windows_scripts -----
Write-Host "[1/7] windows_scripts" -ForegroundColor Yellow
Copy-Excluding "$RepoRoot\windows_scripts" "$Stage\windows_scripts" @("bin","obj","publish") @()

# ----- 2. database -----
Write-Host "[2/7] database" -ForegroundColor Yellow
Copy-Excluding "$RepoRoot\database" "$Stage\database" @("backups","pgsql") @(".zip",".dump")

# ----- 3. 税务管理系统 -----
Write-Host "[3/7] tax-system" -ForegroundColor Yellow
$taxSrc = "$RepoRoot\source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0"
$taxDst = "$Stage\source_code\0.1_税务管理\chengdu_construction_tax_system_v1_0"
Copy-Excluding $taxSrc $taxDst @("node_modules","__pycache__",".venv",".pytest_cache",".ruff_cache") @()

# ----- 4. RAG 事实中台 -----
Write-Host "[4/7] rag-system" -ForegroundColor Yellow
$ragSrc = "$RepoRoot\source_code\0.2_RAG系统\project-rag-v1.1"
$ragDst = "$Stage\source_code\0.2_RAG系统\project-rag-v1.1"
# RAG 主目录（不含 data 和 logs）
Copy-Excluding $ragSrc $ragDst @("node_modules","__pycache__",".venv",".mineru-venv","data","logs",".pytest_cache",".ruff_cache") @()
# RAG data 子目录只排除 parsed 和 cache
$ragDataDst = "$ragDst\data"
Copy-Excluding "$ragSrc\data" $ragDataDst @("parsed","cache") @()

# ----- 5. 老板端天府掌舵 -----
Write-Host "[5/7] boss-app" -ForegroundColor Yellow
$bossSrc = "$RepoRoot\source_code\0.3_老板端安卓App_天府掌舵"
$bossDst = "$Stage\source_code\0.3_老板端安卓App_天府掌舵"
Copy-Excluding $bossSrc $bossDst @("node_modules","platform","android","ios",".svelte-kit","__pycache__",".venv") @()

# ----- 6. IDP 文档录入引擎 -----
Write-Host "[6/7] idp-engine" -ForegroundColor Yellow
Copy-Excluding "$RepoRoot\source_code\0.4_IDP文档录入引擎_V3.0" "$Stage\source_code\0.4_IDP文档录入引擎_V3.0" @() @()

# ----- 7. 桌面端、脚本与文档 -----
Write-Host "[7/7] desktop + scripts + docs" -ForegroundColor Yellow

# 桌面端控制台
Copy-Excluding "$RepoRoot\desktop_apps\windows" "$Stage\desktop_apps\windows" @("obj") @()
# 只保留 win-x64 publish exe
$baseBin = "$Stage\desktop_apps\windows\bin\Release\net8.0-windows"
if (Test-Path $baseBin) {
    Get-ChildItem $baseBin -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne "win-x64" } | ForEach-Object {
        Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# 状态栏托盘
Copy-Excluding "$RepoRoot\desktop_apps\windows_tray" "$Stage\desktop_apps\windows_tray" @() @()

# 根级控制台单文件可执行程序
$rootExe = Join-Path $RepoRoot "成都建工控制台.exe"
if (Test-Path $rootExe) {
    Copy-Item $rootExe "$Stage\成都建工控制台.exe" -Force
    Write-Host "Included root 成都建工控制台.exe" -ForegroundColor Green
}

# 根级 bat 入口与一键更新工具
@("START_WINDOWS.bat","START_TRAY_WINDOWS.bat","STOP_WINDOWS.bat",
  "一键托盘启动_Windows.bat","一键启动_Windows.bat","一键停止_Windows.bat",
  "00_一键应用最新覆盖更新.bat","00_一键启动全部服务.bat") | ForEach-Object {
    $src = Join-Path $RepoRoot $_
    if (Test-Path $src) { Copy-Item $src "$Stage\$_" -Force }
}

# 文档 (含纯 GBK 文本指南)
@("WINDOWS_DEPLOY_GUIDE.md","00_部署必读_Windows远程首次部署指引.md","00_部署必读_Windows远程首次部署指引.txt","README.md","AGENTS.md") | ForEach-Object {
    $src = Join-Path $RepoRoot $_
    if (Test-Path $src) { Copy-Item $src "$Stage\$_" -Force }
}

# ----- 打包（用 Windows 自带 bsdtar）-----
Write-Host "Compressing ..." -ForegroundColor Yellow
$TarExe = "C:\Windows\System32\tar.exe"
$ZipTmp = "C:\Temp\v3_deploy.zip"
if (Test-Path $ZipTmp) { Remove-Item $ZipTmp -Force -ErrorAction SilentlyContinue }

Push-Location $Stage
& $TarExe -a -cf $ZipTmp -C $Stage .
Pop-Location

if (-not (Test-Path $ZipTmp)) {
    throw "tar.exe failed to create archive"
}

# ----- 移动到目标位置 -----
$targets = @(
    "$RepoRoot\成都建工_V3.0_核心组件最新极速升级包.zip",
    "F:\成都建工_V3.0_核心组件最新极速升级包.zip",
    "F:\localsend\成都建工_V3.0_核心组件最新极速升级包.zip"
)

foreach ($target in $targets) {
    $parent = Split-Path -Parent $target
    if (Test-Path $parent) {
        Copy-Item $ZipTmp $target -Force
        Write-Host "Package saved: $target" -ForegroundColor Green
    }
}

Remove-Item $ZipTmp -Force -ErrorAction SilentlyContinue
Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue

$mainZip = "$RepoRoot\成都建工_V3.0_核心组件最新极速升级包.zip"
$size = (Get-Item $mainZip).Length
$sizeStr = if ($size -gt 1GB) { "{0:N1} GB" -f ($size/1GB) }
           elseif ($size -gt 1MB) { "{0:N1} MB" -f ($size/1MB) }
           else { "{0:N1} KB" -f ($size/1KB) }

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green
Write-Host "Output: $mainZip"
Write-Host "Size  : $sizeStr"
