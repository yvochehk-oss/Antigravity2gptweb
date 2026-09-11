using System.Diagnostics;
using System.Text;
using System.Text.RegularExpressions;
using ChengduConstructionController.Models;

namespace ChengduConstructionController.Services;

/// <summary>
/// Windows launch adapter for already-installed project runtimes. It keeps
/// startup non-interactive while enforcing the same runtime/database contracts
/// as the canonical Windows launchers.
/// </summary>
public sealed class WindowsServiceAdapter
{
    private const int MinimumSparkLlamaBuild = 10828;

    private readonly ProjectRootResolver _rootResolver;
    private readonly SafeLogger _logger;

    /// <summary>
    /// The currently negotiated PostgreSQL port. Defaults to 54320 (preferred)
    /// but is overridden at startup by ServiceOrchestrator when port negotiation
    /// succeeds with a different port.
    /// </summary>
    public int DatabasePort { get; set; } = 54320;

    public WindowsServiceAdapter(ProjectRootResolver rootResolver, SafeLogger logger)
    {
        _rootResolver = rootResolver;
        _logger = logger;
    }

    public Process Start(ServiceDefinition definition)
    {
        if (!OperatingSystem.IsWindows())
        {
            throw new PlatformNotSupportedException("该控制台只能在 Windows 上启动业务服务。");
        }

        var startInfo = BuildStartInfo(definition);
        var process = new Process
        {
            StartInfo = startInfo,
            EnableRaisingEvents = true,
        };
        if (!process.Start())
        {
            process.Dispose();
            throw new InvalidOperationException($"无法启动{definition.DisplayName}。");
        }

        _logger.Info($"已提交{definition.DisplayName}启动（进程 {process.Id}，窗口已隐藏）");
        return process;
    }

    public ProcessStartInfo BuildStartInfo(ServiceDefinition definition)
    {
        var workingDirectory = _rootResolver.ResolvePath(definition.RelativeWorkingDirectory);
        if (string.IsNullOrWhiteSpace(workingDirectory) || !Directory.Exists(workingDirectory))
        {
            throw new InvalidOperationException($"未找到{definition.DisplayName}工作目录，未执行启动。");
        }

        return definition.StartKind switch
        {
            ServiceStartKind.LocalExecutable => BuildLocalModelStartInfo(definition, workingDirectory),
            ServiceStartKind.PythonModule => BuildPythonStartInfo(definition, workingDirectory),
            ServiceStartKind.NpmPreview => BuildBossStartInfo(definition, workingDirectory),
            _ => throw new InvalidOperationException($"{definition.DisplayName}没有可用的 Windows 启动方式。"),
        };
    }

    private ProcessStartInfo BuildLocalModelStartInfo(ServiceDefinition definition, string workingDirectory)
    {
        // Resolve runtime directory from the configured relative executable path.
        var configuredExecutable = _rootResolver.ResolvePath(definition.RelativeExecutablePath ?? string.Empty);
        if (string.IsNullOrWhiteSpace(configuredExecutable) || !File.Exists(configuredExecutable))
        {
            throw new InvalidOperationException("未找到本地语言模型运行时目录，未执行启动。");
        }

        // Dynamically select the appropriate binary based on CPU instruction set (AVX2 vs SSE4.2).
        // This prevents 0xC000001D illegal instruction crashes on older CPUs.
        var runtimeDirectory = Path.GetDirectoryName(configuredExecutable) ?? string.Empty;
        var executable = CpuFeatureDetector.ResolveLlamaServerExecutable(runtimeDirectory);
        if (string.IsNullOrWhiteSpace(executable) || !File.Exists(executable))
        {
            throw new InvalidOperationException(
                $"未找到适合当前 CPU 的 llama-server 二进制（检测到 {CpuFeatureDetector.GetCpuFeatureLevel()}），"
                + "请确保 runtime-win-cpu-x64 目录下存在 llama-server-avx2.exe 或 llama-server-sse42.exe。");
        }

        _logger.Info($"[CpuFeatureDetector] 已选择 {CpuFeatureDetector.GetPreferredBinaryName()}（{CpuFeatureDetector.GetCpuFeatureLevel()}）");

        var model = ResolveModel();
        if (model is null)
        {
            throw new InvalidOperationException("未找到已安装的本地语言模型，控制台不会自动下载模型。");
        }

        if (model.Value.RequiresSpark25)
        {
            EnsureSparkRuntimeCompatible(executable);
        }

        var info = CreateHiddenProcessInfo(executable, workingDirectory);
        AddArguments(info,
            "--model", model.Value.Path,
            "--host", "127.0.0.1",
            "--port", definition.Port.ToString(System.Globalization.CultureInfo.InvariantCulture),
            "--alias", model.Value.Alias,
            "--ctx-size", "16384",
            "--threads", "4",
            "--threads-batch", "4",
            "--batch-size", "512",
            "--ubatch-size", "256",
            "--gpu-layers", "0",
            "--reasoning", "off",
            "--parallel", "1",
            "--jinja");
        return info;
    }

