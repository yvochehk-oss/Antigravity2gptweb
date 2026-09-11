using System.Diagnostics;
using ChengduConstructionController.Models;

namespace ChengduConstructionController.Services;

public sealed class ServiceOrchestrator : IDisposable
{
    private static readonly TimeSpan StartupVerificationTimeout = TimeSpan.FromSeconds(90);
    private static readonly TimeSpan StartupVerificationPoll = TimeSpan.FromMilliseconds(500);
    private static readonly TimeSpan PortReleaseTimeout = TimeSpan.FromSeconds(15);
    private static readonly TimeSpan PortReleasePoll = TimeSpan.FromMilliseconds(200);
    private const int TrackedIdentityAttempts = 6;
    private const int RequiredStableIdentityReads = 2;
    private static readonly TimeSpan TrackedIdentityPoll = TimeSpan.FromMilliseconds(100);
    private readonly ProjectRootResolver _rootResolver;
    private readonly SafeLogger _logger;
    private readonly HealthProbe _healthProbe;
    private readonly ProcessInspector _processInspector = new();
    private readonly WindowsServiceAdapter _adapter;
    private readonly PostgreSqlPortNegotiator _postgresNegotiator;
    private readonly ServiceWatchdog _watchdog = new();
    private readonly IReadOnlyList<ServiceDefinition> _definitions = ServiceCatalog.Create();
    private readonly SemaphoreSlim _operationGate = new(1, 1);
    private readonly object _trackedGate = new();
    private readonly Dictionary<ServiceKind, List<TrackedLaunch>> _tracked = new();
    private bool _disposed;

    public ServiceOrchestrator(ProjectRootResolver rootResolver, SafeLogger logger)
    {
        _rootResolver = rootResolver;
        _logger = logger;
        _healthProbe = new HealthProbe(logger);
        _adapter = new WindowsServiceAdapter(rootResolver, logger);
        _postgresNegotiator = new PostgreSqlPortNegotiator(rootResolver, logger);
    }

    /// <summary>
    /// Exposes the watchdog so UI layer can show consecutive crash counts and
    /// trip state. Read-only access; mutation happens via the orchestrator.
    /// </summary>
    public ServiceWatchdog Watchdog => _watchdog;

    public IReadOnlyList<ServiceDefinition> Definitions => _definitions;
    public bool IsBusy => _operationGate.CurrentCount == 0;
    public string ProjectRoot => _rootResolver.Describe();
    public string LogPath => _logger.LogPath;
    public bool TryEnterOperation() => _operationGate.Wait(0);
    public void LeaveOperation() => _operationGate.Release();

    public async Task<IReadOnlyList<ServiceStatus>> CheckStatusAsync(CancellationToken cancellationToken)
    {
        var tasks = _definitions.Select(definition => CheckOneAsync(definition, cancellationToken));
        return await Task.WhenAll(tasks).ConfigureAwait(false);
    }

    public async Task<OperationResult> StartAllAsync(CancellationToken cancellationToken = default)
    {
        if (!TryEnterOperation())
        {
            return new OperationResult(false, "已有启停操作正在执行，请稍候。");
        }

        try
        {
            return await StartAllCoreAsync(cancellationToken).ConfigureAwait(false);
        }
        finally
        {
            LeaveOperation();
        }
    }

    public async Task<OperationResult> RestartAllAsync(CancellationToken cancellationToken = default)
    {
        if (!TryEnterOperation())
        {
            return new OperationResult(false, "已有启停操作正在执行，请稍候。");
        }

        try
        {
            // Restart deliberately uses the exact same stop-preflight and
            // full-start transaction as StartAll.  Keeping a separate
            // stop-then-start route would reintroduce a window in which an
            // unconfirmed listener could be ignored or an old process reused.
            var started = await StartAllCoreAsync(cancellationToken).ConfigureAwait(false);
            return new OperationResult(started.Success, $"重启结果：{started.Message}");
        }
        finally
        {
            LeaveOperation();
        }
    }

    public async Task<OperationResult> StopAllAsync(CancellationToken cancellationToken = default)
    {
        if (!TryEnterOperation())
        {
            return new OperationResult(false, "已有启停操作正在执行，请稍候。");
        }

        try
        {
            return await StopAllCoreAsync(cancellationToken).ConfigureAwait(false);
        }
        finally
        {
            LeaveOperation();
        }
    }

    private async Task<OperationResult> StartAllCoreAsync(CancellationToken cancellationToken)
    {
        if (!OperatingSystem.IsWindows())
        {
            return new OperationResult(false, "当前不是 Windows，不能启动 Windows 服务。");
        }

        var failed = new List<string>();
        var started = new List<string>();
        var startedThisRound = new List<TrackedLaunch>();
        OperationResult? result = null;

        try
        {
            // Start is a full refresh.  The preflight gathers every listener,
            // PID-file candidate, tracked process, and narrow command-line
            // candidate before the first stop signal is sent.  No old
            // process is reused after this point.
            var cleanup = await StopServicesBeforeStartAsync(cancellationToken).ConfigureAwait(false);
            if (!cleanup.Success)
            {
                result = new OperationResult(false, $"启动未执行：{cleanup.Message}");
            }
            else
            {
                EnsurePostgresRunningAsync(cancellationToken).GetAwaiter().GetResult();
                foreach (var definition in _definitions)
                {
                    cancellationToken.ThrowIfCancellationRequested();

                    try
                    {
                        // Cleanup has already confirmed that every target
                        // port is free.  Always launch a fresh process; there
                        // is intentionally no existing-process reuse branch.
                        var process = _adapter.Start(definition);
                        // Add the raw Process handle to the pending rollback
                        // list before identity probing. Even a process whose
                        // identity is briefly unreadable must remain cleanable
                        // by this operation's final failure path.
                        var launch = CreateTrackedLaunch(definition.Kind, process);
                        startedThisRound.Add(launch);
                        RegisterTrackedLaunch(launch);
                        StartOutputPump(definition, process);

                        var identityResult = await EstablishTrackedLaunchAsync(
                            launch,
                            definition,
                            cancellationToken).ConfigureAwait(false);
                        if (!identityResult.Success)
                        {
                            failed.Add($"{definition.DisplayName}：{identityResult.Message}");
                        }
                        else
                        {
                            started.Add(definition.DisplayName);
                        }
                    }
                    catch (Exception exception) when (exception is InvalidOperationException or PlatformNotSupportedException or System.ComponentModel.Win32Exception)
                    {
                        failed.Add($"{definition.DisplayName}：{exception.Message}");
                        _logger.Error($"{definition.DisplayName}启动失败", exception);
                    }

                    await Task.Delay(TimeSpan.FromMilliseconds(250), cancellationToken).ConfigureAwait(false);
                }
            }

            if (result is null && failed.Count > 0)
            {
                result = new OperationResult(
                    false,
                    $"启动未完成；本轮已提交 {started.Count} 项；失败：{string.Join("；", failed)}");
            }
            else if (result is null)
            {
                var verification = await VerifyStartupAsync(startedThisRound, cancellationToken).ConfigureAwait(false);
                result = verification.Success
                    ? new OperationResult(true, $"已提交 {started.Count} 项全新服务进程。窗口已隐藏。")
                    : new OperationResult(false, $"启动后验证未通过：{verification.Message}");
            }
        }
        catch (OperationCanceledException)
        {
            result = new OperationResult(false, "启动操作已取消");
        }
        catch (Exception exception)
        {
            _logger.Error("启动全部服务失败", exception);
            failed.Add($"控制台异常：{exception.Message}");
            result = new OperationResult(
                false,
                $"启动未完成；本轮已提交 {started.Count} 项；失败：{string.Join("；", failed)}");
        }

        if (result is null)
        {
            // This is defensive only: every normal path above assigns a
            // result, and an unset result must never be reported as success.
            result = new OperationResult(false, "启动未产生可确认结果");
        }

        if (!result.Success)
        {
            // All failed, cancelled, and exceptional startup paths converge
            // here so this round is rolled back exactly once.
            var rollback = RollbackStartedThisRound(startedThisRound);
            var message = $"{result.Message}；{rollback}";
            _logger.Warn(message);
            return new OperationResult(false, message);
        }

        return result;
    }

