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
    private readonly RequiredPythonPathResolver _pythonResolver;

    /// <summary>
    /// The currently negotiated PostgreSQL port. Defaults to 54320 (preferred)
    /// but is overridden at startup by ServiceOrchestrator when port negotiation
    /// succeeds with a different port.
    /// </summary>
    public int DatabasePort { get; set; } = 54320;

    public WindowsServiceAdapter(
        ProjectRootResolver rootResolver,
        SafeLogger logger,
        RequiredPythonPathResolver? pythonResolver = null)
    {
        _rootResolver = rootResolver;
        _logger = logger;
        _pythonResolver = pythonResolver ?? new RequiredPythonPathResolver(rootResolver, logger);
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
            ServiceStartKind.StaticPythonServer => BuildBossStartInfo(definition, workingDirectory),
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
            throw new InvalidOperationException(
                $"未找到 {definition.DisplayName} 的嵌入式 Python 运行时（runtime\\python\\Scripts\\python.exe）。"
                + "请确认 Installer 已部署嵌入式 Python；开发期可用 .venv 临时回退。");
        }

        // Fail-Closed: prefer embedded runtime. If a non-embedded venv is
        // being used, log a loud warning so customers know their deployment
        // is misconfigured (e.g. missing Installer step).
        var embeddedPython = _rootResolver.ResolvePath(@"runtime\python\Scripts\python.exe");
        if (string.IsNullOrWhiteSpace(embeddedPython) || !File.Exists(embeddedPython))
        {
            _logger.Warn(
                $"[Python] {definition.DisplayName} 启动时使用非嵌入式 Python（{python}）；"
                + "Installer 部署后必须存在 runtime\\python\\Scripts\\python.exe。");
        }

        // Probe the interpreter before launching the long-lived service. If
        // the binary is corrupted or wrong architecture the probe will fail.
        var (probeOk, probeDetail) = RequiredPythonPathResolver.ProbeVersion(python);
        if (!probeOk)
        {
            throw new InvalidOperationException(
                $"Python 解释器无法启动：{probeDetail}（路径：{python}）");
        }
        _logger.Info($"[Python] 使用解释器 {probeDetail}：{python}");

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

        // ============================================================
        // [V3.1] 老板端 Web 永远不允许 Node.js 运行时依赖。
        // 优先级：
        //   1. dist 已存在（开发期构建 / Installer 部署）
        //   2. 从 models\boss-dist 拷贝（Installer 随包资源）
        //   3. 仍然缺失 → 报错并拒绝启动（Fail-Closed）
        // ============================================================
        if (Directory.Exists(distDirectory) && File.Exists(distIndexPath))
        {
            _logger.Info($"[Boss] 发现 dist 静态包，使用 serve_web.py（无需 Node.js）");
            return BuildServeWebProcessInfo(definition, workingDirectory);
        }

        var bundledDist = _rootResolver.ResolvePath(@"models\boss-dist");
        if (!string.IsNullOrWhiteSpace(bundledDist)
            && Directory.Exists(bundledDist)
            && File.Exists(Path.Combine(bundledDist, "index.html")))
        {
            var targetDist = distDirectory;
            try
            {
                Directory.CreateDirectory(targetDist);
                // Mirror bundled dist into source_code/.../dist so serve_web.py
                // finds it on its canonical path. We use a simple recursive copy
                // because the directory only ships with the Installer and is
                // immutable at runtime.
                CopyDirectory(bundledDist, targetDist);
                _logger.Info($"[Boss] 已从 models/boss-dist 同步到 {targetDist}");
            }
            catch (Exception ex)
            {
                _logger.Warn($"[Boss] 拷贝内置 dist 失败：{ex.Message}");
            }

            if (File.Exists(Path.Combine(targetDist, "index.html")))
            {
                return BuildServeWebProcessInfo(definition, workingDirectory);
            }
        }

        // We intentionally do NOT fall back to npm run preview / vite.
        // Customers must never be asked to install Node.js at runtime.
        throw new InvalidOperationException(
            "老板端静态 dist 缺失：请通过 Vite 在开发期构建 dist，或确认 Installer 已部署 models\\boss-dist。"
            + "（V3.1 已移除 npm preview 兜底，不支持客户机运行时安装 Node.js。）");
    }

    private static void CopyDirectory(string source, string destination)
    {
        Directory.CreateDirectory(destination);
        foreach (var file in Directory.EnumerateFiles(source, "*", SearchOption.AllDirectories))
        {
            var relative = Path.GetRelativePath(source, file);
            var target = Path.Combine(destination, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.Copy(file, target, overwrite: true);
        }
    }

    /// <summary>
    /// Builds the ProcessStartInfo for the static Python HTTP server (serve_web.py).
    /// Python interpreter MUST come from the embedded runtime. Falling back to
    /// the system PATH python.exe is explicitly forbidden.
    /// </summary>
    private ProcessStartInfo BuildServeWebProcessInfo(ServiceDefinition definition, string workingDirectory)
    {
        var serveWebScript = _rootResolver.ResolvePath(@"windows_scripts\serve_web.py");
        var python = _pythonResolver.TryResolve(definition, workingDirectory);
        if (string.IsNullOrWhiteSpace(python))
        {
            throw new InvalidOperationException(
                $"未找到嵌入式 Python 运行时（runtime\\python\\Scripts\\python.exe）。"
                + $"{definition.DisplayName} 静态伺服无法启动；请确认 Installer 已正确部署。");
        }

        var pyInfo = CreateHiddenProcessInfo(python, workingDirectory);
        pyInfo.Environment["PYTHONUNBUFFERED"] = "1";
        pyInfo.Environment["VITE_API_BASE_URL"] = "http://127.0.0.1:8921";
        AddArguments(pyInfo, serveWebScript, definition.Port.ToString(System.Globalization.CultureInfo.InvariantCulture));
        return pyInfo;
    }

    private (string Path, string Alias, bool RequiresSpark25)? ResolveModel()
    {
        var candidates = new[]
        {
            (RelativePath: @"models\local-llm\Spark-X2.5-4B-Q4_K_M.gguf", Alias: "spark-x2.5-4b", RequiresSpark25: true),
            (RelativePath: @"models\local-llm\Qwen3.5-2B-Q4_K_M.gguf", Alias: "qwen3.5-2b", RequiresSpark25: false),
            (RelativePath: @"models\local-llm\Ling-3.1-tiny-Q4_K_M.gguf", Alias: "ling-3.1-tiny", RequiresSpark25: false),
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
    /// Resolves the Python executable path using a strict priority chain.
    ///
    /// Priority order:
    ///   1. Embedded portable Python runtime (runtime/python/Scripts/python.exe)
    ///      — REQUIRED for customer deployments. The Installer must ship this.
    ///   2. Service-local .venv (development only; logged as a warning when used)
    ///   3. Legacy RAG/Tax .venv (development only)
    ///
    /// Returns empty string when no path is available so callers can fail-closed
    /// with a precise error. This method deliberately does NOT fall back to
    /// the system PATH's python.exe.
    /// </summary>
    private string ResolvePythonExecutable(ServiceDefinition definition, string workingDirectory)
    {
        return _pythonResolver.TryResolve(definition, workingDirectory) ?? string.Empty;
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
