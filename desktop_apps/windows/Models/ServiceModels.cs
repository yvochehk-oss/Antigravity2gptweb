namespace ChengduConstructionController.Models;

public enum ServiceKind
{
    LocalModel,
    Tax,
    Rag,
    Idp,
    Boss,
}

public enum ServiceStartKind
{
    LocalExecutable,
    PythonModule,
    NpmPreview,
    /// <summary>
    /// [V3.1] Boss web client: pure Python http.server serving the
    /// prebuilt static dist. No Node.js / vite runtime required on the
    /// customer machine; dist is delivered via Installer.
    /// </summary>
    StaticPythonServer,
}

public enum ServiceCondition
{
    Unknown,
    Stopped,
    Starting,
    Healthy,
    Degraded,
    Unavailable,
}

/// <summary>
/// The service contract used by the Windows controller. It describes only public
/// local endpoints and launch metadata; it does not duplicate business logic.
/// </summary>
public sealed record ServiceDefinition(
    ServiceKind Kind,
    string DisplayName,
    int Port,
    IReadOnlyList<string> HealthPaths,
    IReadOnlySet<string> AllowedProcessNames,
    string RootCommandMarker,
    ServiceStartKind StartKind,
    string RelativeWorkingDirectory,
    string? RelativeExecutablePath,
    string? RelativeModelPath,
    string BrowserUrl)
{
    public string LoopbackBaseUrl => $"http://127.0.0.1:{Port}";
}

public sealed record HttpProbeResult(
    bool Responded,
    bool Healthy,
    bool ModelReady,
    bool Slow,
    int? StatusCode,
    string Detail,
    string? Endpoint,
    TimeSpan Elapsed)
{
    public static HttpProbeResult NotChecked(string detail) =>
        new(false, false, false, false, null, detail, null, TimeSpan.Zero);
}

public sealed record ProcessEvidence(
    bool Found,
    bool BelongsToProject,
    bool PortConfirmed,
    bool PortListening,
    IReadOnlyList<int> ProcessIds,
    string Detail)
{
    public static ProcessEvidence None(string detail) => new(false, false, false, false, Array.Empty<int>(), detail);
}

public sealed record ServiceStatus(
    ServiceDefinition Definition,
    ServiceCondition Condition,
    string StateText,
    bool HttpHealthy,
    bool ProcessConfirmed,
    HttpProbeResult Http,
    ProcessEvidence Process,
    DateTimeOffset CheckedAt,
    string Detail)
{
    public bool IsRunning => Condition is ServiceCondition.Healthy or ServiceCondition.Degraded;

    public string PortText => $"本机端口 {Definition.Port}";
}

public sealed record ServiceSnapshot(
    IReadOnlyDictionary<ServiceKind, ServiceStatus> Services,
    DateTimeOffset CheckedAt,
    bool IsRefreshing)
{
    public static ServiceSnapshot Empty(IReadOnlyList<ServiceDefinition> definitions) =>
        new(
            definitions.ToDictionary(
                definition => definition.Kind,
                definition => new ServiceStatus(
                    definition,
                    ServiceCondition.Unknown,
                    "正在检查",
                    false,
                    false,
                    HttpProbeResult.NotChecked("尚未检查"),
                    ProcessEvidence.None("尚未检查"),
                    DateTimeOffset.MinValue,
                    "正在检查服务状态")),
            DateTimeOffset.MinValue,
            false);

    public ServiceStatus this[ServiceKind kind] => Services[kind];

    public bool AllHealthy => Services.Count > 0 &&
                               Services.Values.All(status => status.Condition == ServiceCondition.Healthy);

    public bool AllStopped => Services.Count > 0 &&
                              Services.Values.All(status => status.Condition == ServiceCondition.Stopped);

    public bool HasMixedStopped => Services.Values.Any(status => status.IsRunning) &&
                                   Services.Values.Any(status => status.Condition == ServiceCondition.Stopped);

    public bool AnyStarting => Services.Values.Any(status => status.Condition == ServiceCondition.Starting);

    public bool AnyUnavailable => Services.Values.Any(status => status.Condition == ServiceCondition.Unavailable);

    public bool AnyDegraded => Services.Values.Any(status => status.Condition == ServiceCondition.Degraded);

    public ServiceCondition OverallCondition =>
        AllHealthy
            ? ServiceCondition.Healthy
            : AllStopped
                ? ServiceCondition.Stopped
                : AnyUnavailable
                    ? ServiceCondition.Unavailable
                    : AnyDegraded || HasMixedStopped
                        ? ServiceCondition.Degraded
                        : AnyStarting
                            ? ServiceCondition.Starting
                            : ServiceCondition.Unknown;

    public string OverallText => OverallCondition switch
    {
        ServiceCondition.Healthy => "全部服务正常",
        ServiceCondition.Stopped => "全部服务已停止",
        ServiceCondition.Starting => "服务正在启动",
        ServiceCondition.Degraded when HasMixedStopped => "部分服务未运行",
        ServiceCondition.Degraded => "部分服务运行异常",
        ServiceCondition.Unavailable => "部分服务不可用",
        _ => "正在读取服务状态",
    };
}

