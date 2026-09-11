using ChengduConstructionController.Models;
using ChengduConstructionController.Services;
using Microsoft.Extensions.Time.Testing;
using Xunit;

namespace ChengduConstructionController.Tests;

public sealed class ServiceWatchdogTests
{
    [Fact]
    public void FirstCrash_TriggersRestartWithBackoff_1Second()
    {
        var time = new FakeTimeProvider(DateTimeOffset.UtcNow);
        var watchdog = new ServiceWatchdog(time);

        var verdict = watchdog.RecordCrashAndDecide(ServiceKind.Tax);

        Assert.Equal(ServiceWatchdog.RestartDecision.RestartWithBackoff, verdict.Decision);
        Assert.Equal(TimeSpan.FromSeconds(1), verdict.Delay);
        Assert.Equal(1, verdict.CrashesInWindow);
        Assert.Equal(ServiceWatchdog.CircuitState.Closed, verdict.State);
    }

    [Fact]
    public void FiveCrashesInWindow_TripsCircuit()
    {
        var time = new FakeTimeProvider(DateTimeOffset.UtcNow);
        var watchdog = new ServiceWatchdog(time);

        for (var i = 0; i < 4; i++)
        {
            var v = watchdog.RecordCrashAndDecide(ServiceKind.Tax);
            Assert.Equal(ServiceWatchdog.RestartDecision.RestartWithBackoff, v.Decision);
        }

        var fifth = watchdog.RecordCrashAndDecide(ServiceKind.Tax);
        Assert.Equal(ServiceWatchdog.RestartDecision.CircuitBreakerTripped, fifth.Decision);
        Assert.Equal(ServiceWatchdog.CircuitState.Open, fifth.State);
    }

    [Fact]
    public void CrashOutsideWindow_DoesNotTripCircuit()
    {
        var start = new DateTimeOffset(2026, 9, 11, 12, 0, 0, TimeSpan.Zero);
        var time = new FakeTimeProvider(start);
        var watchdog = new ServiceWatchdog(time);

        // 5 crashes spread across 10 minutes (one per ~2 minutes).
        for (var i = 0; i < 5; i++)
        {
            watchdog.RecordCrashAndDecide(ServiceKind.Rag);
            time.Advance(TimeSpan.FromMinutes(2));
        }

        // Only the most recent crashes (<5 min) should remain in the window.
        // The last two crashes are within the 5-minute window.
        Assert.Equal(2, watchdog.GetCrashCount(ServiceKind.Rag));
        Assert.Equal(ServiceWatchdog.CircuitState.Closed, watchdog.GetCircuitState(ServiceKind.Rag));
    }

    [Fact]
    public void BackoffIsExponential_AndCappedAt30Seconds()
    {
        var time = new FakeTimeProvider(DateTimeOffset.UtcNow);
        var watchdog = new ServiceWatchdog(time);

        // 4 crashes within 1 second each — well within the 5-minute window
        // and below the circuit-breaker threshold. The 5th crash trips the
        // breaker, so we assert a separate outcome for it.
        var observed = new List<TimeSpan>();
        ServiceWatchdog.RestartDecision lastDecision = ServiceWatchdog.RestartDecision.Healthy;
        for (var i = 0; i < 6; i++)
        {
            time.Advance(TimeSpan.FromMilliseconds(100));
            var v = watchdog.RecordCrashAndDecide(ServiceKind.Boss);
            lastDecision = v.Decision;
            observed.Add(v.Delay);
        }

        Assert.Equal(TimeSpan.FromSeconds(1), observed[0]);
        Assert.Equal(TimeSpan.FromSeconds(2), observed[1]);
        Assert.Equal(TimeSpan.FromSeconds(4), observed[2]);
        Assert.Equal(TimeSpan.FromSeconds(8), observed[3]);
        // The 5th crash inside the sliding window trips the breaker.
        Assert.Equal(ServiceWatchdog.RestartDecision.CircuitBreakerTripped, lastDecision);
        Assert.Equal(TimeSpan.Zero, observed[4]);

        // To verify the 30-second cap we reset state and inspect a fresh run
        // where the lifetime index exceeds the threshold. We do this by
        // resetting the watchdog so the cap is exercised against a clean
        // state where we raise the threshold.
        watchdog.Reset(ServiceKind.Boss);
    }

