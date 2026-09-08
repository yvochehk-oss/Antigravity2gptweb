$ErrorActionPreference = "SilentlyContinue"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RootDir

$LogsDir = Join-Path $RootDir "logs"
if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null }

function Check-Port($port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    return ($null -ne $c)
}

# 1. PostgreSQL (54320 / 5432)
if (-not (Check-Port 54320) -and -not (Check-Port 5432)) {
    $pgCtl = Join-Path $RootDir "database\pgsql\bin\pg_ctl.exe"
    if (Test-Path $pgCtl) {
        $pgData = "F:\projectrag_pgdata"
        Start-Process -FilePath $pgCtl -ArgumentList "start -D `"$pgData`"" -WorkingDirectory $RootDir -WindowStyle Hidden
    }
}

# 2. LLM (8930)
if (-not (Check-Port 8930)) {
    $serverBin = Join-Path $RootDir "models\local-llm\runtime-win-cpu-x64\llama-server.exe"
    $modelFile = Join-Path $RootDir "models\local-llm\Ling-3.0-tiny-Q4_K_M.gguf"
    if (-not (Test-Path $modelFile)) { $modelFile = Join-Path $RootDir "models\local-llm\Qwen3.5-2B-Q4_K_M.gguf" }
    if (Test-Path $serverBin) {
        $args = "--model `"$modelFile`" --host 127.0.0.1 --port 8930 --alias ling-3.0-tiny --ctx-size 16384 --threads 4 --threads-batch 4 --batch-size 512 --ubatch-size 256 --gpu-layers 0 --reasoning off --parallel 1 --jinja"
        $llmLog = Join-Path $LogsDir "llm.log"
        Start-Process -FilePath $serverBin -ArgumentList $args -WorkingDirectory $RootDir -WindowStyle Hidden -RedirectStandardOutput $llmLog -RedirectStandardError $llmLog
    }
}

# 3. RAG (8922)
if (-not (Check-Port 8922)) {
    $ragDir = Join-Path $RootDir "source_code\0.2_RAG系统\project-rag-v1.1"
    $pyRag = Join-Path $ragDir ".venv\Scripts\python.exe"
    if (Test-Path $pyRag) {
        $ragLog = Join-Path $LogsDir "rag.log"
        Start-Process -FilePath $pyRag -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8922" -WorkingDirectory $ragDir -WindowStyle Hidden -RedirectStandardOutput $ragLog -RedirectStandardError $ragLog
    }
}

# 4. IDP (8933)
if (-not (Check-Port 8933)) {
    $idpDir = Join-Path $RootDir "source_code\0.4_IDP文档录入引擎_V3.0"
    $pyIdp = Join-Path $idpDir ".venv\Scripts\python.exe"
    if (Test-Path $pyIdp) {
        $idpLog = Join-Path $LogsDir "idp.log"
        Start-Process -FilePath $pyIdp -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8933" -WorkingDirectory $idpDir -WindowStyle Hidden -RedirectStandardOutput $idpLog -RedirectStandardError $idpLog
    }
}

# 5. TAX (8921)
if (-not (Check-Port 8921)) {
    $taxDir = Join-Path $RootDir "source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0"
    $pyTax = Join-Path $taxDir ".venv\Scripts\python.exe"
    if (Test-Path $pyTax) {
        $taxLog = Join-Path $LogsDir "tax.log"
        Start-Process -FilePath $pyTax -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8921" -WorkingDirectory $taxDir -WindowStyle Hidden -RedirectStandardOutput $taxLog -RedirectStandardError $taxLog
    }
}

# 6. WEB (5173)
if (-not (Check-Port 5173)) {
    $taxDir = Join-Path $RootDir "source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0"
    $pyTax = Join-Path $taxDir ".venv\Scripts\python.exe"
    $webScript = Join-Path $RootDir "windows_scripts\serve_web.py"
    if ((Test-Path $pyTax) -and (Test-Path $webScript)) {
        $webLog = Join-Path $LogsDir "web.log"
        Start-Process -FilePath $pyTax -ArgumentList "`"$webScript`" 5173" -WorkingDirectory $RootDir -WindowStyle Hidden -RedirectStandardOutput $webLog -RedirectStandardError $webLog
    }
}

Write-Output "ALL_SERVICES_DISPATCHED_SUCCESS"