public sealed record OperationResult(bool Success, string Message);

public static class ServiceCatalog
{
    private static readonly ServiceKind[] StartupOrderKinds =
    {
        ServiceKind.LocalModel,
        ServiceKind.Rag,
        ServiceKind.Tax,
        ServiceKind.Idp,
        ServiceKind.Boss,
    };

    private static readonly ServiceKind[] MenuOrderKinds =
    {
        ServiceKind.LocalModel,
        ServiceKind.Tax,
        ServiceKind.Rag,
        ServiceKind.Idp,
        ServiceKind.Boss,
    };

    public static IReadOnlyList<ServiceKind> StartupOrder => StartupOrderKinds;
    public static IReadOnlyList<ServiceKind> MenuOrder => MenuOrderKinds;

    public static IReadOnlyList<ServiceDefinition> Create()
    {
        var definitions = new[]
        {
            new ServiceDefinition(
                ServiceKind.LocalModel,
                "本地语言模型",
                8930,
                new[] { "/health", "/v1/models" },
                new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "llama-server", "llama-server.exe" },
                @"models\local-llm",
                ServiceStartKind.LocalExecutable,
                @"models\local-llm\runtime-win-cpu-x64",
                @"models\local-llm\runtime-win-cpu-x64\llama-server.exe",
                null,
                ""),
            new ServiceDefinition(
                ServiceKind.Rag,
                "资料输入管理系统",
                8922,
                new[] { "/api/v1/health", "/healthz" },
                new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "python", "python.exe", "uvicorn", "uvicorn.exe" },
                @"0.2_RAG系统",
                ServiceStartKind.PythonModule,
                @"source_code\0.2_RAG系统\project-rag-v1.1",
                null,
                null,
                "http://127.0.0.1:8922/"),
            new ServiceDefinition(
                ServiceKind.Tax,
                "智能财税管理系统",
                8921,
                new[] { "/healthz" },
                new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "python", "python.exe", "uvicorn", "uvicorn.exe" },
                @"0.1_税务管理",
                ServiceStartKind.PythonModule,
                @"source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0",
                null,
                null,
                "http://127.0.0.1:8921/"),
            new ServiceDefinition(
                ServiceKind.Idp,
                "文档录入引擎",
                8933,
                new[] { "/health" },
                new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "python", "python.exe", "uvicorn", "uvicorn.exe" },
                @"0.4_IDP文档录入引擎_V3.1",
                ServiceStartKind.PythonModule,
                @"source_code\0.4_IDP文档录入引擎_V3.1",
                null,
                null,
                "http://127.0.0.1:8933/"),
            new ServiceDefinition(
                ServiceKind.Boss,
                "移动端管理系统",
                5173,
                new[] { "/" },
                new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "python", "python.exe", "cmd", "cmd.exe" },
                @"0.3_老板端安卓App_天府掌舵",
                ServiceStartKind.StaticPythonServer,
                @"source_code\0.3_老板端安卓App_天府掌舵",
                null,
                null,
                "http://127.0.0.1:5173/"),
        };

        // ServiceOrchestrator consumes Create() directly, so this return value
        // remains the explicit dependency-safe startup order.
        return OrderByKinds(definitions, StartupOrderKinds, "Windows 启动");
    }

    public static IReadOnlyList<ServiceDefinition> OrderForMenu(
        IReadOnlyList<ServiceDefinition> definitions) =>
        OrderByKinds(definitions, MenuOrderKinds, "托盘菜单");

    private static IReadOnlyList<ServiceDefinition> OrderByKinds(
        IReadOnlyList<ServiceDefinition> definitions,
        IReadOnlyList<ServiceKind> order,
        string contractName)
    {
        var byKind = definitions.ToDictionary(definition => definition.Kind);
        if (byKind.Count != order.Count || order.Any(kind => !byKind.ContainsKey(kind)))
        {
            throw new InvalidOperationException($"服务目录与{contractName}契约不一致，拒绝继续。");
        }

        return order.Select(kind => byKind[kind]).ToArray();
    }
}
