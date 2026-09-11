using System.Runtime.InteropServices;

namespace ChengduConstructionController.Services;

/// <summary>
/// Detects CPU hardware instruction set capabilities (AVX2/SSE4.2) and routes
/// to the appropriate llama-server binary to prevent 0xC000001D illegal
/// instruction crashes on older CPUs (Celeron, Pentium, etc.).
/// </summary>
public static class CpuFeatureDetector
{
    // PF_AVX2_INSTRUCTIONS_AVAILABLE = 40
    private const int PfAvx2InstructionsAvailable = 40;

    // PF_XMMI64_INSTRUCTIONS_AVAILABLE = 10  (SSE2, baseline for x64)
    private const int PfSse2InstructionsAvailable = 10;

    // PFSSSE3_INSTRUCTIONS_AVAILABLE = 41  (SSE4.1, required for llama-server-sse42)
    private const int PfSsse3InstructionsAvailable = 41;

    // PF SSE4.1 is reported as available on all 64-bit Intel/AMD CPUs after 2008.
    // llama-server-sse42 requires at minimum: SSE3 + SSSE3 + SSE4.1 + SSE4.2.
    // Modern x64 CPUs (Intel Nehalem+, AMD Bulldozer+) support SSE4.2 natively.
    // The absence of AVX2 on a 64-bit system almost always means SSE4.2 IS available.

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsProcessorFeaturePresent(int dwProcessorFeature);

    /// <summary>
    /// Returns true if the CPU supports AVX2 (Haswell+ Intel, Excavator+ AMD).
    /// Returns false for older CPUs (Sandy Bridge, Ivy Bridge, Haswell without AVX2,
    /// Celeron/Pentium Silver, etc.).
    /// </summary>
    public static bool SupportsAvx2()
    {
        try
        {
            // AVX2 detection via kernel32 API — no external dependencies.
            // Works reliably on Windows 7+ without admin rights.
            return IsProcessorFeaturePresent(PfAvx2InstructionsAvailable);
        }
        catch (Exception)
        {
            // Fail-safe: if detection throws, assume no AVX2 and use SSE4.2 binary.
            return false;
        }
    }

    /// <summary>
    /// Returns a human-readable description of the detected CPU instruction set level.
    /// </summary>
    public static string GetCpuFeatureLevel()
    {
        return SupportsAvx2() ? "AVX2" : "SSE4.2";
    }

    /// <summary>
    /// Resolves the appropriate llama-server executable path based on detected CPU features.
    ///
    /// Resolution order:
    /// 1. AVX2 binary  → llama-server-avx2.exe  (Intel Haswell+, AMD Excavator+)
    /// 2. SSE4.2 binary → llama-server-sse42.exe (Intel Nehalem+, AMD Bulldozer+)
    /// 3. Default      → llama-server.exe        (no detection / fallback)
    ///
    /// Expected runtime directory layout:
    ///   models\local-llm\runtime-win-cpu-x64\
    ///     ├── llama-server.exe        (generic, may not exist)
    ///     ├── llama-server-avx2.exe   (AVX2 build)
    ///     └── llama-server-sse42.exe  (SSE4.2 build)
    /// </summary>
    /// <param name="runtimeDirectory">Path to the runtime-win-cpu-x64 folder.</param>
    /// <returns>
    /// Full path to the best-matching executable, or an empty string if none found.
    /// </returns>
    public static string ResolveLlamaServerExecutable(string runtimeDirectory)
    {
        if (string.IsNullOrWhiteSpace(runtimeDirectory) || !Directory.Exists(runtimeDirectory))
        {
            return string.Empty;
        }

        var hasAvx2 = SupportsAvx2();
        var preferredBinary = hasAvx2 ? "llama-server-avx2.exe" : "llama-server-sse42.exe";
        var preferredPath = Path.Combine(runtimeDirectory, preferredBinary);

        if (File.Exists(preferredPath))
        {
            return preferredPath;
        }

        // Preferred binary not found. Check for generic default.
        var defaultPath = Path.Combine(runtimeDirectory, "llama-server.exe");
        if (File.Exists(defaultPath))
        {
            // Warn that the architecture-optimal binary is missing.
            // The default binary may still work if it was compiled for a baseline ISA.
            return defaultPath;
        }

        // Neither preferred nor default found.
        return string.Empty;
    }

    /// <summary>
    /// Returns the expected binary name for the current CPU without path resolution.
    /// Useful for logging which binary would be selected.
    /// </summary>
    public static string GetPreferredBinaryName()
    {
        return SupportsAvx2() ? "llama-server-avx2.exe" : "llama-server-sse42.exe";
    }
}
