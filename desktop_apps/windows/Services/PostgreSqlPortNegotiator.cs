using System.Diagnostics;
using System.Text.RegularExpressions;
using ChengduConstructionController.Models;

namespace ChengduConstructionController.Services;

/// <summary>
/// Negotiates an available PostgreSQL port and starts the bundled portable
/// PostgreSQL server without modifying on-disk configuration files.
///
/// SSOT contract:
///   - The negotiated port is the SINGLE source of truth for any other
///     launcher or BAT script. No code path outside this class may hard-code
///     port 54320 (or any other constant); every consumer MUST read
///     <c>runtime\state\postgres.json</c> written by <c>PersistStateAsync</c>.
///   - Before trusting any listener on the negotiated port, this negotiator
///     verifies PostgreSQL identity via <c>pg_isready.exe</c> and the
///     <c>postgres</c> executable name. A python listener on the same port
///     cannot satisfy identity and is therefore rejected.
/// </summary>
public sealed class PostgreSqlPortNegotiator
{
    private const int PreferredPort = 54320;
    private const int PortScanRange = 50;        // Try ports 54320..54369
    private const int StartupWaitSeconds = 8;    // How long to wait for startup to bind
    private const int IdentityWaitSeconds = 30;  // Total window for pg_isready to confirm

    private readonly ProjectRootResolver _rootResolver;
    private readonly SafeLogger _logger;
    private int? _negotiatedPort;
    private PostgresState? _persistedState;

    public PostgreSqlPortNegotiator(ProjectRootResolver rootResolver, SafeLogger logger)
    {
        _rootResolver = rootResolver;
        _logger = logger;
    }

    /// <summary>
    /// The port currently in use for the negotiated PostgreSQL instance.
    /// Returns null if negotiation has not yet been performed.
    /// </summary>
    public int? ActivePort => _negotiatedPort;

    /// <summary>
    /// The persisted single source of truth state. Returns null if no
    /// successful handshake has occurred yet.
    /// </summary>
    public PostgresState? CurrentState => _persistedState;

    /// <summary>
    /// Scans [54320, 54369] and returns the first port whose listener passes
    /// a PostgreSQL identity check. A non-PG listener (Python, Tomcat, etc.)
    /// cannot satisfy identity and is skipped.
    /// </summary>
    public int NegotiateAvailablePort()
    {
        var portRangeStart = PreferredPort;
        var portRangeEnd = PreferredPort + PortScanRange - 1;

        for (var offset = 0; offset < PortScanRange; offset++)
        {
            var candidatePort = PreferredPort + offset;
            var listeners = ProcessInspector.GetListeningProcessIds(candidatePort);
            if (listeners.Count == 0)
            {
                _negotiatedPort = candidatePort;
                _logger.Info($"[PostgreSql] 端口 {candidatePort} 空闲（尝试 {offset + 1} 次）");
                return candidatePort;
            }

            // Identity check: if the listener claims PostgreSQL we keep it;
            // otherwise treat it as occupied and continue scanning.
            if (TryConfirmPostgresIdentity(candidatePort, out var identityDetail))
            {
                _negotiatedPort = candidatePort;
                _logger.Info($"[PostgreSql] 端口 {candidatePort} 已被现有 PostgreSQL 占用（PID {string.Join(",", listeners)}）：{identityDetail}");
                return candidatePort;
            }

            _logger.Warn(
                $"[PostgreSql] 端口 {candidatePort} 已被非 PostgreSQL 进程占用（PID {string.Join(",", listeners)}）；视为冲突并继续协商");
        }

        throw new InvalidOperationException(
            $"PostgreSQL 端口协商失败：{portRangeStart}~{portRangeEnd} 区间内所有端口均被占用或身份不符");
    }

    /// <summary>
    /// Returns the negotiated port (calling NegotiateAvailablePort if not yet done).
    /// </summary>
    public int ResolveOrNegotiatePort()
    {
        return _negotiatedPort ?? NegotiateAvailablePort();
    }

