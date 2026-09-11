using ChengduConstructionController.Models;

namespace ChengduConstructionController.Services;

/// <summary>
/// Resolves the V3.1 checkout at runtime. The published app may live below the
/// checkout or in a separate folder with CHENGDU_JIANGONG_ROOT configured; no
/// developer machine path is compiled into the application. A root selected in
/// the tray UI is persisted under LocalApplicationData and can be switched at
/// runtime without rebuilding the service graph.
/// </summary>
public sealed class ProjectRootResolver : IDisposable
{
    private const string RootFileName = "project_root.txt";
    private readonly SafeLogger _logger;
    private readonly object _gate = new();
    private readonly string _persistencePath;
    private string? _root;

    public ProjectRootResolver(SafeLogger logger)
    {
        _logger = logger;
        _persistencePath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "ChengduConstruction",
            RootFileName);
        _root = ResolveRoot();
        if (_root is null)
        {
            _logger.Warn("未找到 V3.1 项目根目录；可从托盘菜单选择项目目录，或设置 CHENGDU_JIANGONG_ROOT。");
        }
    }

    public string? Root
    {
        get
        {
            lock (_gate)
            {
                return _root;
            }
        }
    }

    public bool IsResolved => Root is not null;

    public string PersistencePath => _persistencePath;

    public string? ResolvePath(string relativePath)
    {
        var root = Root;
        if (root is null || string.IsNullOrWhiteSpace(relativePath) || Path.IsPathRooted(relativePath))
        {
            return null;
        }

        try
        {
            var fullPath = Path.GetFullPath(Path.Combine(root, relativePath));
            var rootWithSeparator = root.EndsWith(Path.DirectorySeparatorChar)
                ? root
                : root + Path.DirectorySeparatorChar;
            return fullPath.StartsWith(rootWithSeparator, StringComparison.OrdinalIgnoreCase)
                || fullPath.Equals(root, StringComparison.OrdinalIgnoreCase)
                ? fullPath
                : null;
        }
        catch (Exception exception) when (exception is ArgumentException or IOException or UnauthorizedAccessException)
        {
            _logger.Warn($"无法解析项目路径：{exception.Message}");
            return null;
        }
    }

    public string Describe() => Root ?? "未找到项目根目录";

    public bool TrySetRoot(string? candidate, out string message)
    {
        if (!IsProjectRoot(candidate, out var normalizedRoot) || normalizedRoot is null)
        {
            message = "所选目录不是有效的成都建工 V3.1 根目录；必须同时包含 windows_scripts 和 source_code。";
            return false;
        }

        try
        {
            PersistRoot(normalizedRoot);
            lock (_gate)
            {
                _root = normalizedRoot;
            }

            _logger.Info($"项目根目录已切换并持久化：{normalizedRoot}");
            message = $"项目目录已连接：{normalizedRoot}";
            return true;
        }
        catch (Exception exception) when (exception is ArgumentException or IOException or UnauthorizedAccessException)
        {
            _logger.Error("保存项目根目录失败", exception);
            message = $"项目目录有效，但无法保存设置：{exception.Message}";
            return false;
        }
    }

    private string? ResolveRoot()
    {
        var persisted = ReadPersistedRoot();
        if (IsProjectRoot(persisted, out var persistedRoot))
        {
            return persistedRoot;
        }

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

    private string? ReadPersistedRoot()
    {
        try
        {
            if (!File.Exists(_persistencePath))
            {
                return null;
            }

            return File.ReadAllText(_persistencePath).Trim();
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            _logger.Warn($"无法读取已保存的项目目录：{exception.Message}");
            return null;
        }
    }

    private void PersistRoot(string normalizedRoot)
    {
        var directory = Path.GetDirectoryName(_persistencePath)
            ?? throw new IOException("无法确定项目目录配置文件所在文件夹。");
        Directory.CreateDirectory(directory);

        var temporaryPath = _persistencePath + ".tmp";
        File.WriteAllText(temporaryPath, normalizedRoot + Environment.NewLine);
        File.Move(temporaryPath, _persistencePath, true);
    }

    public static bool IsProjectRoot(string? candidate, out string? normalizedRoot)
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