    private async Task<OperationResult> VerifyStartupAsync(
        List<TrackedLaunch> startedThisRound,
        CancellationToken cancellationToken)
    {
        var stopwatch = System.Diagnostics.Stopwatch.StartNew();
        IReadOnlyList<ServiceStatus> statuses = Array.Empty<ServiceStatus>();
        while (true)
        {
            cancellationToken.ThrowIfCancellationRequested();
            for (var idx = 0; idx < startedThisRound.Count; idx++)
            {
                var launch = startedThisRound[idx];
                try
                {
                    if (launch.Process.HasExited)
                    {
                        var definition = _definitions.First(item => item.Kind == launch.Kind);
                        var verdict = _watchdog.RecordCrashAndDecide(launch.Kind);
                        if (verdict.Decision == ServiceWatchdog.RestartDecision.CircuitBreakerTripped)
                        {
                            _logger.Error($"[Watchdog] {definition.DisplayName} {verdict.Reason}");
                            return new OperationResult(
                                false,
                                $"{definition.DisplayName}启动进程已立即退出（进程 {launch.ProcessId}）；{verdict.Reason}");
                        }

                        // Backoff path: actually consume verdict.Delay, relaunch,
                        // and replace the tracked launch entry in-place.
                        _logger.Warn($"[Watchdog] {definition.DisplayName} {verdict.Reason}");
                        await Task.Delay(verdict.Delay, cancellationToken).ConfigureAwait(false);
                        if (cancellationToken.IsCancellationRequested)
                        {
                            return new OperationResult(false, "重启等待被取消");
                        }

                        var (relaunch, newLaunch) = RestartSingleTrackedLaunch(launch, definition);
                        if (!relaunch.Success || newLaunch is null)
                        {
                            return new OperationResult(
                                false,
                                $"{definition.DisplayName}重启失败（进程 {launch.ProcessId}）：{relaunch.Message}");
                        }

                        startedThisRound[idx] = newLaunch;
                        stopwatch.Restart();
                        continue;
                    }
                }
                catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception)
                {
                    var definition = _definitions.First(item => item.Kind == launch.Kind);
                    var verdict = _watchdog.RecordCrashAndDecide(launch.Kind);
                    if (verdict.Decision == ServiceWatchdog.RestartDecision.CircuitBreakerTripped)
                    {
                        _logger.Error($"[Watchdog] {definition.DisplayName} {verdict.Reason}");
                        return new OperationResult(
                            false,
                            $"无法确认{definition.DisplayName}启动进程仍在运行（进程 {launch.ProcessId}）；{verdict.Reason}");
                    }

                    _logger.Warn($"[Watchdog] {definition.DisplayName} {verdict.Reason}");
                    await Task.Delay(verdict.Delay, cancellationToken).ConfigureAwait(false);
                    if (cancellationToken.IsCancellationRequested)
                    {
                        return new OperationResult(false, "重启等待被取消");
                    }

                    var (relaunch, newLaunch) = RestartSingleTrackedLaunch(launch, definition);
                    if (!relaunch.Success || newLaunch is null)
                    {
                        return new OperationResult(
                            false,
                            $"{definition.DisplayName}重启失败（进程 {launch.ProcessId}）：{relaunch.Message}");
                    }

                    startedThisRound[idx] = newLaunch;
                    stopwatch.Restart();
                    continue;
                }
            }

            statuses = await CheckStatusAsync(cancellationToken).ConfigureAwait(false);
            var conflict = statuses.FirstOrDefault(status =>
                status.Process.PortListening && !status.Process.PortConfirmed);
            if (conflict is not null)
            {
                return new OperationResult(
                    false,
                    $"{conflict.Definition.DisplayName}目标端口由未确认进程监听，视为端口冲突");
            }

            var allReady = _definitions.All(definition =>
            {
                var status = statuses.FirstOrDefault(item => item.Definition.Kind == definition.Kind);
                return status is not null && IsStartupReady(status);
            });

            if (allReady)
            {
                // Mark each successfully started service as healthy in the watchdog.
                foreach (var launch in startedThisRound)
                {
                    _watchdog.RecordSuccessfulStart(launch.Kind);
                }
                return new OperationResult(true, "五项服务健康、目标端口监听和项目归属均已确认");
            }

            if (stopwatch.Elapsed >= StartupVerificationTimeout)
            {
                var pending = statuses
                    .Where(status => !IsStartupReady(status))
                    .Select(status => $"{status.Definition.DisplayName}：{status.StateText}")
                    .ToArray();

                // Watchdog: record crashes for services that failed to verify.
                foreach (var launch in startedThisRound)
                {
                    var launchStatus = statuses.FirstOrDefault(s => s.Definition.Kind == launch.Kind);
                    if (launchStatus is null || !IsStartupReady(launchStatus))
                    {
                        var verdict = _watchdog.RecordCrashAndDecide(launch.Kind);
                        _logger.Warn($"[Watchdog] {launchStatus?.Definition.DisplayName ?? launch.Kind.ToString()} {verdict.Reason}");
                    }
                }

                return new OperationResult(
                    false,
                    $"等待 {StartupVerificationTimeout.TotalSeconds:0} 秒后仍未完成五项服务验证：{string.Join("；", pending)}");
            }

            await Task.Delay(StartupVerificationPoll, cancellationToken).ConfigureAwait(false);
        }
    }

    private (OperationResult Result, TrackedLaunch? NewLaunch) RestartSingleTrackedLaunch(TrackedLaunch launch, ServiceDefinition definition)
    {
        Untrack(launch);
        try
        {
            launch.Process.Dispose();
        }
        catch
        {
            // best effort
        }

        Process? newProcess;
        try
        {
            newProcess = _adapter.Start(definition);
        }
        catch (Exception ex)
        {
            return (new OperationResult(false, $"重新启动失败：{ex.Message}"), null);
        }

        var newLaunch = CreateTrackedLaunch(definition.Kind, newProcess);
        RegisterTrackedLaunch(newLaunch);
        StartOutputPump(definition, newProcess);

        return (new OperationResult(true, $"已重启，进程 {newProcess.Id}"), newLaunch);
    }

    private static bool IsStartupReady(ServiceStatus status)
    {
        return status.HttpHealthy
            && status.ProcessConfirmed
            && status.Process.PortConfirmed
            && (status.Definition.Kind != ServiceKind.LocalModel || status.Http.ModelReady);
    }

    private async Task<OperationResult> StopAllCoreAsync(CancellationToken cancellationToken)
    {
        if (!OperatingSystem.IsWindows())
        {
            return new OperationResult(false, "当前不是 Windows，不能停止 Windows 服务。");
        }

        var preflight = BuildCleanupPlan();
        if (!preflight.Success)
        {
            return new OperationResult(false, $"停止已中止：{preflight.Message}");
        }

        return await ExecuteCleanupPlanAsync(preflight.Targets, cancellationToken).ConfigureAwait(false);
    }

    private async Task<OperationResult> StopServicesBeforeStartAsync(CancellationToken cancellationToken)
    {
        if (!OperatingSystem.IsWindows())
        {
            return new OperationResult(false, "当前不是 Windows，不能停止 Windows 服务。");
        }

        // This method is deliberately shared by StartAll and RestartAll.
        // BuildCleanupPlan must finish for all five services before
        // ExecuteCleanupPlanAsync is allowed to send even the first signal.
        var preflight = BuildCleanupPlan();
        if (!preflight.Success)
        {
            return new OperationResult(false, $"启动前清理已中止：{preflight.Message}");
        }

        return await ExecuteCleanupPlanAsync(preflight.Targets, cancellationToken).ConfigureAwait(false);
    }

    private CleanupPreflightResult BuildCleanupPlan()
    {
        var portTables = _definitions.ToDictionary(
            definition => definition.Kind,
            definition => ProcessInspector.GetListeningProcessIdsResult(definition.Port));
        var failedPortTables = portTables
            .Where(pair => !pair.Value.Success)
            .Select(pair => $"{_definitions.First(definition => definition.Kind == pair.Key).DisplayName}端口：{pair.Value.Detail}")
            .ToArray();
        if (failedPortTables.Length > 0)
        {
            return CleanupPreflightResult.Failed(
                $"五项服务端口表读取失败：{string.Join("；", failedPortTables)}；未发送停止信号");
        }

        if (_rootResolver.Root is null)
        {
            return CleanupPreflightResult.Failed("未找到可用的 V3.1 项目根目录，未发送停止信号");
        }

        var targets = new List<CleanupTarget>();
        var refusals = new List<string>();
        foreach (var definition in _definitions)
        {
            var trustedIds = GetTrackedProcessIds(definition.Kind);
            var pidFile = ReadPidFileEvidence(definition);
            if (pidFile.Exists && !pidFile.IsValid)
            {
                refusals.Add($"{definition.DisplayName}：{pidFile.Detail}，未发送停止信号");
                continue;
            }

            var pidFileIds = pidFile.ProcessId.HasValue
                ? new[] { pidFile.ProcessId.Value }
                : Array.Empty<int>();
            var evidence = _processInspector.InspectCleanupCandidates(
                definition,
                _rootResolver.Root,
                trustedIds,
                pidFileIds,
                portTables[definition.Kind]);

            foreach (var candidate in evidence.Candidates)
            {
                if (!candidate.ProcessExists)
                {
                    continue;
                }

                if (candidate.ProcessId == Environment.ProcessId)
                {
                    refusals.Add($"{definition.DisplayName}：停止候选 PID 是控制台自身，未发送停止信号");
                    continue;
                }

                if (!candidate.IsConfirmed || candidate.Identity is null)
                {
                    var identityDetail = candidate.Identity is null
                        ? "进程身份无法读取"
                        : "命令、工作目录、可执行路径、项目标记或精确端口无法同时确认归属";
                    refusals.Add(
                        $"{definition.DisplayName}：{candidate.Source} PID {candidate.ProcessId} {identityDetail}，未发送停止信号");
                    continue;
                }

                targets.Add(new CleanupTarget(
                    definition,
                    candidate.Identity,
                    trustedIds,
                    candidate.IsListening,
                    candidate.Source));
            }
        }

        if (refusals.Count > 0)
        {
            return CleanupPreflightResult.Failed(
                $"发现未确认的项目监听者或进程候选：{string.Join("；", refusals)}");
        }

        var uniqueTargets = targets
            .GroupBy(target => (target.Definition.Kind, target.Identity.ProcessId))
            .Select(group => group.First())
            .ToArray();
        return CleanupPreflightResult.Succeeded(uniqueTargets);
    }

    private async Task<OperationResult> ExecuteCleanupPlanAsync(
        IReadOnlyList<CleanupTarget> targets,
        CancellationToken cancellationToken)
    {
        var stopped = new List<string>();
        foreach (var target in targets)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var result = StopOwnedProcess(
                target.Identity,
                target.Definition,
                target.TrustedProcessIds,
                allowCommandPortEvidence: true);
            if (!result.Success)
            {
                return new OperationResult(
                    false,
                    $"已停止 {stopped.Distinct().Count()} 项；{target.Definition.DisplayName} PID {target.Identity.ProcessId} 未能安全停止：{result.Message}。数据库保持运行。");
            }

            stopped.Add(target.Definition.DisplayName);
        }

        var released = await WaitForPortsReleasedAsync(cancellationToken).ConfigureAwait(false);
        if (!released.Success)
        {
            return released;
        }

        // PostgreSQL is intentionally absent from Definitions and is never
        // addressed by this operation. It remains available to other services.
        return new OperationResult(
            true,
            stopped.Count == 0
                ? "没有发现可安全停止的本项目服务；目标端口已释放；数据库保持运行。"
                : $"已停止 {stopped.Distinct().Count()} 项本项目服务；目标端口已释放；数据库保持运行。");
    }

    private async Task<OperationResult> WaitForPortsReleasedAsync(CancellationToken cancellationToken)
    {
        var stopwatch = System.Diagnostics.Stopwatch.StartNew();
        while (true)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var portTables = _definitions
                .Select(definition => (
                    definition,
                    result: ProcessInspector.GetListeningProcessIdsResult(definition.Port)))
                .ToArray();
            var failedPortTables = portTables
                .Where(item => !item.result.Success)
                .Select(item => $"{item.definition.DisplayName}端口：{item.result.Detail}")
                .ToArray();
            if (failedPortTables.Length > 0)
            {
                return new OperationResult(
                    false,
                    $"无法确认五项服务端口是否已释放：{string.Join("；", failedPortTables)}；未启动新服务；数据库保持运行。");
            }

            var listening = portTables
                .SelectMany(item => item.result.ProcessIds
                    .Select(processId => (item.definition, processId)))
                .ToArray();
            if (listening.Length == 0)
            {
                return new OperationResult(true, "五项服务目标端口已全部释放");
            }

            if (stopwatch.Elapsed >= PortReleaseTimeout)
            {
                var detail = string.Join(
                    "；",
                    listening.Select(item => $"{item.definition.DisplayName}端口 {item.definition.Port}（进程 {item.processId}）"));
                return new OperationResult(
                    false,
                    $"目标端口在 {PortReleaseTimeout.TotalSeconds:0} 秒内未全部释放：{detail}；未启动新服务；数据库保持运行。");
            }

            await Task.Delay(PortReleasePoll, cancellationToken).ConfigureAwait(false);
        }
    }

    private async Task<ServiceStatus> CheckOneAsync(ServiceDefinition definition, CancellationToken cancellationToken)
    {
        var launchIds = new HashSet<int>(GetTrackedProcessIds(definition.Kind));
        launchIds.UnionWith(ReadValidatedPidFileIds(definition));
        var http = await _healthProbe.ProbeAsync(definition, cancellationToken).ConfigureAwait(false);
        var process = _processInspector.Inspect(definition, _rootResolver.Root, launchIds);
        var httpHealthy = http.Healthy && (definition.Kind != ServiceKind.LocalModel || http.ModelReady);

        var condition = ServiceCondition.Unknown;
        var state = "正在检查";
        if (httpHealthy && process.BelongsToProject && process.PortConfirmed)
        {
            condition = http.Slow ? ServiceCondition.Degraded : ServiceCondition.Healthy;
            state = http.Slow ? "响应缓慢" : "正常";
        }
        else if (httpHealthy)
        {
            condition = ServiceCondition.Unavailable;
            state = process.PortListening
                ? "健康接口已响应，但目标端口归属未确认"
                : "健康接口已响应，但项目端口证据缺失";
        }
        else if (process.BelongsToProject && process.PortConfirmed)
        {
            condition = http.Responded && http.StatusCode is >= 400 and < 500
                ? ServiceCondition.Unavailable
                : ServiceCondition.Starting;
            state = condition == ServiceCondition.Starting ? "正在启动" : "服务不可用";
        }
        else if (process.BelongsToProject)
        {
            condition = ServiceCondition.Starting;
            state = "项目进程已确认，正在等待目标端口";
        }
        else if (process.Found || http.Responded)
        {
            condition = ServiceCondition.Unavailable;
            state = process.Found ? "接口有响应，进程待确认" : "服务不可用";
        }
        else
        {
            condition = ServiceCondition.Stopped;
            state = "已停止";
        }

        var detail = $"{http.Detail}；{process.Detail}；{http.Elapsed.TotalMilliseconds:0} 毫秒检查。";
        return new ServiceStatus(
            definition,
            condition,
            state,
            httpHealthy,
            process.BelongsToProject,
            http,
            process,
            DateTimeOffset.Now,
            detail);
    }

    private OperationResult StopOwnedProcess(
        ProcessIdentity? identity,
        ServiceDefinition definition,
        IReadOnlySet<int> trustedIds,
        TrackedLaunch? trackedLaunch = null,
        bool allowCommandPortEvidence = false)
    {
        if (trackedLaunch is not null)
        {
            // A tracked rollback must use the original Process handle. It
            // must never re-open the PID, because that PID may now belong to
            // an unrelated process. The direct path also works before LISTEN.
            return TerminateDirectLaunch(trackedLaunch, definition);
        }

        if (identity is null)
        {
            return new OperationResult(false, "进程归属无法确认，已跳过");
        }

        try
        {
            using var process = Process.GetProcessById(identity.ProcessId);
            if (process.HasExited)
            {
                return new OperationResult(true, "进程已退出");
            }

            // Re-check ownership immediately before touching the process. A
            // recycled PID must never turn into a kill of an unrelated process.
            var currentIdentity = _processInspector.ReadIdentity(identity.ProcessId);
            var expandedTrustedIds = _processInspector.ExpandProcessTree(trustedIds);
            if (currentIdentity is null)
            {
                return new OperationResult(false, "进程归属无法确认，已跳过");
            }

            if (!CanSafelyStop(
                    identity,
                    currentIdentity,
                    definition,
                    expandedTrustedIds,
                    trackedLaunch,
                    allowCommandPortEvidence,
                    out var reason))
            {
                return new OperationResult(false, reason ?? "进程归属无法确认，已跳过");
            }

            try
            {
                if (process.MainWindowHandle != IntPtr.Zero)
                {
                    process.CloseMainWindow();
                    process.WaitForExit(1200);
                }
            }
            catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception)
            {
                _logger.Warn($"{definition.DisplayName}未能发送优雅停止信号：{exception.Message}");
            }

            if (!process.HasExited)
            {
                // Re-read all identity and port evidence after the graceful
                // stop window too; both the process and its PID may have
                // changed while waiting.
                var beforeKill = _processInspector.ReadIdentity(identity.ProcessId);
                if (beforeKill is null
                    || !CanSafelyStop(
                        identity,
                        beforeKill,
                        definition,
                        expandedTrustedIds,
                        trackedLaunch,
                        allowCommandPortEvidence,
                        out reason))
                {
                    return new OperationResult(false, reason ?? "进程身份或端口证据已变化，已跳过");
                }

                // Kill is permitted only after the current identity and live
                // port ownership passed every project validation above.
                process.Kill(entireProcessTree: true);
                process.WaitForExit(2500);
            }

            return process.HasExited
                ? new OperationResult(true, "已停止")
                : new OperationResult(false, "停止超时，已保留进程");
        }
        catch (ArgumentException)
        {
            return new OperationResult(true, "进程已退出");
        }
        catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception or UnauthorizedAccessException)
        {
            _logger.Error($"停止{definition.DisplayName}失败", exception);
            return new OperationResult(false, "系统拒绝停止，已跳过");
        }
    }

    private bool CanSafelyStop(
        ProcessIdentity expectedIdentity,
        ProcessIdentity currentIdentity,
        ServiceDefinition definition,
        IReadOnlySet<int> trustedIds,
        TrackedLaunch? trackedLaunch,
        bool allowCommandPortEvidence,
        out string? reason)
    {
        reason = null;
        if (!ProcessInspector.SameProcessIdentity(expectedIdentity, currentIdentity))
        {
            reason = "进程身份已变化或 PID 已复用，已跳过";
            return false;
        }

        if (trackedLaunch is not null && !MatchesTrackedLaunch(trackedLaunch, currentIdentity))
        {
            reason = "本轮启动记录与当前进程不一致，可能发生 PID 复用，已跳过";
            return false;
        }

        if (!ProcessInspector.IsProjectProcess(currentIdentity, definition, _rootResolver.Root, trustedIds))
        {
            reason = "进程项目归属无法确认，已跳过";
            return false;
        }

        // Ordinary user-requested stop requires live target-port ownership.
        // Rollback is the narrow exception: its immutable launch record can
        // prove ownership even before the process has reached LISTEN.
        if (trackedLaunch is null
            && !HasLivePortEvidence(currentIdentity.ProcessId, definition.Port)
            && !allowCommandPortEvidence)
        {
            reason = "端口归属无法确认，已跳过";
            return false;
        }

        return true;
    }

    private bool HasLivePortEvidence(int processId, int port)
    {
        var listeningIds = ProcessInspector.GetListeningProcessIds(port);
        if (listeningIds.Contains(processId))
        {
            return true;
        }

        var processTree = _processInspector.ExpandProcessTree(new HashSet<int> { processId });
        return processTree.Any(listeningIds.Contains);
    }

    private static bool MatchesTrackedLaunch(TrackedLaunch launch, ProcessIdentity currentIdentity)
    {
        if (string.IsNullOrWhiteSpace(launch.ExpectedExecutablePath)
            || string.IsNullOrWhiteSpace(launch.ExpectedWorkingDirectory)
            || launch.ExpectedArguments.Count == 0
            || !launch.StartedAt.HasValue
            || !currentIdentity.StartedAt.HasValue
            || launch.StartedAt.Value.UtcDateTime != currentIdentity.StartedAt.Value.UtcDateTime)
        {
            return false;
        }

        if (!SamePath(launch.ExpectedExecutablePath, currentIdentity.ExecutablePath)
            || !SamePath(launch.ExpectedWorkingDirectory, currentIdentity.WorkingDirectory))
        {
            return false;
        }

        var commandLine = NormalizeProcessValue(currentIdentity.CommandLine);
        return launch.ExpectedArguments.All(argument =>
            !string.IsNullOrWhiteSpace(argument)
            && commandLine.Contains(NormalizeProcessValue(argument), StringComparison.OrdinalIgnoreCase));
    }

    private static bool SamePath(string left, string right)
    {
        return string.Equals(
            NormalizeProcessValue(left).TrimEnd('\\'),
            NormalizeProcessValue(right).TrimEnd('\\'),
            StringComparison.OrdinalIgnoreCase);
    }

    private static string NormalizeProcessValue(string value) => value.Replace('/', '\\').Trim().Trim('"');

    private IReadOnlySet<int> ReadValidatedPidFileIds(ServiceDefinition definition)
    {
        var evidence = ReadPidFileEvidence(definition);
        if (!evidence.IsValid || !evidence.ProcessId.HasValue)
        {
            return new HashSet<int>();
        }

        var identity = _processInspector.ReadIdentity(evidence.ProcessId.Value);
        return identity is not null
            && ProcessInspector.IsProjectProcess(identity, definition, _rootResolver.Root, new HashSet<int>())
            ? new HashSet<int> { evidence.ProcessId.Value }
            : new HashSet<int>();
    }

    private PidFileEvidence ReadPidFileEvidence(ServiceDefinition definition)
    {
        if (_rootResolver.Root is null)
        {
            return PidFileEvidence.Missing;
        }

        var fileName = definition.Kind switch
        {
            ServiceKind.LocalModel => ".local_llm.pid",
            ServiceKind.Tax => ".tax.pid",
            ServiceKind.Rag => ".rag.pid",
            ServiceKind.Idp => ".idp.pid",
            ServiceKind.Boss => ".app.pid",
            _ => null,
        };
        if (fileName is null)
        {
            return PidFileEvidence.Missing;
        }

        var pidPath = _rootResolver.ResolvePath(fileName);
        if (string.IsNullOrWhiteSpace(pidPath) || !File.Exists(pidPath))
        {
            return PidFileEvidence.Missing;
        }

        try
        {
            var content = File.ReadAllText(pidPath).Trim();
            if (!int.TryParse(content, out var pid) || pid <= 4)
            {
                return PidFileEvidence.Invalid("PID 文件格式无效");
            }

            return PidFileEvidence.Valid(pid);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            return PidFileEvidence.Invalid("PID 文件无法读取");
        }
    }

    private string RollbackStartedThisRound(IReadOnlyList<TrackedLaunch> launches)
    {
        if (launches.Count == 0)
        {
            return "回滚：本轮没有已提交的新进程，未触碰原先已运行服务或 PostgreSQL";
        }

        var rolledBack = new List<string>();
        var skipped = new List<string>();
        foreach (var launch in launches.Reverse())
        {
            var definition = _definitions.First(item => item.Kind == launch.Kind);

            // 保护策略：若大模型（8930）已经成功加载并处于健康状态，回滚时豁免杀死，避免重复耗费数十秒重新加载 2.6GB 权重
            if (launch.Kind == ServiceKind.LocalModel)
            {
                try
                {
                    var probeResult = _healthProbe.ProbeAsync(definition, CancellationToken.None).GetAwaiter().GetResult();
                    if (probeResult.Healthy)
                    {
                        _logger.Info("回滚安全保护：本地大模型 (8930) 运行健康，已予以保留豁免，无需重新加载模型。");
                        skipped.Add($"{definition.DisplayName}（已就绪并予以保留）");
                        continue;
                    }
                }
                catch
                {
                }
            }

            var result = StopTrackedLaunch(launch, definition);
            if (result.Success)
            {
                rolledBack.Add(definition.DisplayName);
            }
            else
            {
                skipped.Add($"{definition.DisplayName}（进程 {launch.ProcessId}）：{result.Message}");
            }
        }

        var message = $"回滚：已安全停止 {rolledBack.Distinct().Count()} 项本轮新启动服务";
        if (skipped.Count > 0)
        {
            message += $"；未回滚：{string.Join("；", skipped)}";
        }

        return message + "；原先已运行服务和 PostgreSQL 未纳入回滚";
    }

    private OperationResult StopTrackedLaunch(TrackedLaunch launch, ServiceDefinition definition)
    {
        var result = StopOwnedProcess(
            launch.Identity,
            definition,
            new HashSet<int> { launch.ProcessId },
            launch);
        if (result.Success)
        {
            Untrack(launch);
        }

        return result;
    }

    private async Task<OperationResult> EstablishTrackedLaunchAsync(
        TrackedLaunch launch,
        ServiceDefinition definition,
        CancellationToken cancellationToken)
    {
        ProcessIdentity? previousIdentity = null;
        var stableReads = 0;
        var lastIssue = "启动后无法读取完整进程身份";

        for (var attempt = 0; attempt < TrackedIdentityAttempts; attempt++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            try
            {
                // Read all lifetime evidence from the original Process handle
                // before observing any PID-derived identity. This prevents a
                // recycled PID from being attached to this launch record.
                if (launch.Process.HasExited)
                {
                    return new OperationResult(false, $"启动进程在身份建立前已退出（进程 {launch.ProcessId}）");
                }

                if (launch.Process.Id != launch.ProcessId)
                {
                    return new OperationResult(false, $"启动进程 PID 已变化（进程 {launch.ProcessId}）");
                }

                var originalStartedAt = new DateTimeOffset(launch.Process.StartTime);
                if (launch.StartedAt.HasValue
                    && launch.StartedAt.Value.UtcDateTime != originalStartedAt.UtcDateTime)
                {
                    return new OperationResult(false, $"启动进程启动时间已变化（进程 {launch.ProcessId}）");
                }

                launch.StartedAt ??= originalStartedAt;
                var identity = _processInspector.ReadIdentity(launch.Process);
                if (identity is not null
                    && identity.ProcessId == launch.ProcessId
                    && identity.StartedAt.HasValue
                    && identity.StartedAt.Value.UtcDateTime == originalStartedAt.UtcDateTime
                    && MatchesTrackedLaunch(launch, identity)
                    && ProcessInspector.IsProjectProcess(
                        identity,
                        definition,
                        _rootResolver.Root,
                        new HashSet<int> { launch.ProcessId }))
                {
                    stableReads = previousIdentity is not null
                        && ProcessInspector.SameProcessIdentity(previousIdentity, identity)
                        ? stableReads + 1
                        : 1;
                    previousIdentity = identity;
                    if (stableReads >= RequiredStableIdentityReads)
                    {
                        launch.Identity = identity;
                        return new OperationResult(true, "已建立稳定进程身份");
                    }

                    lastIssue = $"进程身份正在稳定确认（第 {stableReads}/{RequiredStableIdentityReads} 次一致读取）";
                }
                else
                {
                    stableReads = 0;
                    previousIdentity = null;
                    lastIssue = "启动后进程身份或预期启动信息尚未一致";
                }
            }
            catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception or NotSupportedException or UnauthorizedAccessException)
            {
                stableReads = 0;
                previousIdentity = null;
                lastIssue = $"启动后进程身份暂不可读取：{exception.Message}";
            }

            if (attempt + 1 < TrackedIdentityAttempts)
            {
                await Task.Delay(TrackedIdentityPoll, cancellationToken).ConfigureAwait(false);
            }
        }

        return new OperationResult(
            false,
            $"{lastIssue}；身份建立失败，将由原始 Process 句柄执行本轮回滚（进程 {launch.ProcessId}）");
    }

    private OperationResult TerminateDirectLaunch(TrackedLaunch launch, ServiceDefinition definition)
    {
        // This is the only rollback path for a tracked launch. It deliberately
        // uses the original Process handle rather than Process.GetProcessById.
        // Consequently it remains safe before LISTEN and cannot kill a PID
        // that was recycled after the original process exited.
        var process = launch.Process;
        try
        {
            if (process.HasExited)
            {
                return new OperationResult(true, "本轮进程已退出");
            }

            if (!MatchesOriginalLaunchHandle(launch, process, definition, out var reason))
            {
                return new OperationResult(false, reason ?? "原始 Process 句柄身份无法确认，已跳过");
            }

            if (!ValidateTrackedLaunchIdentity(launch, process, definition, out reason))
            {
                return new OperationResult(false, reason ?? "本轮进程身份或预期启动信息已变化，已跳过");
            }

            try
            {
                if (process.MainWindowHandle != IntPtr.Zero)
                {
                    process.CloseMainWindow();
                    process.WaitForExit(1200);
                }
            }
            catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception)
            {
                _logger.Warn($"{definition.DisplayName}回滚时未能发送优雅停止信号：{exception.Message}");
            }

            if (process.HasExited)
            {
                return new OperationResult(true, "本轮进程已退出");
            }

            // Re-check the original handle immediately before Kill. A process
            // that exits here stays harmlessly attached to this handle; a
            // recycled PID can never be reached through it.
            if (!MatchesOriginalLaunchHandle(launch, process, definition, out reason)
                || !ValidateTrackedLaunchIdentity(launch, process, definition, out reason))
            {
                return new OperationResult(false, reason ?? "原始进程身份或预期启动信息已变化，已跳过");
            }

            process.Kill(entireProcessTree: true);
            process.WaitForExit(2500);
            return process.HasExited
                ? new OperationResult(true, "已安全停止本轮新启动进程")
                : new OperationResult(false, "回滚停止超时，已保留本轮进程");
        }
        catch (ArgumentException)
        {
            // The original handle no longer represents a running process.
            return new OperationResult(true, "本轮进程已退出");
        }
        catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception or UnauthorizedAccessException)
        {
            _logger.Error($"回滚停止{definition.DisplayName}失败", exception);
            return new OperationResult(false, "系统拒绝通过原始 Process 句柄停止，已跳过");
        }
    }

    private bool MatchesOriginalLaunchHandle(
        TrackedLaunch launch,
        Process process,
        ServiceDefinition definition,
        out string? reason)
    {
        reason = null;
        try
        {
            if (process.Id != launch.ProcessId)
            {
                reason = "原始 Process 句柄 PID 已变化，已跳过";
                return false;
            }

            if (!launch.StartedAt.HasValue)
            {
                reason = "本轮进程启动时间无法确认，已跳过";
                return false;
            }

            var startedAt = new DateTimeOffset(process.StartTime);
            if (startedAt.UtcDateTime != launch.StartedAt.Value.UtcDateTime)
            {
                reason = "本轮进程启动时间已变化，已跳过";
                return false;
            }

            var executablePath = process.MainModule?.FileName ?? string.Empty;
            if (!SamePath(launch.ExpectedExecutablePath, executablePath))
            {
                reason = "本轮进程可执行文件已变化，已跳过";
                return false;
            }

            var processName = process.ProcessName;
            var executableName = Path.GetFileName(executablePath);
            if (!definition.AllowedProcessNames.Contains(processName)
                && !definition.AllowedProcessNames.Contains(executableName))
            {
                reason = "本轮进程名称无法确认属于项目，已跳过";
                return false;
            }

            var expectedWorkingDirectory = _rootResolver.ResolvePath(definition.RelativeWorkingDirectory);
            if (string.IsNullOrWhiteSpace(expectedWorkingDirectory)
                || !SamePath(expectedWorkingDirectory, launch.ExpectedWorkingDirectory))
            {
                reason = "本轮预期工作目录已变化，已跳过";
                return false;
            }

            var startInfo = process.StartInfo;
            if (!SamePath(startInfo.FileName, launch.ExpectedExecutablePath)
                || !SamePath(startInfo.WorkingDirectory, launch.ExpectedWorkingDirectory)
                || !ArgumentsMatch(startInfo.ArgumentList, launch.ExpectedArguments))
            {
                reason = "本轮预期启动参数已变化，已跳过";
                return false;
            }

            return launch.ExpectedArguments.Count > 0;
        }
        catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception or NotSupportedException or UnauthorizedAccessException)
        {
            reason = $"无法读取原始 Process 句柄身份：{exception.Message}";
            return false;
        }
    }

    private bool ValidateTrackedLaunchIdentity(
        TrackedLaunch launch,
        Process process,
        ServiceDefinition definition,
        out string? reason)
    {
        reason = null;
        var identity = _processInspector.ReadIdentity(process);
        if (identity is null)
        {
            // The raw handle checks above still prove PID, start time, path,
            // process name, working directory, and expected launch metadata.
            // Native command-line access can be denied for a live process; do
            // not re-open the PID merely to manufacture a different owner.
            return true;
        }

        if (!IsCompatibleTrackedIdentity(launch, identity))
        {
            reason = "本轮进程身份或 PID 已复用，已跳过";
            return false;
        }

        if (HasCompleteIdentity(identity)
            && !ProcessInspector.IsProjectProcess(
                identity,
                definition,
                _rootResolver.Root,
                new HashSet<int> { launch.ProcessId }))
        {
            reason = "本轮进程项目归属无法确认，已跳过";
            return false;
        }

        return true;
    }

    private static bool IsCompatibleTrackedIdentity(TrackedLaunch launch, ProcessIdentity identity)
    {
        if (identity.ProcessId != launch.ProcessId
            || (launch.StartedAt.HasValue
                && identity.StartedAt.HasValue
                && launch.StartedAt.Value.UtcDateTime != identity.StartedAt.Value.UtcDateTime))
        {
            return false;
        }

        if (!string.IsNullOrWhiteSpace(identity.ExecutablePath)
            && !SamePath(launch.ExpectedExecutablePath, identity.ExecutablePath))
        {
            return false;
        }

        if (!string.IsNullOrWhiteSpace(identity.WorkingDirectory)
            && !SamePath(launch.ExpectedWorkingDirectory, identity.WorkingDirectory))
        {
            return false;
        }

        var commandLine = NormalizeProcessValue(identity.CommandLine);
        return string.IsNullOrWhiteSpace(commandLine)
            || launch.ExpectedArguments.All(argument =>
                !string.IsNullOrWhiteSpace(argument)
                && commandLine.Contains(NormalizeProcessValue(argument), StringComparison.OrdinalIgnoreCase));
    }

    private static bool HasCompleteIdentity(ProcessIdentity identity)
    {
        return identity.StartedAt.HasValue
            && !string.IsNullOrWhiteSpace(identity.ProcessName)
            && !string.IsNullOrWhiteSpace(identity.ExecutablePath)
            && !string.IsNullOrWhiteSpace(identity.CommandLine)
            && !string.IsNullOrWhiteSpace(identity.WorkingDirectory);
    }

    private static bool ArgumentsMatch(
        IEnumerable<string> actual,
        IReadOnlyList<string> expected)
    {
        var actualValues = actual.ToArray();
        return actualValues.Length == expected.Count
            && actualValues.Zip(expected).All(pair => string.Equals(
                NormalizeProcessValue(pair.First),
                NormalizeProcessValue(pair.Second),
                StringComparison.OrdinalIgnoreCase));
    }

    private IReadOnlySet<int> GetTrackedProcessIds(ServiceKind kind)
    {
        lock (_trackedGate)
        {
            if (!_tracked.TryGetValue(kind, out var launches))
            {
                return new HashSet<int>();
            }

            var active = new HashSet<int>();
            foreach (var launch in launches.ToArray())
            {
                try
                {
                    if (!launch.Process.HasExited)
                    {
                        active.Add(launch.Process.Id);
                    }
                    else
                    {
                        launches.Remove(launch);
                    }
                }
                catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception)
                {
                    launches.Remove(launch);
                }
            }

            return active;
        }
    }

    private TrackedLaunch CreateTrackedLaunch(ServiceKind kind, Process process)
    {
        var startInfo = process.StartInfo;
        return new TrackedLaunch(
            kind,
            process,
            process.Id,
            null,
            null,
            NormalizeProcessValue(startInfo.FileName),
            startInfo.ArgumentList.ToArray(),
            NormalizeProcessValue(startInfo.WorkingDirectory),
            DateTimeOffset.Now);
    }

    private void RegisterTrackedLaunch(TrackedLaunch launch)
    {
        lock (_trackedGate)
        {
            if (!_tracked.TryGetValue(launch.Kind, out var launches))
            {
                launches = new List<TrackedLaunch>();
                _tracked[launch.Kind] = launches;
            }

            launches.Add(launch);
        }

        var process = launch.Process;
        process.Exited += (_, _) => _logger.Info($"{_definitions.First(item => item.Kind == launch.Kind).DisplayName}启动进程已退出（进程 {launch.ProcessId}）");
    }

    private void Untrack(TrackedLaunch launch)
    {
        lock (_trackedGate)
        {
            if (!_tracked.TryGetValue(launch.Kind, out var launches))
            {
                return;
            }

            launches.Remove(launch);
            if (launches.Count == 0)
            {
                _tracked.Remove(launch.Kind);
            }
        }
    }

    private sealed record CleanupTarget(
        ServiceDefinition Definition,
        ProcessIdentity Identity,
        IReadOnlySet<int> TrustedProcessIds,
        bool IsListening,
        string Source);

    private sealed record CleanupPreflightResult(
        bool Success,
        string Message,
        IReadOnlyList<CleanupTarget> Targets)
    {
        public static CleanupPreflightResult Failed(string message) =>
            new(false, message, Array.Empty<CleanupTarget>());

        public static CleanupPreflightResult Succeeded(IReadOnlyList<CleanupTarget> targets) =>
            new(true, "所有停止候选的项目归属已确认", targets);
    }

    private sealed record PidFileEvidence(
        bool Exists,
        bool IsValid,
        int? ProcessId,
        string Detail)
    {
        public static PidFileEvidence Missing => new(false, true, null, "PID 文件不存在");

        public static PidFileEvidence Valid(int processId) =>
            new(true, true, processId, "PID 文件有效");

        public static PidFileEvidence Invalid(string detail) =>
            new(true, false, null, detail);
    }

    private void StartOutputPump(ServiceDefinition definition, Process process)
    {
        _ = PumpAsync(process.StandardOutput, definition.DisplayName);
        _ = PumpAsync(process.StandardError, definition.DisplayName);
    }

    private async Task PumpAsync(StreamReader reader, string serviceName)
    {
        try
        {
            while (await reader.ReadLineAsync().ConfigureAwait(false) is { } line)
            {
                if (!string.IsNullOrWhiteSpace(line))
                {
                    _logger.Info($"{serviceName}：{line}");
                }
            }
        }
        catch (Exception exception) when (exception is IOException or ObjectDisposedException or InvalidOperationException)
        {
            // Process exit and app shutdown close the redirected stream normally.
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        _healthProbe.Dispose();
        lock (_trackedGate)
        {
            foreach (var launch in _tracked.Values.SelectMany(items => items))
            {
                launch.Process.Dispose();
            }

            _tracked.Clear();
        }

        _operationGate.Dispose();
    }

    private async Task<int> EnsurePostgresRunningAsync(CancellationToken cancellationToken)
    {
        try
        {
            // PostgreSqlPortNegotiator is the SOLE source of truth for the
            // bundled database endpoint. Any other code path MUST read
            // runtime\state\postgres.json rather than hard-coding a port.
            var activePort = await _postgresNegotiator
                .EnsureRunningAsync(cancellationToken)
                .ConfigureAwait(false);

            // Propagate the negotiated port to the adapter so Python services
            // are launched with the correct DATABASE_PORT / DATABASE_URL env vars.
            _adapter.DatabasePort = activePort;
            return activePort;
        }
        catch (Exception ex)
        {
            _logger.Warn($"自动拉起 PostgreSQL 提示: {ex.Message}");
            return _adapter.DatabasePort;
        }
    }

    private sealed class TrackedLaunch
    {
        public TrackedLaunch(
            ServiceKind kind,
            Process process,
            int processId,
            ProcessIdentity? identity,
            DateTimeOffset? startedAt,
            string expectedExecutablePath,
            IReadOnlyList<string> expectedArguments,
            string expectedWorkingDirectory,
            DateTimeOffset trackedAt)
        {
            Kind = kind;
            Process = process;
            ProcessId = processId;
            Identity = identity;
            StartedAt = startedAt;
            ExpectedExecutablePath = expectedExecutablePath;
            ExpectedArguments = expectedArguments;
            ExpectedWorkingDirectory = expectedWorkingDirectory;
            TrackedAt = trackedAt;
        }

        public ServiceKind Kind { get; }
        public Process Process { get; }
        public int ProcessId { get; }
        public ProcessIdentity? Identity { get; set; }
        public DateTimeOffset? StartedAt { get; set; }
        public string ExpectedExecutablePath { get; }
        public IReadOnlyList<string> ExpectedArguments { get; }
        public string ExpectedWorkingDirectory { get; }
        public DateTimeOffset TrackedAt { get; }
    }
}