    /// <summary>
    /// Starts the bundled portable PostgreSQL server using the negotiated port.
    /// Idempotent: if a verified PostgreSQL listener is already running, that
    /// instance is reused and its state file refreshed.
    /// </summary>
    /// <returns>The port the server is running on (negotiated or existing).</returns>
    public async Task<int> EnsureRunningAsync(CancellationToken cancellationToken = default)
    {
        // Round 1: reuse an already-running PostgreSQL anywhere in [54320..54369].
        for (var offset = 0; offset < PortScanRange; offset++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var candidatePort = PreferredPort + offset;
            if (ProcessInspector.GetListeningProcessIds(candidatePort).Count == 0)
            {
                continue;
            }

            if (TryConfirmPostgresIdentity(candidatePort, out var identityDetail))
            {
                _negotiatedPort = candidatePort;
                _logger.Info(
                    $"[PostgreSql] 检测到已运行的 PostgreSQL（端口 {candidatePort}）：{identityDetail}");
                var existingState = await PersistStateAsync(candidatePort, cancellationToken)
                    .ConfigureAwait(false);
                return existingState.Port;
            }
        }

        // Round 2: find a free port and start a new PostgreSQL on it.
        var port = ResolveOrNegotiatePort();
        StartPostgresServer(port);
        await WaitForIdentityAsync(port, cancellationToken).ConfigureAwait(false);
        var state = await PersistStateAsync(port, cancellationToken).ConfigureAwait(false);
        return state.Port;
    }

    /// <summary>
    /// Starts postgres.exe with the given port via -c "port=&lt;port&gt;" argument.
    /// The runtime -c argument overrides postgresql.conf without touching disk.
    /// </summary>
    private void StartPostgresServer(int port)
    {
        var pgBin = _rootResolver.ResolvePath(@"database\pgsql\bin");
        if (string.IsNullOrWhiteSpace(pgBin) || !Directory.Exists(pgBin))
        {
            throw new InvalidOperationException(
                $"未找到便携 PostgreSQL bin 目录：{pgBin}；请确认 Setup.exe 已正确安装便携数据库。");
        }

        var dataDir = _rootResolver.ResolvePath(@"database\data");
        if (string.IsNullOrWhiteSpace(dataDir) || !Directory.Exists(dataDir))
        {
            throw new InvalidOperationException(
                $"未找到便携 PostgreSQL data 目录：{dataDir}；请确认 Setup.exe 已正确安装便携数据库。");
        }

        var postgresExe = Path.Combine(pgBin, "postgres.exe");
        if (!File.Exists(postgresExe))
        {
            throw new InvalidOperationException($"未找到 postgres.exe：{postgresExe}");
        }

        var psi = new ProcessStartInfo
        {
            FileName = postgresExe,
            // -D: data directory
            // -p: listen port (used for early startup)
            // -c "port=<port>": override postgresql.conf (no disk write)
            Arguments = $"-D \"{dataDir}\" -p {port} -c \"port={port}\"",
            WorkingDirectory = pgBin,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardOutput = false,
            RedirectStandardError = false,
        };

        var process = Process.Start(psi);
        if (process is null)
        {
            throw new InvalidOperationException("无法启动 postgres.exe 进程");
        }

        _logger.Info($"[PostgreSql] 已提交 postgres.exe 启动（端口 {port}，PID {process.Id}）");
    }

