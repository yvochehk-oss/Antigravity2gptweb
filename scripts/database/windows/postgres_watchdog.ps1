param(
    [string]$RootDir = "",
    [int]$Port = 54320,
    [int]$CheckIntervalSeconds = 10
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RootDir)) {
    $RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
} else {
    $RootDir = (Resolve-Path $RootDir).Path
}

$logDir = Join-Path $RootDir "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logPath = Join-Path $logDir "postgres_watchdog.log"
$pidPath = Join-Path $logDir "postgres_watchdog.pid"
$pgIsReady = Join-Path $RootDir "database\pgsql\bin\pg_isready.exe"
$launcher = Join-Path $RootDir "windows_scripts\00_START_POSTGRES.bat"
$mutexName = "Local\ChengduConstruction.Postgres54320.Watchdog"
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
$ownsMutex = $false

function Write-WatchdogLog([string]$Message) {
    $line = "{0:o} {1}" -f [DateTimeOffset]::Now, $Message
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Test-PostgresReady {
    if (Test-Path -LiteralPath $pgIsReady) {
        & $pgIsReady -h 127.0.0.1 -p $Port -t 2 *> $null
        return ($LASTEXITCODE -eq 0)
    }

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(1500)) {
            return $false
        }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

try {
    try {
        $ownsMutex = $mutex.WaitOne(0, $false)
    } catch [System.Threading.AbandonedMutexException] {
        $ownsMutex = $true
    }

    if (-not $ownsMutex) {
        exit 0
    }

    Set-Content -LiteralPath $pidPath -Value $PID -Encoding Ascii
    Write-WatchdogLog "watchdog started; root=$RootDir port=$Port pid=$PID"

    if (-not (Test-Path -LiteralPath $launcher)) {
        throw "canonical PostgreSQL launcher not found: $launcher"
    }

    $backoffSeconds = 2
    while ($true) {
        if (Test-PostgresReady) {
            $backoffSeconds = 2
            Start-Sleep -Seconds ([Math]::Max(2, $CheckIntervalSeconds))
            continue
        }

        Write-WatchdogLog "PostgreSQL unhealthy; invoking canonical launcher"
        try {
            $arguments = @('/d', '/s', '/c', ('call "{0}"' -f $launcher))
            $process = Start-Process -FilePath "cmd.exe" -ArgumentList $arguments -WorkingDirectory $RootDir -WindowStyle Hidden -PassThru -Wait
            if ($process.ExitCode -eq 0 -and (Test-PostgresReady)) {
                Write-WatchdogLog "PostgreSQL recovered successfully"
                $backoffSeconds = 2
                Start-Sleep -Seconds ([Math]::Max(2, $CheckIntervalSeconds))
                continue
            }
            Write-WatchdogLog "recovery attempt failed; launcher exit=$($process.ExitCode)"
        } catch {
            Write-WatchdogLog "recovery attempt raised: $($_.Exception.Message)"
        }

        Start-Sleep -Seconds $backoffSeconds
        $backoffSeconds = [Math]::Min(30, $backoffSeconds * 2)
    }
} catch {
    Write-WatchdogLog "watchdog fatal error: $($_.Exception.Message)"
    exit 1
} finally {
    try {
        if (Test-Path -LiteralPath $pidPath) {
            $recordedPid = (Get-Content -LiteralPath $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
            if ([string]$recordedPid -eq [string]$PID) {
                Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
            }
        }
    } catch { }

    if ($ownsMutex) {
        try { $mutex.ReleaseMutex() } catch { }
    }
    $mutex.Dispose()
}
