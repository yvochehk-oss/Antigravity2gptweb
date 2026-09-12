$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RootDir

$controllerCandidates = @(
    (Join-Path $RootDir "成都建工控制台3.1.exe"),
    (Join-Path $RootDir "desktop_apps\windows\publish\win-x64\成都建工控制台3.1.exe")
)
$controller = $controllerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if (-not $controller) {
    $build = Join-Path $RootDir "desktop_apps\windows\build.cmd"
    if (Test-Path -LiteralPath $build) {
        $buildProcess = Start-Process -FilePath $build -WorkingDirectory (Split-Path $build) -PassThru -Wait
        if ($buildProcess.ExitCode -ne 0) {
            throw "Windows controller build failed with exit code $($buildProcess.ExitCode)"
        }
        $controller = Join-Path $RootDir "desktop_apps\windows\publish\win-x64\成都建工控制台3.1.exe"
    }
}

if (-not $controller -or -not (Test-Path -LiteralPath $controller)) {
    throw "成都建工控制台3.1.exe not found"
}

$process = Start-Process -FilePath $controller -ArgumentList "--start-all" -WorkingDirectory $RootDir -WindowStyle Hidden -PassThru -Wait
if ($process.ExitCode -ne 0) {
    throw "C# runtime control plane failed with exit code $($process.ExitCode)"
}

Write-Output "ALL_SERVICES_READY"
