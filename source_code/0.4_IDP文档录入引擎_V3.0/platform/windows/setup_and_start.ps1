$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
Set-Location $RootDir

$VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    $UvBin = Join-Path $RootDir "..\..\windows_scripts\tools\uv.exe"
    if (Test-Path $UvBin) {
        Write-Host "Using bundled uv to create Python 3.14 venv for IDP..."
        & $UvBin venv --python 3.14 .venv
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv .venv
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv .venv
    } else {
        $RagPython = Join-Path $RootDir "..\0.2_RAG系统\project-rag-v1.1\.venv\Scripts\python.exe"
        if (Test-Path $RagPython) {
            $VenvPython = $RagPython
        } else {
            throw "未找到可用 Python 3 环境。请先双击运行 windows_scripts\06_一键配置Python314环境.bat。"
        }
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

$InstallOcr = "0"
if ($env:IDP_INSTALL_OCR) {
    $InstallOcr = $env:IDP_INSTALL_OCR.ToLowerInvariant()
}
if ($InstallOcr -in @("1", "true", "yes", "on")) {
    & $VenvPython -m pip install paddleocr paddlepaddle
}

$IdpHost = "127.0.0.1"
if ($env:IDP_HOST) { $IdpHost = $env:IDP_HOST }
$IdpPort = "8933"
if ($env:IDP_PORT) { $IdpPort = $env:IDP_PORT }

Write-Host "Starting Chengdu Construction IDP V3 on http://$IdpHost`:$IdpPort"
Write-Host "Ling failures degrade to review; Granite remains disabled unless GRANITE_ENABLED=1."
& $VenvPython -m uvicorn app.main:app --host $IdpHost --port $IdpPort
exit $LASTEXITCODE
