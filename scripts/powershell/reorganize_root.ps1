# reorganize_root.ps1
# 整理根目录：文档归 docs/，脚本归 scripts/，macOS 文件归 scripts/macos/，运行时产物归 runtime/
# 操作：只移动，不删除。根目录始终从脚本自身位置解析，禁止绑定开发机盘符。

$ErrorActionPreference = 'Continue'
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Move-IfExists($src, $dest) {
    $s = Join-Path $root $src
    $d = Join-Path $root $dest
    if (Test-Path $s) {
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
        Move-Item $s $d -Force
        Write-Host "[MOVE] $src  ->  $dest"
    }
}

Write-Host "=== Step 1: 创建子目录 ==="
$subdirs = @("scripts\macos", "scripts\powershell", "scripts\python")
foreach ($dir in $subdirs) {
    $p = Join-Path $root $dir
    New-Item -ItemType Directory -Path $p -Force | Out-Null
    Write-Host "[DIR]  $dir"
}

Write-Host ""
Write-Host "=== Step 2: 移动文档 .md 到 docs/ ==="
$docs = @(
    "AGENTS.md", "AI_FINAL_OPERATIONS_GUIDE_V2.0.md", "BRANCH_SYNC.md",
    "UI_UNIFY_ACTION_PLAN.md", "V3_UI_UNIFY_ACTION_PLAN_v2.0.md", "WINDOWS_DEPLOY_GUIDE.md"
)
foreach ($f in $docs) { Move-IfExists $f "docs\$f" }

Write-Host ""
Write-Host "=== Step 3: 移动 .env.example 到 scripts/ ==="
Move-IfExists ".env.example" "scripts\.env.example"

Write-Host ""
Write-Host "=== Step 4: 移动 Windows 脚本 ==="
$win_scripts = @(
    "00_打开大模型控制台后台.bat", "01_启动大模型后台.bat", "02_启动RAG实况台.bat",
    "03_启动税务系统后台.bat", "04_启动前端Web后台.bat", "05_修改缺少DLL问题.bat",
    "05_下载AI模型_迅雷下载.bat", "06_安装Python314环境.bat", "07_恢复原PostgreSQL数据库.bat",
    "99_停止全部.bat", "05_DOWNLOAD_MODELS.bat", "06_SETUP_ENV.bat", "07_RESTORE_DB.bat",
    "00_START_SILENT.bat", "99_STOP_SILENT.bat", "sync_from_macos.bat"
)
foreach ($f in $win_scripts) { Move-IfExists $f "scripts\$f" }

$win_tools = @(
    "00_START_ALL.bat", "00_START_POSTGRES.bat", "00_START_SILENT.bat", "01_START_LLM.bat",
    "02_START_RAG.bat", "03_START_TAX.bat", "04_START_WEB.bat", "05_DOWNLOAD_MODELS.bat",
    "06_SETUP_ENV.bat", "07_RESTORE_DB.bat", "99_STOP_ALL.bat", "99_STOP_SILENT.bat", "OPEN_LLM_CONSOLE.bat"
)
foreach ($f in $win_tools) { Move-IfExists $f "windows_scripts\$f" }

Write-Host ""
Write-Host "=== Step 5: 移动 macOS .command 到 scripts/macos/ ==="
$macos = @("停止成都建工系统.command", "启动成都建工控制台.command")
foreach ($f in $macos) { Move-IfExists $f "scripts\macos\$f" }

Write-Host ""
Write-Host "=== Step 6: 移动 PowerShell 工具 ==="
$ps1 = @("start_services_hidden.ps1", "stop_services_hidden.ps1", "deploy_overwrite.ps1", "package_windows_minimal.ps1")
foreach ($f in $ps1) { Move-IfExists $f "scripts\powershell\$f" }

Write-Host ""
Write-Host "=== Step 7: 移动 Python 工具 ==="
$py_scripts = @(
    "patch_columns.py", "patch_confidence.py", "patch_entity_attrs.py", "patch_entity_kind.py",
    "patch_entity_status.py", "patch_entity_status2.py", "patch_entity_uscc.py", "patch_filter.py",
    "patch_filter2.py", "patch_llm_priority.py", "patch_metadata_regex.py", "patch_single_entity.py",
    "patch_table_width.py", "patch_table_width2.py", "patch_tax_seed.py", "fix_migration.py",
    "fix_migration2.py", "clear_biz_data.py", "auto_quality_pipeline.py", "psql.sh", "serve_web.py",
    "build_embedded_python.py", "fix_ingest_jobs_schema.py", "download_models.py",
    "generate_projects_02_03_archives.py", "generate_project_archives.py", "generate_tax_certificates.py",
    "phase25_apply.py", "v3_phase_b_canonical_party_migration.py"
)
foreach ($f in $py_scripts) { Move-IfExists $f "scripts\python\$f" }

Write-Host ""
Write-Host "=== Step 8: 移动运行时 PID 到 runtime/ ==="
$pids = @(".app.pid", ".idp.pid", ".local_llm.pid", ".rag.pid", ".tax.pid")
foreach ($f in $pids) { Move-IfExists $f "runtime\$f" }

Write-Host ""
Write-Host "=== 整理完成 ==="
Write-Host "根目录剩余文件："
Get-ChildItem $root -File | Select-Object Name | Format-Table -AutoSize
