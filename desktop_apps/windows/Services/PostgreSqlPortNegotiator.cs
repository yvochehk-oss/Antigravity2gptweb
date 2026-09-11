using System.Diagnostics;

namespace ChengduConstructionController.Services;

/// <summary>
/// Negotiates an available PostgreSQL port and starts the bundled portable
/// PostgreSQL server without modifying on-disk configuration files.
///
/// This addresses two customer-environment problems:
///   1. Default port 54320 may already be in use (other apps, multiple V3.x installs).
///   2. The data directory is read-only on locked-down machines (UAC, U盘 readonly).
///
/// Solution: scan 54320-54369 for the first available port, then start
/// `postgres.exe -D <datadir> -p <port> -c "port=<port>"`. The `-c port=` argument
/// overrides the postgresql.conf file at runtime; no disk write needed.
/// </summary>
public sealed class PostgreSqlPortNegotiator
{
    private const int PreferredPort = 54320;
    private const int PortScanRange = 50;       // Try ports 54320..54369
    private const int StartupWaitSeconds = 8;   // How long to wait for startup to bind

    private readonly ProjectRootResolver _rootResolver;
    private readonly SafeLogger _logger;
    private int? _negotiatedPort;

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
    /// Discovers an available port in [54320, 54369] for the PostgreSQL server.
    /// Returns the first port that has no listening processes bound to it.
    /// </summary>
    /// <returns>The selected port number.</returns>
    /// <exception cref="InvalidOperationException">
    /// Thrown when all 50 candidate ports are already in use.
    /// </exception>
    public int NegotiateAvailablePort()
    {
        for (var offset = 0; offset < PortScanRange; offset++)
        {
            var candidatePort = PreferredPort + offset;
            var listeners = ProcessInspector.GetListeningProcessIds(candidatePort);
            if (listeners.Count == 0)
            {
                _negotiatedPort = candidatePort;
                _logger.Info($"[PostgreSql] 已协商端口 {candidatePort}（尝试 {offset + 1} 次）");
                return candidatePort;
            }
            _logger.Info($"[PostgreSql] 端口 {candidatePort} 已被占用（PID {string.Join(",", listeners)}）");
        }

        throw new InvalidOperationException(
            $"PostgreSQL 端口协商失败：{PreferredPort}~{PreferredPort + PortScanRange - 1} 区间内所有端口均被占用");
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
    /// Skips startup if the port is already bound by another process.
    /// </summary>
    /// <returns>The port the server is running on (negotiated or existing).</returns>
    public int EnsureRunning()
    {
        // Check if PostgreSQL is already running on the preferred port.
        var existing = ProcessInspector.GetListeningProcessIds(PreferredPort);
        if (existing.Count > 0)
        {
            _negotiatedPort = PreferredPort;
            _logger.Info($"[PostgreSql] 检测到端口 {PreferredPort} 已运行（PID {string.Join(",", existing)}）");
            return PreferredPort;
        }

        // No running PostgreSQL found - negotiate a port and start one.
        var port = ResolveOrNegotiatePort();
        StartPostgresServer(port);
        WaitForPortReady(port);
        return port;
    }

    /// <summary>
    /// Starts postgres.exe with the given port via -c "port=<port>" argument.
    /// The runtime -c argument overrides postgresql.conf without touching disk.
    /// </summary>
    private void StartPostgresServer(int port)
    {
        var pgBin = _rootResolver.ResolvePath(@"database\pgsql\bin");
        if (string.IsNullOrWhiteSpace(pgBin) || !Directory.Exists(pgBin))
        {
            throw new InvalidOperationException($"未找到便携 PostgreSQL bin 目录：{pgBin}");
        }

        var dataDir = _rootResolver.ResolvePath(@"database\data");
        if (string.IsNullOrWhiteSpace(dataDir) || !Directory.Exists(dataDir))
        {
            throw new InvalidOperationException($"未找到便携 PostgreSQL data 目录：{dataDir}");
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
    /// Polls the negotiated port until it begins listening or the timeout elapses.
    /// </summary>
    private void WaitForPortReady(int port)
    {
        var stopwatch = Stopwatch.StartNew();
        while (stopwatch.Elapsed.TotalSeconds < StartupWaitSeconds)
        {
            var listeners = ProcessInspector.GetListeningProcessIds(port);
            if (listeners.Count > 0)
            {
                _logger.Info($"[PostgreSql] 端口 {port} 已就绪（等待 {stopwatch.ElapsedMilliseconds} 毫秒）");
                return;
            }
            Thread.Sleep(200);
        }

        throw new InvalidOperationException(
            $"PostgreSQL 启动超时：端口 {port} 在 {StartupWaitSeconds} 秒内未进入监听状态");
    }
}