    /// <summary>
    /// Polls until pg_isready reports the negotiated port as accepting
    /// connections. A python listener that only opens a TCP socket can never
    /// satisfy pg_isready, so it is rejected.
    /// </summary>
    private async Task WaitForIdentityAsync(int port, CancellationToken cancellationToken)
    {
        var pgBin = _rootResolver.ResolvePath(@"database\pgsql\bin");
        if (string.IsNullOrWhiteSpace(pgBin))
        {
            throw new InvalidOperationException("未找到便携 PostgreSQL bin 目录，无法调用 pg_isready。");
        }

        var pgIsready = Path.Combine(pgBin, "pg_isready.exe");
        if (!File.Exists(pgIsready))
        {
            throw new InvalidOperationException(
                $"未找到 pg_isready.exe：{pgIsready}；无法确认 PostgreSQL 身份。");
        }

        var stopwatch = Stopwatch.StartNew();
        while (stopwatch.Elapsed.TotalSeconds < IdentityWaitSeconds)
        {
            cancellationToken.ThrowIfCancellationRequested();

            if (TryConfirmPostgresIdentity(port, out var detail))
            {
                _logger.Info(
                    $"[PostgreSql] 端口 {port} 已通过 pg_isready 身份确认（等待 {stopwatch.ElapsedMilliseconds} 毫秒）：{detail}");
                return;
            }

            await Task.Delay(500, cancellationToken).ConfigureAwait(false);
        }

        throw new InvalidOperationException(
            $"PostgreSQL 启动超时：端口 {port} 在 {IdentityWaitSeconds} 秒内未通过 pg_isready 身份确认");
    }