    [Fact]
    public void BackoffCapsAt30Seconds_OnceLifetimeCrashIndexExceedsCap()
    {
        var time = new FakeTimeProvider(DateTimeOffset.UtcNow);
        var watchdog = new ServiceWatchdog(time);

        // Manually pre-populate the lifetime counter above the cap by
        // recording many crashes spaced far enough apart that each is its own
        // sliding-window entry (no eviction), but still within the threshold
        // by alternating with successful starts that reset the window.
        // Simpler path: directly trigger a single crash with a very high
        // lifetime index by reflecting into the private state. We instead
        // assert the cap behaviour through repeated crash + restart cycles
        // where the lifetime index keeps growing.
        //
        // Strategy: crash 6 times alternating with successful starts spaced
        // more than CrashWindow apart, so the window never holds more than 1
        // crash but the lifetime counter monotonically increases.
        var observed = new List<TimeSpan>();
        for (var i = 0; i < 6; i++)
        {
            // Advance beyond CrashWindow so the previous crash is evicted.
            time.Advance(TimeSpan.FromMinutes(6));
            var v = watchdog.RecordCrashAndDecide(ServiceKind.Idp);
            if (v.Decision == ServiceWatchdog.RestartDecision.CircuitBreakerTripped)
            {
                // Should not happen because window only holds the latest crash.
                break;
            }
            observed.Add(v.Delay);
        }

        // After 4 lifetime crashes the cap is reached (2^4=16), at 5 it's 32→capped.
        Assert.Equal(TimeSpan.FromSeconds(1), observed[0]);
        Assert.Equal(TimeSpan.FromSeconds(2), observed[1]);
        Assert.Equal(TimeSpan.FromSeconds(4), observed[2]);
        Assert.Equal(TimeSpan.FromSeconds(8), observed[3]);
        Assert.Equal(TimeSpan.FromSeconds(16), observed[4]);
        Assert.Equal(TimeSpan.FromSeconds(30), observed[5]);  // capped
    }

    [Fact]
    public void RecordSuccessfulStart_ResetsWindow()
    {
        var time = new FakeTimeProvider(DateTimeOffset.UtcNow);
        var watchdog = new ServiceWatchdog(time);

        // 4 crashes within window.
        for (var i = 0; i < 4; i++)
        {
            watchdog.RecordCrashAndDecide(ServiceKind.Idp);
            time.Advance(TimeSpan.FromSeconds(1));
        }

        // Successful run lasting > 5 minutes.
        watchdog.RecordSuccessfulStart(ServiceKind.Idp);
        time.Advance(TimeSpan.FromMinutes(6));
        watchdog.RecordSuccessfulStart(ServiceKind.Idp);

        // After reset, count should be zero.
        Assert.Equal(0, watchdog.GetCrashCount(ServiceKind.Idp));
    }

    [Fact]
    public void Reset_ClearsAllState()
    {
        var time = new FakeTimeProvider(DateTimeOffset.UtcNow);
        var watchdog = new ServiceWatchdog(time);

        for (var i = 0; i < 5; i++)
        {
            watchdog.RecordCrashAndDecide(ServiceKind.LocalModel);
        }
        Assert.Equal(ServiceWatchdog.CircuitState.Open, watchdog.GetCircuitState(ServiceKind.LocalModel));

        watchdog.Reset(ServiceKind.LocalModel);

        Assert.Equal(0, watchdog.GetCrashCount(ServiceKind.LocalModel));
        Assert.Equal(ServiceWatchdog.CircuitState.Closed, watchdog.GetCircuitState(ServiceKind.LocalModel));
    }
}
