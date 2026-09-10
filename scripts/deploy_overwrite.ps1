# ============================================================
# 成都建工 V3.0 Windows 部署覆盖脚本
# 用途：解压新部署包覆盖现有部署（保留数据库备份和模型目录）
# 用法：
#   .\deploy_overwrite.ps1 -ZipPath "D:\成都建工V3.0_最小化部署包_xxx.zip"
#   .\deploy_overwrite.ps1 -ZipPath "D:\xxx.zip" -TargetDir "D:\ChengDuJianGong" -KeepData
# ============================================================
# 强制 UTF-8 输出
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

param(
    [Parameter(Mandatory=$true)] [string]$ZipPath,
    [string]$TargetDir = "F:\073_成都建工_V3.0_Windows",
    [switch]$KeepData,    # 保留 database/backups 和 RAG data/parsed
    [switch]$KeepModels   # 保留已下载的模型
)

$ErrorActionPreference = "Stop"

Write-Host "=== 成都建工 V3.0 部署覆盖 ===" -ForegroundColor Cyan
Write-Host "Zip       : $ZipPath"
Write-Host "Target    : $TargetDir"
Write-Host "KeepData  : $KeepData"
Write-Host "KeepModels: $KeepModels"
Write-Host ""

# 校验 ZIP
if (-not (Test-Path $ZipPath)) {
    Write-Host "ERROR: ZIP not found - $ZipPath" -ForegroundColor Red
    exit 1
}

# 准备临时解压目录（ASCII 路径避开编码）
$ExtractTmp = "C:\Temp\v3_extract_$PID"
if (Test-Path $ExtractTmp) { Remove-Item $ExtractTmp -Recurse -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Path $ExtractTmp -Force | Out-Null

# ----- 解压 -----
Write-Host "[1/5] Extracting ..." -ForegroundColor Yellow
$TarExe = "C:\Windows\System32\tar.exe"
& $TarExe -xf $ZipPath -C $ExtractTmp
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: tar extract failed (exit=$LASTEXITCODE)" -ForegroundColor Red
    exit 2
}

# ----- 备份要保留的目录 -----
$BackupRoot = "C:\Temp\v3_backup_$PID"
New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null

if ($KeepData -and (Test-Path "$TargetDir\database\backups")) {
    Write-Host "[2/5] Backup database\backups ..." -ForegroundColor Yellow
    Copy-Item "$TargetDir\database\backups" "$BackupRoot\database_backups" -Recurse -Force
}
if ($KeepData -and (Test-Path "$TargetDir\source_code\0.2_RAG系统\project-rag-v1.1\data")) {
    Write-Host "[2/5] Backup RAG data ..." -ForegroundColor Yellow
    Copy-Item "$TargetDir\source_code\0.2_RAG系统\project-rag-v1.1\data" "$BackupRoot\rag_data" -Recurse -Force
}
if ($KeepModels -and (Test-Path "$TargetDir\models")) {
    Write-Host "[2/5] Backup models ..." -ForegroundColor Yellow
    Copy-Item "$TargetDir\models" "$BackupRoot\models" -Recurse -Force
}

# ----- 清理目标目录（按白名单保留可选目录） -----
Write-Host "[3/5] Cleaning target ..." -ForegroundColor Yellow
if (Test-Path $TargetDir) {
    # 默认保留：models（已下载的 LLM 权重）、database\backups（历史数据库备份）
    Get-ChildItem $TargetDir -Force | ForEach-Object {
        $keep = $false
        if ($_.Name -eq "models" -and $KeepModels) { $keep = $true }
        if ($_.Name -eq "database" -and $KeepData) {
            # 仅保留 database\backups 子目录，删除 database 下其他内容
            if (Test-Path "$TargetDir\database\backups") {
                Copy-Item "$TargetDir\database\backups" "$BackupRoot\db_backups_inside" -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        if (-not $keep) {
            Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        } else {
            # 仅清空非保留子目录
            if ($_.Name -eq "models") {
                # 保留整个 models 目录
            } elseif ($_.Name -eq "database") {
                # 删除 database 下除 backups 之外的所有内容
                Get-ChildItem "$TargetDir\database" -Force | Where-Object { $_.Name -ne "backups" } | ForEach-Object {
                    Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
                }
            }
        }
    }
} else {
    New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
}

# ----- 复制新内容 -----
Write-Host "[4/5] Copying new files ..." -ForegroundColor Yellow
# 使用 robocopy 镜像覆盖
$robocopyArgs = @(
    $ExtractTmp,
    $TargetDir,
    "/E",        # 包括空目录
    "/IS",       # 包含相同文件
    "/IT",       # 包含调整过的文件
    "/NFL",      # 无文件日志（减少噪音）
    "/NDL",      # 无目录日志
    "/NP",       # 无进度
    "/R:3",
    "/W:5"
)
$robocopy = Start-Process -FilePath "robocopy.exe" -ArgumentList $robocopyArgs -Wait -PassThru -NoNewWindow
# robocopy exit: 0=无变化, 1=复制成功, 2=额外操作, 3+ = 错误
if ($robocopy.ExitCode -ge 8) {
    Write-Host "ERROR: robocopy failed (exit=$($robocopy.ExitCode))" -ForegroundColor Red
    exit 3
}

# ----- 恢复保留目录 -----
Write-Host "[5/5] Restoring kept data ..." -ForegroundColor Yellow
if (Test-Path "$BackupRoot\db_backups_inside") {
    New-Item -ItemType Directory -Path "$TargetDir\database\backups" -Force | Out-Null
    Get-ChildItem "$BackupRoot\db_backups_inside" -Force | ForEach-Object {
        Copy-Item $_.FullName "$TargetDir\database\backups\$($_.Name)" -Recurse -Force
    }
}
if (Test-Path "$BackupRoot\rag_data") {
    $ragData = "$TargetDir\source_code\0.2_RAG系统\project-rag-v1.1\data"
    if (Test-Path $ragData) {
        Get-ChildItem $BackupRoot\rag_data -Force | ForEach-Object {
            Copy-Item $_.FullName "$ragData\$($_.Name)" -Recurse -Force
        }
    }
}

# 清理
Remove-Item $ExtractTmp -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $BackupRoot -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=== Deploy Complete ===" -ForegroundColor Green
Write-Host "Target : $TargetDir"
Write-Host "Next   : Run windows_scripts\00_START_ALL.bat to start"