    private ProcessStartInfo BuildPythonStartInfo(ServiceDefinition definition, string workingDirectory)
    {
        // Use the dynamically negotiated port (default 54320). The orchestrator
        // sets DatabasePort after EnsurePostgresRunning() succeeds, so this
        // honors whatever port was actually bound by the portable PG server.
        var dbPort = DatabasePort;
        var dbListeners = ProcessInspector.GetListeningProcessIdsResult(dbPort);
        if (!dbListeners.Success)
        {
            throw new InvalidOperationException($"无法确认 PostgreSQL {dbPort} 端口状态：{dbListeners.Detail}");
        }
        if (dbListeners.ProcessIds.Count == 0)
        {
            throw new InvalidOperationException($"便携 PostgreSQL {dbPort} 未就绪，拒绝启动依赖数据库的 Python 服务。");
        }

        var python = ResolvePythonExecutable(definition, workingDirectory);
        if (string.IsNullOrWhiteSpace(python) || !File.Exists(python))
        {
            throw new InvalidOperationException($"未找到{definition.DisplayName}的 Windows Python 环境，请先运行 06_一键配置Python314环境.bat 或运行 build_embedded_python.py 制作嵌入式运行时。");
        }

        _logger.Info($"[Python] 使用解释器：{python}");

        var info = CreateHiddenProcessInfo(python, workingDirectory);
        info.Environment["PYTHONUNBUFFERED"] = "1";

        // Inject the negotiated port via environment variable so Python services
        // can dynamically pick it up via os.getenv("DATABASE_PORT").
        var databaseUrl = $"postgresql://postgres@127.0.0.1:{dbPort}/projectrag";
        info.Environment["DATABASE_PORT"] = dbPort.ToString(System.Globalization.CultureInfo.InvariantCulture);
        info.Environment["DATABASE_URL"] = databaseUrl;
        info.Environment["PROJECT_RAG_DB_URL"] = databaseUrl;

        var localAlias = ResolveManagedModelAlias();
        if (definition.Kind == ServiceKind.Rag)
        {
            info.Environment["RAG_LLM_BASE_URL"] = "http://127.0.0.1:8930/v1";
            info.Environment["RAG_LLM_LOCAL_BASE_URL"] = "http://127.0.0.1:8930/v1";
            info.Environment["RAG_LLM_MODEL"] = localAlias;
            info.Environment["RAG_LLM_LOCAL_MODEL"] = localAlias;
        }
        else if (definition.Kind == ServiceKind.Tax)
        {
            info.Environment["TAX_RAG_SERVICE_URL"] = "http://127.0.0.1:8922";
        }
        else if (definition.Kind == ServiceKind.Idp)
        {
            info.Environment["LING_BASE_URL"] = "http://127.0.0.1:8930/v1";
            info.Environment["LING_MODEL"] = localAlias;
        }

        AddArguments(info,
            "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1",
            "--port", definition.Port.ToString(System.Globalization.CultureInfo.InvariantCulture));
        return info;
    }

