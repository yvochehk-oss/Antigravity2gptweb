using Microsoft.Win32;

namespace ChengduConstructionController.Services;

/// <summary>
/// Detects the presence of the WebView2 runtime and provides silent install
/// capability for environments where it is missing. Designed to be friendly
/// to non-administrator user accounts by:
///   1. Checking HKCU (user-level) registry first (no admin needed)
///   2. Falling back to HKLM (system-level) registry
///   3. Using --per-user install mode when bootstrapping
/// </summary>
public static class WebView2Detector
{
    // Microsoft.WebView2.Runtime client GUID
    private const string WebView2ClientGuid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";

    private const string UserRegistryPath = @"SOFTWARE\Microsoft\EdgeUpdate\Clients\";
    private const string MachineRegistryPath = @"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\";
    private const string NativeMachineRegistryPath = @"SOFTWARE\Microsoft\EdgeUpdate\Clients\";

    /// <summary>
    /// Result of a WebView2 availability check.
    /// </summary>
    public sealed record WebView2Status(
        bool IsInstalled,
        Version? InstalledVersion,
        string InstallScope,
        string Detail)
    {
        public static WebView2Status NotInstalled(string detail) =>
            new(false, null, "none", detail);

        public static WebView2Status Found(Version version, string scope, string detail) =>
            new(true, version, scope, detail);
    }

    /// <summary>
    /// Checks whether the Microsoft Edge WebView2 runtime is installed on this machine.
    ///
    /// Detection order (most permissive first):
    ///   1. HKCU user-level installation (always accessible, no admin required)
    ///   2. HKLM 64-bit redirect path (WOW6432Node)
    ///   3. HKLM native 64-bit path
    /// </summary>
    /// <returns>WebView2Status with version and scope information.</returns>
    public static WebView2Status CheckInstalled()
    {
        // 1. Try user-level registry (HKCU) — requires no admin rights.
        var userResult = ReadVersionFromRegistry(
            Registry.CurrentUser,
            UserRegistryPath + WebView2ClientGuid,
            "user-level (HKCU)");
        if (userResult.IsInstalled)
        {
            return userResult;
        }

        // 2. Try system-level 32-bit redirect view (WOW6432Node).
        var wow64Result = ReadVersionFromRegistry(
            Registry.LocalMachine,
            MachineRegistryPath + WebView2ClientGuid,
            "machine-level 32-bit (HKLM\\WOW6432Node)");
        if (wow64Result.IsInstalled)
        {
            return wow64Result;
        }

        // 3. Try system-level native 64-bit path.
        var nativeResult = ReadVersionFromRegistry(
            Registry.LocalMachine,
            NativeMachineRegistryPath + WebView2ClientGuid,
            "machine-level 64-bit (HKLM\\SOFTWARE)");
        if (nativeResult.IsInstalled)
        {
            return nativeResult;
        }

        return WebView2Status.NotInstalled(
            "未在 HKCU、HKLM\\WOW6432Node 或 HKLM\\SOFTWARE 中检测到 WebView2 Runtime");
    }

    /// <summary>
    /// Convenience method returning just the boolean availability.
    /// </summary>
    public static bool IsInstalled() => CheckInstalled().IsInstalled;

    /// <summary>
    /// Reads the WebView2 version from a specific registry hive + path.
    /// Returns NotInstalled if the registry key or "pv" value is missing.
    /// </summary>
    private static WebView2Status ReadVersionFromRegistry(
        RegistryKey hive,
        string subKeyPath,
        string scopeDescription)
    {
        try
        {
            using var key = hive.OpenSubKey(subKeyPath);
            if (key is null)
            {
                return WebView2Status.NotInstalled($"未找到注册表项 {scopeDescription}");
            }

            var pv = key.GetValue("pv") as string;
            if (string.IsNullOrWhiteSpace(pv))
            {
                return WebView2Status.NotInstalled($"注册表项 {scopeDescription} 缺少 'pv' 值");
            }

            if (!Version.TryParse(pv, out var version))
            {
                return WebView2Status.NotInstalled(
                    $"注册表项 {scopeDescription} 的 'pv' 值无法解析为版本号：{pv}");
            }

            return WebView2Status.Found(
                version,
                scopeDescription,
                $"WebView2 Runtime {version} 已安装于 {scopeDescription}");
        }
        catch (System.Security.SecurityException)
        {
            // HKLM without admin rights can throw SecurityException.
            return WebView2Status.NotInstalled($"访问注册表 {scopeDescription} 权限不足");
        }
        catch (UnauthorizedAccessException)
        {
            return WebView2Status.NotInstalled($"访问注册表 {scopeDescription} 权限不足");
        }
        catch (Exception ex)
        {
            return WebView2Status.NotInstalled(
                $"读取注册表 {scopeDescription} 失败：{ex.GetType().Name} - {ex.Message}");
        }
    }

    /// <summary>
    /// Installs the WebView2 Runtime silently using the provided bootstrapper.
    /// Uses --per-user mode so it works for non-administrator user accounts.
    /// Elevation is intentionally NOT requested so a non-admin user can still
    /// complete the install via the per-user scope.
    ///
    /// Bootstrapper can be obtained from:
    ///   https://developer.microsoft.com/en-us/microsoft-edge/webview2/
    ///   File: MicrosoftEdgeWebview2Setup.exe
    /// </summary>
    /// <param name="bootstrapperPath">Full path to MicrosoftEdgeWebview2Setup.exe.</param>
    /// <param name="timeoutSeconds">Maximum time to wait for install completion.</param>
    /// <returns>True if install completed successfully; false otherwise.</returns>
    public static bool InstallSilently(string bootstrapperPath, int timeoutSeconds = 300)
    {
        if (string.IsNullOrWhiteSpace(bootstrapperPath))
        {
            return false;
        }

        if (!File.Exists(bootstrapperPath))
        {
            return false;
        }

        try
        {
            var psi = new System.Diagnostics.ProcessStartInfo
            {
                FileName = bootstrapperPath,
                // /silent        - no UI shown
                // /install       - install the runtime
                // --per-user     - install for current user only (no admin required)
                // No Verb="runas" - per-user install must NOT request elevation.
                Arguments = "/silent /install --per-user",
                UseShellExecute = true,
                CreateNoWindow = true,
                WindowStyle = System.Diagnostics.ProcessWindowStyle.Hidden,
            };

            using var process = System.Diagnostics.Process.Start(psi);
            if (process is null)
            {
                return false;
            }

            if (!process.WaitForExit(timeoutSeconds * 1000))
            {
                try { process.Kill(entireProcessTree: true); } catch { }
                return false;
            }

            return process.ExitCode == 0;
        }
        catch (Exception)
        {
            return false;
        }
    }
}
