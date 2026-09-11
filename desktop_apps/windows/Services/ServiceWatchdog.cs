namespace ChengduConstructionController.Services;

using ChengduConstructionController.Models;

/// <summary>
/// Watchdog that prevents restart storms when a service keeps crashing.
///
/// Behavior:
///   - Real 5-minute sliding window via <see cref="Queue{DateTimeOffset}"/>.
///   - Backoff state machine: Healthy → Backoff → OpenCircuit → ManualReset.
///   - <see cref="RestartWithBackoff"/> actually executes
///     <c>await Task.Delay(verdict.Delay)</c> on the caller side; this class
///     only computes the verdict. The orchestrator must consume
///     <see cref="RestartVerdict.Delay"/> and re-launch the service.
///   - <see cref="TimeProvider"/> is injected so unit tests can advance
///     virtual time without sleeping.
/// </summary>
public sealed class ServiceWatchdog
{
    private static readonly TimeSpan CrashWindow = TimeSpan.FromMinutes(5);
    private const int CrashThreshold = 5;
    private const int MaxBackoffSeconds = 30;

    private readonly TimeProvider _time;
    private readonly object _gate = new();
    private readonly Dictionary<ServiceKind, WatchdogState> _states = new();
    public ServiceWatchdog() : this(TimeProvider.System) { }

    public ServiceWatchdog(TimeProvider time)
    {
        _time = time ?? throw new ArgumentNullException(nameof(time));
    }

    public enum RestartDecision
    {
        /// <summary>Service is healthy; no crash recorded.</summary>
        Healthy,
        /// <summary>Restart the service after the suggested backoff delay.</summary>
        RestartWithBackoff,
        /// <summary>Circuit breaker tripped — surface error and stop restarting.</summary>
        CircuitBreakerTripped,
    }

    public enum CircuitState
    {
        Closed,
        Open,
    }

    public sealed record RestartVerdict(
        RestartDecision Decision,
        TimeSpan Delay,
        int CrashesInWindow,
        CircuitState State,
        DateTimeOffset NextRetryAt,
        string Reason);

    /// <summary>
    /// Records a successful start of the given service. If the service has
    /// been healthy for at least one sliding window without crashing, the
    /// circuit is reset to <see cref="CircuitState.Closed"/>.
    /// </summary>
    public void RecordSuccessfulStart(ServiceKind kind)
    {
        var now = _time.GetUtcNow();
        lock (_gate)
        {
            if (!_states.TryGetValue(kind, out var state))
            {
                _states[kind] = new WatchdogState { LastSuccessfulStartAt = now };
                return;
            }

            // Window reset: if the previous run was healthy for at least the
            // sliding window, the circuit closes and crashes are forgotten.
            if (state.LastSuccessfulStartAt.HasValue)
            {
                var uptime = now - state.LastSuccessfulStartAt.Value;
                if (uptime >= CrashWindow)
                {
                    state.CrashTimestamps.Clear();
                    state.LifetimeCrashCount = 0;
                    state.State = CircuitState.Closed;
                }
            }

            state.LastSuccessfulStartAt = now;
        }
    }

    /// <summary>
    /// Records that the service started but crashed within the sliding window.
    /// Returns the verdict describing whether and when to restart.
    /// </summary>
    public RestartVerdict RecordCrashAndDecide(ServiceKind kind)
    {
        var now = _time.GetUtcNow();
        lock (_gate)
        {
            if (!_states.TryGetValue(kind, out var state))
            {
                state = new WatchdogState();
                _states[kind] = state;
            }

            // Insert the new crash.
            state.CrashTimestamps.Enqueue(now);

            // Evict timestamps older than the sliding window.
            while (state.CrashTimestamps.Count > 0
                   && now - state.CrashTimestamps.Peek() >= CrashWindow)
            {
                state.CrashTimestamps.Dequeue();
            }

            var crashesInWindow = state.CrashTimestamps.Count;
            if (crashesInWindow >= CrashThreshold)
            {
                state.State = CircuitState.Open;
                return new RestartVerdict(
                    Decision: RestartDecision.CircuitBreakerTripped,
                    Delay: TimeSpan.Zero,
                    CrashesInWindow: crashesInWindow,
                    State: CircuitState.Open,
                    NextRetryAt: DateTimeOffset.MinValue,
                    Reason: $"{crashesInWindow} 次崩溃集中在 {CrashWindow.TotalMinutes:0} 分钟内，熔断保护已触发，请人工重置。");
            }

            // Exponential backoff: 1s, 2s, 4s, 8s, 16s, capped at 30s.
            //
            // IMPORTANT: we base the exponent on the LIFETIME crash index
            // (state.LifetimeCrashCount), not the sliding window count. If we
            // used crashesInWindow the exponent would reset every time old
            // crashes fall out of the window, which would defeat the backoff
            // (a service that crashes every 5 minutes could never escalate).
            state.LifetimeCrashCount += 1;
            var backoffSeconds = Math.Min(
                (int)Math.Pow(2, state.LifetimeCrashCount - 1),
                MaxBackoffSeconds);
            var delay = TimeSpan.FromSeconds(backoffSeconds);
            state.NextRetryAt = now + delay;

            return new RestartVerdict(
                Decision: RestartDecision.RestartWithBackoff,
                Delay: delay,
                CrashesInWindow: crashesInWindow,
                State: CircuitState.Closed,
                NextRetryAt: state.NextRetryAt.Value,
                Reason: $"第 {state.LifetimeCrashCount}/{CrashThreshold} 次崩溃（5 分钟窗口内 {crashesInWindow} 次），将于 {backoffSeconds} 秒后重试。");
        }
    }

    /// <summary>
    /// Returns the current crash count within the sliding window without
    /// modifying state.
    /// </summary>
    public int GetCrashCount(ServiceKind kind)
    {
        lock (_gate)
        {
            if (!_states.TryGetValue(kind, out var state))
            {
                return 0;
            }

            var now = _time.GetUtcNow();
            return state.CrashTimestamps.Count(timestamp => now - timestamp < CrashWindow);
        }
    }

    /// <summary>
    /// Returns the current circuit state for a service.
    /// </summary>
    public CircuitState GetCircuitState(ServiceKind kind)
    {
        lock (_gate)
        {
            return _states.TryGetValue(kind, out var state) ? state.State : CircuitState.Closed;
        }
    }

    /// <summary>
    /// Resets the watchdog state for a service. Used when user manually
    /// intervenes or when the orchestrator decides to clear the circuit
    /// breaker after a successful restart.
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
        public Queue<DateTimeOffset> CrashTimestamps { get; } = new();
        /// <summary>
        /// Monotonically increasing crash counter used as the exponent base
        /// for the exponential backoff. Reset only when a successful run
        /// survives the full sliding window.
        /// </summary>
        public int LifetimeCrashCount { get; set; }
        public DateTimeOffset? LastSuccessfulStartAt { get; set; }
        public DateTimeOffset? NextRetryAt { get; set; }
        public CircuitState State { get; set; } = CircuitState.Closed;
    }
}
