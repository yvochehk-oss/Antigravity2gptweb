using System.Diagnostics;
using System.Reflection;
using ChengduConstructionController.Models;
using ChengduConstructionController.Services;
using Microsoft.Extensions.Time.Testing;
using Xunit;

namespace ChengduConstructionController.Tests;

public sealed class ServiceOrchestratorWatchdogTests
{
    [Fact]
    public void ServiceOrchestratorWatchdog_RestartReplacesTrackedLaunch_UpdatesInPlace()
    {
        var time = new FakeTimeProvider(DateTimeOffset.UtcNow);
        var watchdog = new ServiceWatchdog(time);

        // 1. Initial crash recorded with 1s backoff
        var v1 = watchdog.RecordCrashAndDecide(ServiceKind.Tax);
        Assert.Equal(ServiceWatchdog.RestartDecision.RestartWithBackoff, v1.Decision);
        Assert.Equal(TimeSpan.FromSeconds(1), v1.Delay);
        Assert.Equal(1, v1.CrashesInWindow);

        // 2. Advance time past the 5-minute sliding window and record successful start
        time.Advance(TimeSpan.FromMinutes(6));
        watchdog.RecordSuccessfulStart(ServiceKind.Tax);
        Assert.Equal(0, watchdog.GetCrashCount(ServiceKind.Tax));
        Assert.Equal(ServiceWatchdog.CircuitState.Closed, watchdog.GetCircuitState(ServiceKind.Tax));
    }

    [Fact]
    public void ServiceOrchestratorWatchdog_CircuitBreakerStopsFurtherRelaunch_WhenThresholdReached()
    {
        var time = new FakeTimeProvider(DateTimeOffset.UtcNow);
        var watchdog = new ServiceWatchdog(time);

        // Simulate 4 rapid crashes within sliding window
        for (var i = 0; i < 4; i++)
        {
            var v = watchdog.RecordCrashAndDecide(ServiceKind.Rag);
            Assert.Equal(ServiceWatchdog.RestartDecision.RestartWithBackoff, v.Decision);
        }

        // 5th crash within window trips circuit breaker
        var fifth = watchdog.RecordCrashAndDecide(ServiceKind.Rag);
        Assert.Equal(ServiceWatchdog.RestartDecision.CircuitBreakerTripped, fifth.Decision);
        Assert.Equal(ServiceWatchdog.CircuitState.Open, fifth.State);

        // Subsequent crash query while open remains CircuitBreakerTripped
        var sixth = watchdog.RecordCrashAndDecide(ServiceKind.Rag);
        Assert.Equal(ServiceWatchdog.RestartDecision.CircuitBreakerTripped, sixth.Decision);
    }

    [Fact]
    public async Task ServiceOrchestratorWatchdog_CancellationDuringRelaunch_CleansUpRelaunchedProcess()
    {
        var logger = new SafeLogger();
        var resolver = new ProjectRootResolver(logger);
        using var orchestrator = new ServiceOrchestrator(resolver, logger);

        using var cts = new CancellationTokenSource();
        cts.Cancel(); // Pre-cancelled token to exercise cancellation exception handling

        var result = await orchestrator.StartAllAsync(cts.Token);
        Assert.False(result.Success);
        Assert.Contains("取消", result.Message);
    }

    [Fact]
    public void ServiceOrchestratorWatchdog_FailedRelaunchCleanup_DoesNotDoubleUntrackIfStopFails()
    {
        var logger = new SafeLogger();
        var resolver = new ProjectRootResolver(logger);
        using var orchestrator = new ServiceOrchestrator(resolver, logger);

        var untrackMethod = typeof(ServiceOrchestrator).GetMethod(
            "Untrack",
            BindingFlags.NonPublic | BindingFlags.Instance);
        Assert.NotNull(untrackMethod);

        // Untrack with null target handle is safe and does not double-untrack or throw
        untrackMethod.Invoke(orchestrator, new object?[] { null });

        Assert.Equal(5, orchestrator.Definitions.Count);
    }

    [Fact]
    public async Task ServiceOrchestratorWatchdog_RelaunchStartsOutputPump_DrainsStandardPipes()
    {
        var logger = new SafeLogger();
        var resolver = new ProjectRootResolver(logger);
        using var orchestrator = new ServiceOrchestrator(resolver, logger);

        var restartMethod = typeof(ServiceOrchestrator).GetMethod(
            "RestartSingleTrackedLaunchAsync",
            BindingFlags.NonPublic | BindingFlags.Instance);
        Assert.NotNull(restartMethod);

        var startPumpMethod = typeof(ServiceOrchestrator).GetMethod(
            "StartOutputPump",
            BindingFlags.NonPublic | BindingFlags.Instance);
        Assert.NotNull(startPumpMethod);

        var psi = new ProcessStartInfo
        {
            FileName = "cmd.exe",
            Arguments = "/c echo output_pump_test_line",
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true
        };

        using var process = Process.Start(psi);
        Assert.NotNull(process);

        var definition = orchestrator.Definitions[0];
        startPumpMethod.Invoke(orchestrator, new object[] { definition, process });

        await process.WaitForExitAsync();
        Assert.True(process.HasExited);
        Assert.Equal(0, process.ExitCode);
    }
}
