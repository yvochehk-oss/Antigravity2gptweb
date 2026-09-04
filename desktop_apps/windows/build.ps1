[CmdletBinding()]
param(
    [switch] $RunSmokeTests
)

$ErrorActionPreference = "Stop"
$AppDirectory = (Resolve-Path (Join-Path $PSScriptRoot ".")).Path
$ProjectFile = Join-Path $AppDirectory "ChengduConstructionController.csproj"
$PublishDirectory = Join-Path $AppDirectory "publish\win-x64"

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw "未找到 dotnet SDK。请在 Windows 上安装 .NET 8 SDK 后重新运行此脚本。"
}

if ($RunSmokeTests) {
    $python = Get-Command py -ErrorAction SilentlyContinue
    if ($null -eq $python) {
        $python = Get-Command python -ErrorAction SilentlyContinue
    }
    if ($null -eq $python) {
        throw "已要求静态自检，但未找到 Python。"
    }

    & $python.Source (Join-Path $AppDirectory "tests\static_smoke.py")
    if ($LASTEXITCODE -ne 0) {
        throw "静态自检失败。"
    }
}

New-Item -ItemType Directory -Path $PublishDirectory -Force | Out-Null
& dotnet restore $ProjectFile --ignore-failed-sources
if ($LASTEXITCODE -ne 0) {
    throw "dotnet restore 失败。"
}

& dotnet publish $ProjectFile --configuration Release --runtime win-x64 --self-contained false --no-restore --output $PublishDirectory -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -p:DebugType=None
if ($LASTEXITCODE -ne 0) {
    throw "Windows 发布构建失败。"
}

$publishedExe = Join-Path $PublishDirectory "成都建工控制台.exe"
if (-not (Test-Path $publishedExe)) {
    throw "发布目录没有生成 成都建工控制台.exe。"
}

Write-Host "已生成：$publishedExe"
