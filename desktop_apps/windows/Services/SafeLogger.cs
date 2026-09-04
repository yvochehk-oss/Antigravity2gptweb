using System.Text;
using System.Text.RegularExpressions;

namespace ChengduConstructionController.Services;

/// <summary>
/// Small local logger for the tray controller. Lines are redacted before they
/// leave memory so command output cannot accidentally persist credentials.
/// </summary>
public sealed class SafeLogger
{
    private const long MaxLogBytes = 2 * 1024 * 1024;
    private static readonly Regex SecretPattern = new(
        @"(?ix)(?<name>password|passwd|secret|token|api[_-]?key|authorization|cookie|private[_-]?key)\s*(?<separator>[:=])\s*(?<value>""[^""]*""|'[^']*'|[^\s,;&]+)",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);
    private static readonly Regex UrlSecretPattern = new(
        @"(?i)(?<name>[?&](?:token|secret|api[_-]?key|password)=)(?<value>[^&#\s]+)",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);
    private readonly object _gate = new();
    private readonly string _logDirectory;
    private readonly string _logPath;

    public SafeLogger()
    {
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        if (string.IsNullOrWhiteSpace(localAppData))
        {
            localAppData = Path.GetTempPath();
        }

        _logDirectory = Path.Combine(localAppData, "ChengduConstructionController");
        _logPath = Path.Combine(_logDirectory, "controller.log");
    }

    public string LogPath => _logPath;

    public void Info(string message) => Write("信息", message);

    public void Warn(string message) => Write("警告", message);

    public void Error(string message, Exception? exception = null)
    {
        var suffix = exception is null ? string.Empty : $"；{exception.GetType().Name}: {exception.Message}";
        Write("错误", message + suffix);
    }

    public string ReadTail(int maxCharacters = 12000)
    {
        lock (_gate)
        {
            try
            {
                if (!File.Exists(_logPath))
                {
                    return "暂无控制台日志。";
                }

                var text = File.ReadAllText(_logPath, Encoding.UTF8);
                return text.Length <= maxCharacters ? text : text[^maxCharacters..];
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
            {
                return "日志暂时无法读取。";
            }
        }
    }

    private void Write(string level, string message)
    {
        var safeMessage = Redact(message).Replace(Environment.NewLine, " ", StringComparison.Ordinal);
        var line = $"{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss zzz} [{level}] {safeMessage}{Environment.NewLine}";

        lock (_gate)
        {
            try
            {
                Directory.CreateDirectory(_logDirectory);
                RotateIfNeeded();
                File.AppendAllText(_logPath, line, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
            {
                // Logging must never take down a tray process or alter an operation result.
            }
        }
    }

    private void RotateIfNeeded()
    {
        try
        {
            if (!File.Exists(_logPath) || new FileInfo(_logPath).Length <= MaxLogBytes)
            {
                return;
            }

            var archivePath = _logPath + ".1";
            if (File.Exists(archivePath))
            {
                File.Delete(archivePath);
            }

            File.Move(_logPath, archivePath);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            // A locked log should not prevent the current line from being attempted.
        }
    }

    public static string Redact(string value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return value;
        }

        var redacted = SecretPattern.Replace(value, match =>
            $"{match.Groups["name"].Value}{match.Groups["separator"].Value}[已隐藏]");
        return UrlSecretPattern.Replace(redacted, match => $"{match.Groups["name"].Value}[已隐藏]");
    }
}