public sealed class StatusMonitor : IDisposable
{
    private readonly ServiceOrchestrator _orchestrator;
    private readonly SafeLogger _logger;
    private readonly System.Threading.Timer _timer;
    private readonly CancellationTokenSource _cancellation = new();
    private int _refreshing;
    private bool _started;
    private bool _disposed;
    private ServiceSnapshot _current;

    public StatusMonitor(ServiceOrchestrator orchestrator, SafeLogger logger)
    {
        _orchestrator = orchestrator;
        _logger = logger;
        _current = ServiceSnapshot.Empty(orchestrator.Definitions);
        _timer = new System.Threading.Timer(_ => _ = RefreshNowAsync(), null, Timeout.Infinite, Timeout.Infinite);
    }

    public event EventHandler<ServiceSnapshot>? SnapshotChanged;

    public ServiceSnapshot Current => _current;

    public void Start()
    {
        if (_started || _disposed)
        {
            return;
        }

        _started = true;
        _timer.Change(TimeSpan.Zero, TimeSpan.FromSeconds(5));
    }

    public async Task<ServiceSnapshot> RefreshNowAsync()
    {
        if (_disposed || Interlocked.Exchange(ref _refreshing, 1) == 1)
        {
            return _current;
        }

        try
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(_cancellation.Token);
            timeout.CancelAfter(TimeSpan.FromSeconds(10));
            var statuses = await _orchestrator.CheckStatusAsync(timeout.Token).ConfigureAwait(false);
            var snapshot = new ServiceSnapshot(
                statuses.ToDictionary(status => status.Definition.Kind),
                DateTimeOffset.Now,
                false);
            Publish(snapshot);
            return snapshot;
        }
        catch (OperationCanceledException) when (_cancellation.IsCancellationRequested)
        {
            return _current;
        }
        catch (OperationCanceledException)
        {
            var snapshot = CreateFailureSnapshot("状态检查超时，未沿用上次结果");
            Publish(snapshot);
            return snapshot;
        }
        catch (Exception exception)
        {
            _logger.Error("刷新服务状态失败", exception);
            var snapshot = CreateFailureSnapshot("状态检查失败，未沿用上次结果");
            Publish(snapshot);
            return snapshot;
        }
        finally
        {
            Volatile.Write(ref _refreshing, 0);
        }
    }

    private void Publish(ServiceSnapshot snapshot)
    {
        _current = snapshot;
        SnapshotChanged?.Invoke(this, snapshot);
    }

    private ServiceSnapshot CreateFailureSnapshot(string detail)
    {
        var checkedAt = DateTimeOffset.Now;
        var services = _orchestrator.Definitions.ToDictionary(
            definition => definition.Kind,
            definition => new ServiceStatus(
                definition,
                ServiceCondition.Unavailable,
                "检查失败",
                false,
                false,
                HttpProbeResult.NotChecked(detail),
                ProcessEvidence.None(detail),
                checkedAt,
                detail));
        return new ServiceSnapshot(services, checkedAt, false);
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        _cancellation.Cancel();
        _timer.Dispose();
        _cancellation.Dispose();
        SnapshotChanged = null;
    }
}
