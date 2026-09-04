using Microsoft.Win32;
using System.Security;

namespace ChengduConstructionController.Services;

/// <summary>
/// Current-user-only login registration. It never requests elevation and never
/// writes machine-wide startup locations.
/// </summary>
public sealed class StartupRegistration
{
    private const string RunKeyPath = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string ValueName = "ChengduConstructionController";
    private readonly SafeLogger _logger;

    public StartupRegistration(SafeLogger logger)
    {
        _logger = logger;
    }

    public bool IsEnabled()
    {
        if (!OperatingSystem.IsWindows())
        {
            return false;
        }

        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(RunKeyPath, writable: false);
            var value = key?.GetValue(ValueName) as string;
            return !string.IsNullOrWhiteSpace(value)
                && (value.Contains("成都建工控制台", StringComparison.OrdinalIgnoreCase)
                    || value.Contains("ChengduConstructionController", StringComparison.OrdinalIgnoreCase));
        }
        catch (Exception exception) when (exception is SecurityException or UnauthorizedAccessException)
        {
            _logger.Warn($"读取登录后自动运行设置失败：{exception.Message}");
            return false;
        }
    }

    public bool TrySetEnabled(bool enabled, out string message)
    {
        message = string.Empty;
        if (!OperatingSystem.IsWindows())
        {
            message = "只有 Windows 支持登录后自动运行。";
            return false;
        }

        try
        {
            using var key = Registry.CurrentUser.CreateSubKey(RunKeyPath, writable: true);
            if (key is null)
            {
                message = "无法打开当前用户的自动运行设置。";
                return false;
            }

            if (enabled)
            {
                var executable = Environment.ProcessPath;
                if (string.IsNullOrWhiteSpace(executable))
                {
                    message = "无法定位控制台程序。";
                    return false;
                }

                key.SetValue(ValueName, $"\"{executable.Replace("\"", string.Empty, StringComparison.Ordinal)}\" --autostart", RegistryValueKind.String);
                message = "已开启登录后自动运行。";
            }
            else
            {
                key.DeleteValue(ValueName, throwOnMissingValue: false);
                message = "已关闭登录后自动运行。";
            }

            _logger.Info(message);
            return true;
        }
        catch (Exception exception) when (exception is SecurityException or UnauthorizedAccessException or IOException)
        {
            message = "当前用户的自动运行设置无法修改。";
            _logger.Error(message, exception);
            return false;
        }
    }
}
