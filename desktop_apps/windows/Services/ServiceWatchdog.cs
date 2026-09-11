namespace ChengduConstructionController.Services;

using ChengduConstructionController.Models;

/// <summary>
/// Watchdog that prevents restart storms when a service keeps crashing.
///
/// Behavior:
///   - Tracks consecutive crash count within a sliding 5-minute window.
///   - If a service runs stably for > 5 minutes, the crash counter resets.
///   - Restart attempts use exponential backoff: 1s -> 2s -> 4s -> 8s -> 16s.
///   - After 5 crashes within 5 minutes, the circuit breaker trips:
///     the watchdog refuses to restart and surfaces an error to the user
///     instead of CPU-spinning on a broken service.
/// </summary>
public sealed class ServiceWatchdog
{
    private const int MaxCrashesBeforeTrip = 5;
    private const int StableRunResetMinutes = 5;
    private const int MaxBackoffSeconds = 30;

    private readonly object _gate = new();
    private readonly Dictionary<ServiceKind, WatchdogState> _states = new();

    /// <summary>
    /// Result of a ShouldRestart() check.
    /// </summary>
    public enum RestartDecision
    {
        /// <summary>Restart the service after the suggested backoff delay.</summary>
        RestartWithBackoff,
        /// <summary>Circuit breaker tripped — surface error and stop restarting.</summary>
        CircuitBreakerTripped,
    }

    public sealed record RestartVerdict(
        RestartDecision Decision,
        TimeSpan Delay,
        int ConsecutiveCrashes,
        string Reason);

    /// <summary>
    /// Records a successful start of the given service. Resets the crash count
    /// if the service remains healthy past the stable-run threshold.
    /// </summary>
    public void RecordSuccessfulStart(ServiceKind kind)
    {
        lock (_gate)
        {
            if (!_states.TryGetValue(kind, out var state))
            {
                _states[kind] = new WatchdogState { LastSuccessfulStartAt = DateTime.UtcNow };
                return;
            }

            // If the previous run was healthy for >= StableRunResetMinutes, reset counter.
            if (state.LastSuccessfulStartAt.HasValue)
            {
                var uptime = DateTime.UtcNow - state.LastSuccessfulStartAt.Value;
                if (uptime.TotalMinutes >= StableRunResetMinutes)
                {
                    state.ConsecutiveCrashes = 0;
                }
            }
            state.LastSuccessfulStartAt = DateTime.UtcNow;
        }
    }

    /// <summary>
    /// Records that the service started but crashed within the stable-run window.
    /// Returns the verdict on whether and when to restart.
    /// </summary>
    public RestartVerdict RecordCrashAndDecide(ServiceKind kind)
    {
        lock (_gate)
        {
            if (!_states.TryGetValue(kind, out var state))
            {
                state = new WatchdogState();
                _states[kind] = state;
            }

            state.ConsecutiveCrashes++;

            // Trip circuit breaker if too many crashes inside the window.
            if (state.ConsecutiveCrashes > MaxCrashesBeforeTrip)
            {
                return new RestartVerdict(
                    Decision: RestartDecision.CircuitBreakerTripped,
                    Delay: TimeSpan.Zero,
                    ConsecutiveCrashes: state.ConsecutiveCrashes,
                    Reason: $"{MaxCrashesBeforeTrip + 1} 次连续崩溃，熔断保护已触发。");
            }

            var backoffSeconds = Math.Min(
                (int)Math.Pow(2, state.ConsecutiveCrashes - 1),
                MaxBackoffSeconds);
            var delay = TimeSpan.FromSeconds(backoffSeconds);

            return new RestartVerdict(
                Decision: RestartDecision.RestartWithBackoff,
                Delay: delay,
                ConsecutiveCrashes: state.ConsecutiveCrashes,
                Reason: $"第 {state.ConsecutiveCrashes}/{MaxCrashesBeforeTrip} 次崩溃，将于 {backoffSeconds} 秒后重试。");
        }
    }

    /// <summary>
    /// Returns the current consecutive crash count without modifying state.
    /// </summary>
    public int GetCrashCount(ServiceKind kind)
    {
        lock (_gate)
        {
            return _states.TryGetValue(kind, out var state) ? state.ConsecutiveCrashes : 0;
        }
    }

    /// <summary>
    /// Resets the watchdog state for a service. Used when user manually intervenes
    /// or when the orchestrator decides to clear the circuit breaker.
    /// </summary>
    public void Reset(ServiceKind kind)
    {
        lock (_gate)
        {
            _states.Remove(kind);
        }
    }

    private sealed class WatchdogState
    {
        public int ConsecutiveCrashes;
        public DateTime? LastSuccessfulStartAt;
    }
}
