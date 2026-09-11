$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RootDir

$LogsDir = Join-Path $RootDir "logs"
New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null

function Test-Port([int]$Port) {
    try {
        return $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1)
    } catch {
        return $false
    }
}

function Wait-Port([int]$Port, [int]$MaxAttempts, [string]$Name) {
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        if (Test-Port $Port) { return }
        $delaySeconds = if ($attempt -le 10) { 1 } else { 2 }
        Start-Sleep -Seconds $delaySeconds
    }
    throw "$Name did not listen on port $Port within the bounded startup window"
}

function Start-CanonicalBatch([string]$RelativePath, [string]$LogStem) {
    $script = Join-Path $RootDir $RelativePath
    if (-not (Test-Path -LiteralPath $script)) {
        throw "canonical launcher not found: $script"
    }

    $stdout = Join-Path $LogsDir "$LogStem.out.log"
    $stderr = Join-Path $LogsDir "$LogStem.err.log"
    $args = @('/d', '/s', '/c', ('call "{0}"' -f $script))
    return Start-Process -FilePath "cmd.exe" -ArgumentList $args -WorkingDirectory $RootDir -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
}

# PostgreSQL is synchronous and fail-closed. The canonical launcher also
# ensures the single-instance self-heal watchdog is running.
$pgLauncher = Join-Path $RootDir "windows_scripts\00_START_POSTGRES.bat"
if (-not (Test-Path -LiteralPath $pgLauncher)) {
    throw "PostgreSQL launcher missing: $pgLauncher"
}
$pgArgs = @('/d', '/s', '/c', ('call "{0}"' -f $pgLauncher))
$pg = Start-Process -FilePath "cmd.exe" -ArgumentList $pgArgs -WorkingDirectory $RootDir -WindowStyle Hidden -PassThru -Wait
if ($pg.ExitCode -ne 0) {
    throw "PostgreSQL 54320 startup failed with exit code $($pg.ExitCode)"
}
Wait-Port 54320 10 "PostgreSQL"

if (-not (Test-Port 8930)) {
    Start-CanonicalBatch "windows_scripts\01_START_LLM.bat" "llm" | Out-Null
}
Wait-Port 8930 45 "Local LLM"

if (-not (Test-Port 8922)) {
    Start-CanonicalBatch "windows_scripts\02_START_RAG.bat" "rag" | Out-Null
}
Wait-Port 8922 45 "RAG"

if (-not (Test-Port 8933)) {
    Start-CanonicalBatch "source_code\0.4_IDP文档录入引擎_V3.0\START_IDP_WINDOWS.bat" "idp" | Out-Null
}
Wait-Port 8933 90 "IDP"

if (-not (Test-Port 8921)) {
    Start-CanonicalBatch "windows_scripts\03_START_TAX.bat" "tax" | Out-Null
}
Wait-Port 8921 45 "Tax"

if (-not (Test-Port 5173)) {
    Start-CanonicalBatch "windows_scripts\04_START_WEB.bat" "web" | Out-Null
}
Wait-Port 5173 30 "Boss Web"

Write-Output "ALL_SERVICES_READY"
