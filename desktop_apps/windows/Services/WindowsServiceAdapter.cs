using System.Diagnostics;
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
        var executable = _rootResolver.ResolvePath(definition.RelativeExecutablePath ?? string.Empty);
        if (string.IsNullOrWhiteSpace(executable) || !File.Exists(executable))
        {
            throw new InvalidOperationException("未找到本地语言模型运行时，未执行启动。");
        }

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
        var dbListeners = ProcessInspector.GetListeningProcessIdsResult(54320);
        if (!dbListeners.Success)
        {
            throw new InvalidOperationException($"无法确认 PostgreSQL 54320 端口状态：{dbListeners.Detail}");
        }
        if (dbListeners.ProcessIds.Count == 0)
        {
            throw new InvalidOperationException("便携 PostgreSQL 54320 未就绪，拒绝启动依赖数据库的 Python 服务。");
        }

        var python = Path.Combine(workingDirectory, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python))
        {
            var ragPython = _rootResolver.ResolvePath(@"source_code\0.2_RAG系统\project-rag-v1.1\.venv\Scripts\python.exe");
            var taxPython = _rootResolver.ResolvePath(@"source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0\.venv\Scripts\python.exe");
            if (File.Exists(ragPython)) python = ragPython;
            else if (File.Exists(taxPython)) python = taxPython;
            else
            {
                throw new InvalidOperationException($"未找到{definition.DisplayName}的 Windows Python 环境，请先运行 06_一键配置Python314环境.bat。");
            }
        }

        var info = CreateHiddenProcessInfo(python, workingDirectory);
        info.Environment["PYTHONUNBUFFERED"] = "1";
        info.Environment["DATABASE_URL"] = "postgresql://postgres@127.0.0.1:54320/projectrag";
        info.Environment["PROJECT_RAG_DB_URL"] = "postgresql://postgres@127.0.0.1:54320/projectrag";

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
        if (File.Exists(Path.Combine(workingDirectory, "package.json")) &&
            Directory.Exists(Path.Combine(workingDirectory, "node_modules")))
        {
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

        var serveWebScript = _rootResolver.ResolvePath(@"windows_scripts\serve_web.py");
        if (!string.IsNullOrWhiteSpace(serveWebScript) && File.Exists(serveWebScript))
        {
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

        throw new InvalidOperationException("未找到老板驾驶舱的可用前端运行环境（缺少已安装 node_modules 且缺少 serve_web.py）。");
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
            // Never redirect long-running services into unread anonymous pipes.
            RedirectStandardOutput = false,
            RedirectStandardError = false,
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
