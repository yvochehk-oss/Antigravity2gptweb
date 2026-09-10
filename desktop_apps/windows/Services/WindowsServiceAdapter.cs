using System.Diagnostics;
using System.Text;
using ChengduConstructionController.Models;

namespace ChengduConstructionController.Services;

/// <summary>
/// The Windows launch adapter deliberately calls already-installed project
/// runtimes directly. It does not invoke the legacy all-in-one batch files,
/// because those files open interactive command windows and the IDP wrapper may
/// install packages. No dependency installation, model download, migration, or
/// database command is performed here.
/// </summary>
public sealed class WindowsServiceAdapter
{
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

        var model = ResolveModelPath();
        if (model is null)
        {
            throw new InvalidOperationException("未找到已安装的本地语言模型，控制台不会自动下载模型。");
        }

        var alias = "spark-x2.5-4b";
        if (model.Contains("Ling", StringComparison.OrdinalIgnoreCase))
        {
            alias = "ling-3.0-tiny";
        }
        else if (model.Contains("Qwen", StringComparison.OrdinalIgnoreCase))
        {
            alias = "qwen3.5-2b";
        }

        var info = CreateHiddenProcessInfo(executable, workingDirectory);
        AddArguments(info,
            "--model", model,
            "--host", "127.0.0.1",
            "--port", definition.Port.ToString(System.Globalization.CultureInfo.InvariantCulture),
            "--alias", alias,
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
            AddArguments(pyInfo, serveWebScript, definition.Port.ToString(System.Globalization.CultureInfo.InvariantCulture));
            return pyInfo;
        }

        throw new InvalidOperationException("未找到老板驾驶舱的可用前端运行环境（缺少已安装 node_modules 且缺少 serve_web.py）。");
    }

    private string? ResolveModelPath()
    {
        foreach (var relativePath in new[]
        {
            @"models\local-llm\Spark-X2.5-4B-Q4_K_M.gguf",
            @"models\local-llm\Ling-3.0-tiny-Q4_K_M.gguf",
            @"models\local-llm\Qwen3.5-2B-Q4_K_M.gguf",
        })
        {
            var path = _rootResolver.ResolvePath(relativePath);
            if (!string.IsNullOrWhiteSpace(path) && File.Exists(path))
            {
                return path;
            }
        }

        return null;
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
