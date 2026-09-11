$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
Set-Location $RootDir

$PipIndex = "https://mirrors.aliyun.com/pypi/simple/"
$VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    $UvBin = Join-Path $RootDir "..\..\windows_scripts\tools\uv.exe"
    if (Test-Path $UvBin) {
        Write-Host "Using bundled uv to create Python 3.12 venv for IDP..."
        & $UvBin venv --python 3.12 .venv
        if ($LASTEXITCODE -ne 0) { throw "Failed to create IDP Python 3.12 venv with bundled uv." }
    } elseif (Test-Path "D:\python3\python.exe") {
        & "D:\python3\python.exe" -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Failed to create IDP venv with D:\python3\python.exe." }
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Failed to create IDP Python 3.12 venv with py launcher." }
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Failed to create IDP venv with python." }
    } else {
        $RagPython = Join-Path $RootDir "..\0.2_RAG系统\project-rag-v1.1\.venv\Scripts\python.exe"
        if (Test-Path $RagPython) {
            $VenvPython = $RagPython
        } else {
            throw "No usable Python environment found. Run windows_scripts\06_SETUP_ENV.bat first."
        }
    }
}

if (-not (Test-Path $VenvPython)) {
    $VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
}
if (-not (Test-Path $VenvPython)) {
    throw "IDP virtual environment python is missing after setup: $VenvPython"
}

$Requirements = Join-Path $RootDir "requirements-v3.txt"
$Marker = Join-Path $RootDir ".venv\.idp-v3-ready"
$NeedInstall = -not (Test-Path $Marker)
if (-not $NeedInstall) {
    $NeedInstall = (Get-Item $Requirements).LastWriteTimeUtc -gt (Get-Item $Marker).LastWriteTimeUtc
}

if ($NeedInstall) {
    # 06_SETUP_ENV.bat normally installs these dependencies already. Verify the
    # local venv first so IDP startup never performs unnecessary network I/O.
    & $VenvPython -c "import fastapi, uvicorn, pydantic, fitz, multipart, dotenv, httpx, psycopg" *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "IDP dependencies already present; skipping pip network access."
        New-Item -ItemType File -Path $Marker -Force | Out-Null
        $NeedInstall = $false
    }
}

if ($NeedInstall) {
    Write-Host "Installing IDP dependencies from Aliyun PyPI mirror..."
    & $VenvPython -m pip install --disable-pip-version-check --retries 2 --timeout 15 -i $PipIndex -r $Requirements
    if ($LASTEXITCODE -ne 0) {
        throw "IDP dependency installation failed. Run windows_scripts\06_SETUP_ENV.bat while package mirror access is available."
    }
    New-Item -ItemType File -Path $Marker -Force | Out-Null
}

if (-not (Test-Path (Join-Path $RootDir ".env"))) {
    Copy-Item (Join-Path $RootDir ".env.example") (Join-Path $RootDir ".env")
    Write-Host "Created .env from .env.example. Review DATABASE_URL and model endpoints before production use."
}

$InstallOcr = "0"
if ($env:IDP_INSTALL_OCR) {
    $InstallOcr = $env:IDP_INSTALL_OCR.ToLowerInvariant()
}
if ($InstallOcr -in @("1", "true", "yes", "on")) {
    & $VenvPython -m pip install --disable-pip-version-check --retries 2 --timeout 15 -i $PipIndex paddleocr paddlepaddle
    if ($LASTEXITCODE -ne 0) { throw "Optional IDP OCR dependency installation failed." }
}

$IdpHost = "127.0.0.1"
if ($env:IDP_HOST) { $IdpHost = $env:IDP_HOST }
$IdpPort = "8933"
if ($env:IDP_PORT) { $IdpPort = $env:IDP_PORT }

Write-Host "Starting Chengdu Construction IDP V3 on http://$IdpHost`:$IdpPort"
Write-Host "Ling failures degrade to review; Granite remains disabled unless GRANITE_ENABLED=1."
& $VenvPython -m uvicorn app.main:app --host $IdpHost --port $IdpPort
exit $LASTEXITCODE
