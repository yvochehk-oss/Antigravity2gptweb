using ChengduConstructionController.Models;

namespace ChengduConstructionController.Services;

/// <summary>
/// Resolves the V3.0 checkout at runtime. The published app may live below the
/// checkout or in a separate folder with CHENGDU_JIANGONG_ROOT configured; no
/// developer machine path is compiled into the application.
/// </summary>
public sealed class ProjectRootResolver : IDisposable
{
    private readonly SafeLogger _logger;
    private readonly string? _root;

    public ProjectRootResolver(SafeLogger logger)
    {
        _logger = logger;
        _root = ResolveRoot();
        if (_root is null)
        {
            _logger.Warn("未找到 V3.0 项目根目录；请将控制台放在 V3.0/desktop_apps/windows 下，或设置 CHENGDU_JIANGONG_ROOT。 ");
        }
    }

    public string? Root => _root;

    public bool IsResolved => _root is not null;

    public string? ResolvePath(string relativePath)
    {
        if (_root is null || string.IsNullOrWhiteSpace(relativePath) || Path.IsPathRooted(relativePath))
        {
            return null;
        }

        try
        {
            var fullPath = Path.GetFullPath(Path.Combine(_root, relativePath));
            var rootWithSeparator = _root.EndsWith(Path.DirectorySeparatorChar)
                ? _root
                : _root + Path.DirectorySeparatorChar;
            return fullPath.StartsWith(rootWithSeparator, StringComparison.OrdinalIgnoreCase)
                || fullPath.Equals(_root, StringComparison.OrdinalIgnoreCase)
                ? fullPath
                : null;
        }
        catch (Exception exception) when (exception is ArgumentException or IOException or UnauthorizedAccessException)
        {
            _logger.Warn($"无法解析项目路径：{exception.Message}");
            return null;
        }
    }

    public string Describe() => _root ?? "未找到项目根目录";

    private string? ResolveRoot()
    {
        var configured = Environment.GetEnvironmentVariable("CHENGDU_JIANGONG_ROOT");
        if (IsProjectRoot(configured, out var configuredRoot))
        {
            return configuredRoot;
        }

        foreach (var seed in new[] { AppContext.BaseDirectory, Environment.CurrentDirectory })
        {
            var candidate = seed;
            for (var depth = 0; depth < 8 && !string.IsNullOrWhiteSpace(candidate); depth++)
            {
                if (IsProjectRoot(candidate, out var discoveredRoot))
                {
                    return discoveredRoot;
                }

                var parent = Directory.GetParent(candidate);
                if (parent is null || parent.FullName.Equals(candidate, StringComparison.OrdinalIgnoreCase))
                {
                    break;
                }

                candidate = parent.FullName;
            }
        }

        return null;
    }

    private static bool IsProjectRoot(string? candidate, out string? normalizedRoot)
    {
        normalizedRoot = null;
        if (string.IsNullOrWhiteSpace(candidate))
        {
            return false;
        }

        try
        {
            var full = Path.GetFullPath(candidate.Trim().Trim('"'));
            if (!Directory.Exists(full))
            {
                return false;
            }

            var scripts = Path.Combine(full, "windows_scripts");
            var source = Path.Combine(full, "source_code");
            if (!Directory.Exists(scripts) || !Directory.Exists(source))
            {
                return false;
            }

            normalizedRoot = full.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            return true;
        }
        catch (Exception exception) when (exception is ArgumentException or IOException or UnauthorizedAccessException)
        {
            return false;
        }
    }

    public void Dispose()
    {
        // Kept disposable so the application can own the resolver as part of its
        // lifetime graph. It holds no OS handles or background work.
    }
}
