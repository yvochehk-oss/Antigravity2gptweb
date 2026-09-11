using System.Diagnostics;
using System.Text.RegularExpressions;

namespace ChengduConstructionController.Services;

using ChengduConstructionController.Models;

/// <summary>
/// Strict single-source-of-truth for the Python executable path used to launch
/// every Python-backed subsystem (RAG, Tax, IDP, Boss static server).
///
/// Resolution order:
///   1. <c>runtime\python\Scripts\python.exe</c>     — embedded portable Python
///      shipped by the Installer. Required for customer deployments.
///   2. &lt;service&gt;/.venv/Scripts/python.exe        — service-local venv,
///      used during development. NOT acceptable as the customer-facing
///      fallback because customers must not depend on .venv layouts.
///   3. <c>source_code\0.2_RAG系统\...\.venv\python.exe</c>  — legacy venv
///      (development only).
///
/// IMPORTANT: this class deliberately does NOT fall back to <c>PATH</c>'s
/// <c>python.exe</c>. If the embedded runtime is missing the orchestrator MUST
/// refuse to start Python services. Customers cannot be expected to install
/// a system Python.
/// </summary>
public sealed class RequiredPythonPathResolver
{
    private readonly ProjectRootResolver _rootResolver;
    private readonly SafeLogger _logger;
    private string? _cachedPath;

    public RequiredPythonPathResolver(ProjectRootResolver rootResolver, SafeLogger logger)
    {
        _rootResolver = rootResolver;
        _logger = logger;
    }

    /// <summary>
    /// Resolves the Python executable path. Returns null when the embedded
    /// runtime is missing AND no development venv is available. Callers
    /// should treat null as a hard launch failure.
    /// </summary>
    public string? TryResolve(ServiceDefinition definition, string workingDirectory)
    {
        if (!string.IsNullOrWhiteSpace(_cachedPath) && File.Exists(_cachedPath))
        {
            return _cachedPath;
        }

        // Priority 1: embedded portable Python runtime (production path).
        var embedded = _rootResolver.ResolvePath(@"runtime\python\Scripts\python.exe");
        if (!string.IsNullOrWhiteSpace(embedded) && File.Exists(embedded))
        {
            _logger.Info($"[Python] 使用嵌入式运行时：{embedded}");
            _cachedPath = embedded;
            return embedded;
        }

        // Priority 2: service-local .venv (development path).
        var localVenv = Path.Combine(workingDirectory, ".venv", "Scripts", "python.exe");
        if (File.Exists(localVenv))
        {
            _logger.Warn(
                $"[Python] 未发现嵌入式 Python（runtime\\python\\Scripts\\python.exe），"
                + $"临时回退到开发期 venv：{localVenv}。Installer 部署后必须存在嵌入式运行时。");
            _cachedPath = localVenv;
            return localVenv;
        }

        // Priority 3: legacy RAG/Tax venvs (development path).
        var ragVenv = _rootResolver.ResolvePath(
            @"source_code\0.2_RAG系统\project-rag-v1.1\.venv\Scripts\python.exe");
        if (!string.IsNullOrWhiteSpace(ragVenv) && File.Exists(ragVenv))
        {
            _logger.Warn(
                $"[Python] 未发现嵌入式 Python，回退到 RAG venv：{ragVenv}。"
                + "Installer 部署后必须存在嵌入式运行时。");
            _cachedPath = ragVenv;
            return ragVenv;
        }

        _logger.Error(
            "[Python] 无法定位 Python 运行时："
            + $"嵌入式 {embedded} 不存在；{definition.DisplayName} 没有可用的 .venv。"
            + "Installer 部署必须包含 runtime\\python\\Scripts\\python.exe。");
        _cachedPath = null;
        return null;
    }

    /// <summary>
    /// Confirms the resolved Python interpreter actually runs and reports a
    /// version. Returns a (success, version-or-error) tuple.
    /// </summary>
    public static (bool Success, string Version) ProbeVersion(string pythonPath, int timeoutMs = 5000)
    {
        if (string.IsNullOrWhiteSpace(pythonPath) || !File.Exists(pythonPath))
        {
            return (false, "Python interpreter not found");
        }

        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = pythonPath,
                Arguments = "--version",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };

            using var process = Process.Start(psi);
            if (process is null)
            {
                return (false, "Failed to start python --version");
            }

            if (!process.WaitForExit(timeoutMs))
            {
                try { process.Kill(entireProcessTree: true); } catch { /* best effort */ }
                return (false, "python --version timed out");
            }

            var stdout = process.StandardOutput.ReadToEnd().Trim();
            var stderr = process.StandardError.ReadToEnd().Trim();
            var combined = string.IsNullOrWhiteSpace(stdout) ? stderr : stdout;

            var match = Regex.Match(combined, @"Python\s+(\d+\.\d+\.\d+)");
            if (match.Success)
            {
                return (true, match.Groups[1].Value);
            }
            return (true, combined);
        }
        catch (Exception ex)
        {
            return (false, $"python --version raised {ex.GetType().Name}: {ex.Message}");
        }
    }
}