    private ProcessStartInfo BuildBossStartInfo(ServiceDefinition definition, string workingDirectory)
    {
        var distDirectory = Path.Combine(workingDirectory, "dist");
        var distIndexPath = Path.Combine(distDirectory, "index.html");

        // 优先级 1：dist 已存在 → 直接使用 serve_web.py（零 Node.js 依赖）
        if (Directory.Exists(distDirectory) && File.Exists(distIndexPath))
        {
            _logger.Info($"[Boss] 发现 dist 静态包，使用 serve_web.py（无需 Node.js）");
            return BuildServeWebProcessInfo(definition, workingDirectory);
        }

        // 优先级 2：dist 不存在 → 尝试自动构建
        var buildBat = _rootResolver.ResolvePath(@"windows_scripts\build_boss_dist.bat");
        if (!string.IsNullOrWhiteSpace(buildBat) && File.Exists(buildBat))
        {
            _logger.Info($"[Boss] dist 缺失，触发自动构建脚本：{buildBat}");
            var buildOk = RunBossBuildScript(buildBat);
            if (buildOk && Directory.Exists(distDirectory) && File.Exists(distIndexPath))
            {
                _logger.Info($"[Boss] 自动构建成功，切换到 serve_web.py 静态伺服");
                return BuildServeWebProcessInfo(definition, workingDirectory);
            }
            _logger.Warn($"[Boss] 自动构建失败，回退到下一优先级。");
        }

        // 优先级 3：有 node_modules 但 dist 缺失 → 使用 npm run preview
        if (File.Exists(Path.Combine(workingDirectory, "package.json")) &&
            Directory.Exists(Path.Combine(workingDirectory, "node_modules")))
        {
            _logger.Warn($"[Boss] 回退到 npm run preview（目标机需要 Node.js）");
            var commandShell = Environment.GetEnvironmentVariable("ComSpec");
            if (string.IsNullOrWhiteSpace(commandShell))
            {
                commandShell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "cmd.exe");
            }

            var info = CreateHiddenProcessInfo(commandShell, workingDirectory);
            info.Environment["VITE_API_BASE_URL"] = "http://127.0.0.1:8921";
            AddArguments(info,
                "/d", "/s", "/c",
                $"npm run preview -- --port {definition.Port} --host 0.0.0.0");
            return info;
        }

        // 兜底：尝试 serve_web.py（即使 dist 不存在，serve_web.py 也有 tax 静态目录回退）
        var serveWebScript = _rootResolver.ResolvePath(@"windows_scripts\serve_web.py");
        if (!string.IsNullOrWhiteSpace(serveWebScript) && File.Exists(serveWebScript))
        {
            _logger.Info($"[Boss] 兜底使用 serve_web.py（可能回退到税务系统静态目录）");
            return BuildServeWebProcessInfo(definition, workingDirectory);
        }

