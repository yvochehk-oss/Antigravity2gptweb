using System.Runtime.Intrinsics;
using System.Runtime.Intrinsics.X86;

namespace ChengduConstructionController.Services;

/// <summary>
/// Detects CPU hardware instruction set capabilities and routes to the
/// appropriate llama-server binary. The resolution chain is strictly:
///
///   1. AVX2        → llama-server-avx2.exe   (Intel Haswell+, AMD Excavator+)
///   2. SSE4.2      → llama-server-sse42.exe  (Intel Nehalem+, AMD Bulldozer+)
///   3. Generic SSE2→ llama-server-generic.exe
///   4. Fail-Closed → no binary, the caller MUST refuse to start the LLM
///
/// We use <see cref="System.Runtime.Intrinsics"/>.IsSupported properties so
/// every decision is backed by an authoritative CPUID feature bit rather
/// than by absence-of-AVX2 inference (which falsely assumes SSE4.2).
/// </summary>
public static class CpuFeatureDetector
{
    public enum CpuTier
    {
        Avx2,
        Sse42,
        Generic,
        Unsupported,
    }

    /// <summary>
    /// Returns the highest supported CPU instruction set tier.
    /// </summary>
    public static CpuTier ResolveCpuTier()
    {
        if (Avx2.IsSupported)
        {
            return CpuTier.Avx2;
        }
        if (Sse42.IsSupported)
        {
            return CpuTier.Sse42;
        }
        if (Sse2.IsSupported)
        {
            return CpuTier.Generic;
        }

        return CpuTier.Unsupported;
    }

    /// <summary>
    /// Returns true if the CPU supports AVX2 (Haswell+ Intel, Excavator+ AMD).
    /// </summary>
    public static bool SupportsAvx2() => Avx2.IsSupported;

    /// <summary>
    /// Returns true if the CPU supports SSE4.2 (Nehalem+ Intel, Bulldozer+ AMD).
    /// </summary>
    public static bool SupportsSse42() => Sse42.IsSupported;

    /// <summary>
    /// Returns a human-readable description of the detected CPU instruction set level.
    /// </summary>
    public static string GetCpuFeatureLevel() => ResolveCpuTier() switch
    {
        CpuTier.Avx2 => "AVX2",
        CpuTier.Sse42 => "SSE4.2",
        CpuTier.Generic => "Generic (SSE2 baseline)",
        _ => "Unsupported (will fail-closed)",
    };

    /// <summary>
    /// Resolves the appropriate llama-server executable path based on detected CPU features.
    ///
    /// Resolution order:
    ///   1. llama-server-avx2.exe
    ///   2. llama-server-sse42.exe
    ///   3. llama-server-generic.exe
    ///   4. llama-server.exe       (only as a final fallback if the tier-specific
    ///                              binary is genuinely missing)
    ///
    /// Returns an empty string when the caller must refuse to start the LLM
    /// (CPU tier unsupported OR no compatible binary on disk).
    /// </summary>
    /// <param name="runtimeDirectory">Path to the runtime-win-cpu-x64 folder.</param>
    public static string ResolveLlamaServerExecutable(string runtimeDirectory)
    {
        if (string.IsNullOrWhiteSpace(runtimeDirectory) || !Directory.Exists(runtimeDirectory))
        {
            return string.Empty;
        }

        var tier = ResolveCpuTier();
        if (tier == CpuTier.Unsupported)
        {
            // Fail-Closed: do not start llama-server on a CPU that lacks SSE2.
            return string.Empty;
        }

        var orderedCandidates = tier switch
        {
            CpuTier.Avx2 => new[] { "llama-server-avx2.exe", "llama-server-sse42.exe", "llama-server-generic.exe" },
            CpuTier.Sse42 => new[] { "llama-server-sse42.exe", "llama-server-generic.exe" },
            _ => new[] { "llama-server-generic.exe" },
        };

        foreach (var candidate in orderedCandidates)
        {
            var candidatePath = Path.Combine(runtimeDirectory, candidate);
            if (File.Exists(candidatePath))
            {
                return candidatePath;
            }
        }

        // Last-resort fallback: a stock llama-server.exe may still work if the
        // operator built it for a baseline ISA. We do NOT silently fall back to
        // this for higher tiers because it could Illegal Instruction (0xC000001D)
        // on AVX2-requiring builds.
        var stockPath = Path.Combine(runtimeDirectory, "llama-server.exe");
        return File.Exists(stockPath) ? stockPath : string.Empty;
    }

    /// <summary>
    /// Returns the expected binary name for the current CPU without path resolution.
    /// Useful for logging which binary would be selected.
    /// </summary>
    public static string GetPreferredBinaryName() => ResolveCpuTier() switch
    {
        CpuTier.Avx2 => "llama-server-avx2.exe",
        CpuTier.Sse42 => "llama-server-sse42.exe",
        CpuTier.Generic => "llama-server-generic.exe",
        _ => string.Empty,
    };
}
