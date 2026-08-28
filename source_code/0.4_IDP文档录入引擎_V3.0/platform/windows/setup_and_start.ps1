$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
Set-Location $RootDir

$VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    $Python = $null
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $Python = @("py", "-3")
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $Python = @("python")
    } else {
        throw "Python 3 not found. Install Python 3.11+ and retry."
    }

    if ($Python.Count -eq 2) {
        & $Python[0] $Python[1] -m venv .venv
    } else {
        & $Python[0] -m venv .venv
    }
}

$Requirements = Join-Path $RootDir "requirements-v3.txt"
$Marker = Join-Path $RootDir ".venv\.idp-v3-ready"
$NeedInstall = -not (Test-Path $Marker)
if (-not $NeedInstall) {
    $NeedInstall = (Get-Item $Requirements).LastWriteTimeUtc -gt (Get-Item $Marker).LastWriteTimeUtc
}

if ($NeedInstall) {
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -r $Requirements
    New-Item -ItemType File -Path $Marker -Force | Out-Null
}

if (-not (Test-Path (Join-Path $RootDir ".env"))) {
    Copy-Item (Join-Path $RootDir ".env.example") (Join-Path $RootDir ".env")
    Write-Host "Created .env from .env.example. Review DATABASE_URL and model endpoints before production use."
}

$InstallOcr = ($env:IDP_INSTALL_OCR ?? "0").ToLowerInvariant()
if ($InstallOcr -in @("1", "true", "yes", "on")) {
    & $VenvPython -m pip install paddleocr paddlepaddle
}

$IdpHost = if ($env:IDP_HOST) { $env:IDP_HOST } else { "127.0.0.1" }
$IdpPort = if ($env:IDP_PORT) { $env:IDP_PORT } else { "8930" }

Write-Host "Starting Chengdu Construction IDP V3 on http://$IdpHost`:$IdpPort"
Write-Host "Ling failures degrade to review; Granite remains disabled unless GRANITE_ENABLED=1."
& $VenvPython -m uvicorn app.main:app --host $IdpHost --port $IdpPort
exit $LASTEXITCODE