        throw new InvalidOperationException("未找到老板驾驶舱的可用前端运行环境（缺少 dist、node_modules 且缺少 serve_web.py）。");
    }

    /// <summary>
    /// Builds the ProcessStartInfo for the static Python HTTP server (serve_web.py).
    /// </summary>
    private ProcessStartInfo BuildServeWebProcessInfo(ServiceDefinition definition, string workingDirectory)
    {
        var serveWebScript = _rootResolver.ResolvePath(@"windows_scripts\serve_web.py");
        var taxPython = _rootResolver.ResolvePath(@"source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0\.venv\Scripts\python.exe");
        var python = (!string.IsNullOrWhiteSpace(taxPython) && File.Exists(taxPython))
            ? taxPython
            : "python.exe";

        var pyInfo = CreateHiddenProcessInfo(python, workingDirectory);
        pyInfo.Environment["PYTHONUNBUFFERED"] = "1";
        pyInfo.Environment["VITE_API_BASE_URL"] = "http://127.0.0.1:8921";
        AddArguments(pyInfo, serveWebScript, definition.Port.ToString(System.Globalization.CultureInfo.InvariantCulture));
        return pyInfo;
    }

    /// <summary>
    /// Runs the boss frontend dist build script synchronously.
    /// Returns true on successful exit, false on failure.
    /// </summary>
    private bool RunBossBuildScript(string buildBatPath)
    {
        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = "cmd.exe",
                Arguments = $"/d /s /c \"\"{buildBatPath}\"\"",
                WorkingDirectory = Path.GetDirectoryName(buildBatPath) ?? string.Empty,
                CreateNoWindow = true,
                UseShellExecute = false,
                RedirectStandardOutput = false,
                RedirectStandardError = false,
            };
            using var proc = Process.Start(psi);
            if (proc is null)
            {
                return false;
            }
            // Build can take several minutes on slow machines.
            if (!proc.WaitForExit(600_000))  // 10 minutes max
            {
                try { proc.Kill(entireProcessTree: true); } catch { }
                return false;
            }
            return proc.ExitCode == 0;
        }
        catch (Exception ex)
        {
            _logger.Warn($"[Boss] 构建脚本执行失败：{ex.Message}");
            return false;
        }
    }

    private (string Path, string Alias, bool RequiresSpark25)? ResolveModel()
    {
        var candidates = new[]
        {
            (RelativePath: @"models\local-llm\Spark-X2.5-4B-Q4_K_M.gguf", Alias: "spark-x2.5-4b", RequiresSpark25: true),
            (RelativePath: @"models\local-llm\Qwen3.5-2B-Q4_K_M.gguf", Alias: "qwen3.5-2b", RequiresSpark25: false),
            (RelativePath: @"models\local-llm\Ling-3.0-tiny-Q4_K_M.gguf", Alias: "ling-3.0-tiny", RequiresSpark25: false),
        };

        foreach (var candidate in candidates)
        {
            var path = _rootResolver.ResolvePath(candidate.RelativePath);
            if (!string.IsNullOrWhiteSpace(path) && File.Exists(path))
            {
                return (path, candidate.Alias, candidate.RequiresSpark25);
            }
        }

        return null;
    }

    private string ResolveManagedModelAlias() => ResolveModel()?.Alias ?? "spark-x2.5-4b";

    /// <summary>
    /// Resolves the Python executable path using a priority chain.
    ///
    /// Priority order:
    ///   1. Embedded portable Python runtime (v3.1+: runtime/python/Scripts/python.exe).
    ///      This is a trimmed-down venv (~600MB) for offline deployment.
    ///   2. Service-local .venv (most common during development).
    ///   3. RAG system venv (legacy fallback).
    ///   4. Tax system venv (legacy fallback).
    ///
    /// Returns the first existing path, or empty string if none found.
    /// </summary>
    private string ResolvePythonExecutable(ServiceDefinition definition, string workingDirectory)
    {
        // Priority 1: embedded portable Python runtime.
        var embeddedPython = _rootResolver.ResolvePath(@"runtime\python\Scripts\python.exe");
        if (!string.IsNullOrWhiteSpace(embeddedPython) && File.Exists(embeddedPython))
        {
            return embeddedPython;
        }

        // Priority 2: service-local .venv.
        var localVenv = Path.Combine(workingDirectory, ".venv", "Scripts", "python.exe");
        if (File.Exists(localVenv))
        {
            return localVenv;
        }

        // Priority 3: RAG system venv.
        var ragPython = _rootResolver.ResolvePath(@"source_code\0.2_RAG系统\project-rag-v1.1\.venv\Scripts\python.exe");
        if (!string.IsNullOrWhiteSpace(ragPython) && File.Exists(ragPython))
        {
            return ragPython;
        }

        // Priority 4: Tax system venv.
        var taxPython = _rootResolver.ResolvePath(@"source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0\.venv\Scripts\python.exe");
        if (!string.IsNullOrWhiteSpace(taxPython) && File.Exists(taxPython))
        {
            return taxPython;
        }

        return string.Empty;
    }

    private static void EnsureSparkRuntimeCompatible(string executable)
    {
        var info = new ProcessStartInfo
        {
            FileName = executable,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        info.ArgumentList.Add("--version");

        using var process = new Process { StartInfo = info };
        if (!process.Start())
        {
            throw new InvalidOperationException("无法执行 llama-server --version，拒绝以未确认 runtime 启动 Spark2_5。");
        }

        var stdoutTask = process.StandardOutput.ReadToEndAsync();
        var stderrTask = process.StandardError.ReadToEndAsync();
        if (!process.WaitForExit(15_000))
        {
            try { process.Kill(entireProcessTree: true); } catch { }
            try { process.WaitForExit(2_000); } catch { }
            throw new InvalidOperationException("llama-server --version 超时，拒绝以未确认 runtime 启动 Spark2_5。");
        }

        var stdout = stdoutTask.GetAwaiter().GetResult();
        var stderr = stderrTask.GetAwaiter().GetResult();
        var match = Regex.Match($"{stdout}\n{stderr}", @"\bbuild\s+(\d+)\b", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        if (!match.Success || !int.TryParse(match.Groups[1].Value, out var build))
        {
            throw new InvalidOperationException("无法识别 llama.cpp build 版本；请运行 python scripts\\download_models.py --runtime-only 修复 runtime。");
        }
        if (build < MinimumSparkLlamaBuild)
        {
            throw new InvalidOperationException($"当前 llama.cpp build {build} 不支持 Spark2_5；至少需要 build {MinimumSparkLlamaBuild}。请运行 python scripts\\download_models.py --runtime-only。");
        }
    }

    private static ProcessStartInfo CreateHiddenProcessInfo(string executable, string workingDirectory)
    {
        return new ProcessStartInfo
        {
            FileName = executable,
            WorkingDirectory = workingDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            // Long-running services are redirected only because ServiceOrchestrator
            // continuously drains both pipes through StartOutputPump/PumpAsync.
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
    }

    private static void AddArguments(ProcessStartInfo info, params string[] arguments)
    {
        foreach (var argument in arguments)
        {
            info.ArgumentList.Add(argument);
        }
    }
}