    /// <summary>
    /// Strict PostgreSQL identity check. Confirms BOTH that pg_isready returns
    /// exit code 0 AND that the listener process image path ends in
    /// postgres.exe. A python http.server or any other non-PG listener will
    /// fail at least one of these checks and be rejected.
    /// </summary>
    private bool TryConfirmPostgresIdentity(int port, out string detail)
    {
        detail = string.Empty;

        var listeners = ProcessInspector.GetListeningProcessIds(port);
        if (listeners.Count == 0)
        {
            detail = "端口无监听者";
            return false;
        }

        var pgBin = _rootResolver.ResolvePath(@"database\pgsql\bin");
        var pgIsready = string.IsNullOrWhiteSpace(pgBin) ? null : Path.Combine(pgBin, "pg_isready.exe");
        if (pgIsready is null || !File.Exists(pgIsready))
        {
            detail = "pg_isready 不存在，跳过协议级身份确认";
            return false;
        }

        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = pgIsready,
                Arguments = $"-h 127.0.0.1 -p {port} -t 1",
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            using var process = Process.Start(psi);
            if (process is null)
            {
                detail = "pg_isready 启动失败";
                return false;
            }

            if (!process.WaitForExit(2000))
            {
                try { process.Kill(entireProcessTree: true); } catch { /* best effort */ }
                detail = "pg_isready 超时";
                return false;
            }

            if (process.ExitCode != 0)
            {
                detail = $"pg_isready 退出码 {process.ExitCode}";
                return false;
            }

            // All listeners must map to postgres.exe. If any listener is a
            // foreign process, the port is treated as occupied.
            var nonPostgresListener = false;
            foreach (var pid in listeners)
            {
                var image = SafeReadExecutablePath(pid);
                if (string.IsNullOrWhiteSpace(image)
                    || !image.EndsWith("postgres.exe", StringComparison.OrdinalIgnoreCase))
                {
                    nonPostgresListener = true;
                    detail = $"PID {pid} 可执行路径 {image} 不是 postgres.exe";
                    break;
                }
            }
            if (nonPostgresListener)
            {
                return false;
            }

            detail = $"pg_isready OK；监听 PID {string.Join(",", listeners)}";
            return true;
        }
        catch (Exception ex)
        {
            detail = $"pg_isready 调用异常：{ex.Message}";
            return false;
        }
    }

    private static string? SafeReadExecutablePath(int processId)
    {
        try
        {
            using var process = Process.GetProcessById(processId);
            return process.MainModule?.FileName;
        }
        catch
        {
            return null;
        }
    }

    /// <summary>
    /// Writes <c>runtime\state\postgres.json</c> so every downstream consumer
    /// (BAT, Python launchers, IDP) can read the negotiated endpoint without
    /// depending on hard-coded constants.
    /// </summary>
    private async Task<PostgresState> PersistStateAsync(int port, CancellationToken cancellationToken)
    {
        var root = _rootResolver.Root ?? _rootResolver.ResolvePath(string.Empty) ?? string.Empty;
        var dataDir = _rootResolver.ResolvePath(@"database\data") ?? string.Empty;
        var postmasterPid = TryReadPostmasterPid(dataDir);
        var version = TryReadPostgresVersion();
        var startedAt = TryReadPostgresStartedAt(dataDir) ?? DateTimeOffset.UtcNow;

        var state = new PostgresState(
            Port: port,
            Host: PostgresState.DefaultHost,
            DataDir: dataDir,
            ProjectRoot: root,
            PostmasterPid: postmasterPid,
            StartedAt: startedAt,
            PostgresVersion: version);

        var stateFilePath = _rootResolver.ResolvePath(PostgresState.RelativeFilePath);
        if (string.IsNullOrWhiteSpace(stateFilePath))
        {
            _logger.Warn("[PostgreSql] 无法解析 runtime\\state\\postgres.json 路径，跳过持久化。");
            _persistedState = state;
            return state;
        }

        try
        {
            var directory = Path.GetDirectoryName(stateFilePath);
            if (!string.IsNullOrWhiteSpace(directory))
            {
                Directory.CreateDirectory(directory);
            }

            // Best-effort atomic write: write to a sibling .tmp then replace.
            var tempPath = stateFilePath + ".tmp";
            await File.WriteAllTextAsync(tempPath, state.Serialize(), cancellationToken).ConfigureAwait(false);
            if (File.Exists(stateFilePath))
            {
                File.Replace(tempPath, stateFilePath, destinationBackupFileName: null);
            }
            else
            {
                File.Move(tempPath, stateFilePath);
            }

            _persistedState = state;
            _logger.Info($"[PostgreSql] 已写入运行事实 {stateFilePath}（端口 {port}）");
            return state;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _logger.Warn($"[PostgreSql] 写入运行事实失败：{ex.Message}");
            _persistedState = state;
            return state;
        }
    }

    private static int TryReadPostmasterPid(string dataDir)
    {
        if (string.IsNullOrWhiteSpace(dataDir))
        {
            return 0;
        }

        var pidPath = Path.Combine(dataDir, "postmaster.pid");
        if (!File.Exists(pidPath))
        {
            return 0;
        }

        try
        {
            var firstLine = File.ReadAllLines(pidPath).FirstOrDefault();
            if (firstLine is null)
            {
                return 0;
            }

            return int.TryParse(firstLine.Trim(), out var pid) && pid > 4 ? pid : 0;
        }
        catch
        {
            return 0;
        }
    }

    private static DateTimeOffset? TryReadPostgresStartedAt(string dataDir)
    {
        if (string.IsNullOrWhiteSpace(dataDir))
        {
            return null;
        }

        var pidPath = Path.Combine(dataDir, "postmaster.pid");
        if (!File.Exists(pidPath))
        {
            return null;
        }

        try
        {
            var lines = File.ReadAllLines(pidPath);
            if (lines.Length < 2)
            {
                return null;
            }

            if (long.TryParse(lines[1].Trim(), out var unixSeconds) && unixSeconds > 0)
            {
                return DateTimeOffset.FromUnixTimeSeconds(unixSeconds);
            }
        }
        catch
        {
            return null;
        }

        return null;
    }

    private string? TryReadPostgresVersion()
    {
        var pgBin = _rootResolver.ResolvePath(@"database\pgsql\bin");
        if (string.IsNullOrWhiteSpace(pgBin))
        {
            return null;
        }

        var pgConfig = Path.Combine(pgBin, "pg_config.exe");
        if (!File.Exists(pgConfig))
        {
            return null;
        }

        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = pgConfig,
                Arguments = "--version",
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            using var process = Process.Start(psi);
            if (process is null)
            {
                return null;
            }

            if (!process.WaitForExit(1500))
            {
                try { process.Kill(entireProcessTree: true); } catch { /* best effort */ }
                return null;
            }

            var output = process.StandardOutput.ReadToEnd().Trim();
            return Regex.Match(output, @"PostgreSQL\s+([\d\.]+)").Groups[1].Value.NullIfEmpty();
        }
        catch
        {
            return null;
        }
    }
}

internal static class StringExtensions
{
    public static string? NullIfEmpty(this string? value) => string.IsNullOrWhiteSpace(value) ? null : value;
}
