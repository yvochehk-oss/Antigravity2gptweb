using System.ComponentModel;
using System.Diagnostics;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using ChengduConstructionController.Models;

namespace ChengduConstructionController.Services;

public sealed record ProcessIdentity(
    int ProcessId,
    string ProcessName,
    string ExecutablePath,
    string CommandLine,
    string WorkingDirectory,
    DateTimeOffset? StartedAt);

/// <summary>
/// The result of one Windows TCP listener-table read.  An empty process list
/// is a successful "no listener" observation; a failed read is kept distinct
/// so destructive cleanup never treats an unavailable table as an empty one.
/// </summary>
public sealed record PortTableReadResult(
    bool Success,
    IReadOnlyList<int> ProcessIds,
    string Detail)
{
    public static PortTableReadResult Failed(string detail) =>
        new(false, Array.Empty<int>(), detail);

    public static PortTableReadResult Succeeded(IReadOnlyList<int> processIds) =>
        new(true, processIds, "端口表读取成功");
}

/// <summary>
/// One process considered by a destructive start/restart preflight.  A
/// candidate is kept even when its identity cannot be read so callers can
/// fail closed instead of silently dropping an ambiguous PID.
/// </summary>
public sealed record ProcessCleanupCandidate(
    int ProcessId,
    ProcessIdentity? Identity,
    bool ProcessExists,
    bool IsListening,
    bool BelongsToProject,
    string Source)
{
    public bool IsConfirmed =>
        ProcessExists
        && BelongsToProject
        && Identity is { StartedAt: not null } identity
        && !string.IsNullOrWhiteSpace(identity.ProcessName)
        && !string.IsNullOrWhiteSpace(identity.ExecutablePath)
        && !string.IsNullOrWhiteSpace(identity.CommandLine)
        && !string.IsNullOrWhiteSpace(identity.WorkingDirectory);
}

public sealed record ProcessCleanupEvidence(
    IReadOnlyList<ProcessCleanupCandidate> Candidates,
    string Detail)
{
    public IReadOnlyList<ProcessCleanupCandidate> UnconfirmedCandidates =>
        Candidates.Where(candidate => candidate.ProcessExists && !candidate.IsConfirmed).ToArray();

    public bool IsSafe => UnconfirmedCandidates.Count == 0;
}

/// <summary>
/// Windows-only process evidence. Port ownership is resolved through the IP
/// Helper API and then checked against a project-root/process allow-list. A
/// port by itself is never considered sufficient evidence for a project service.
/// </summary>
public sealed class ProcessInspector
{
    private const int AfInet = 2;
    private const int TcpTableOwnerPidAll = 5;
    private const int TcpStateListen = 2;
    private const uint ErrorInsufficientBuffer = 122;
    private const uint Th32csSnappProcess = 0x00000002;

    public ProcessEvidence Inspect(ServiceDefinition definition, string? projectRoot, IReadOnlySet<int>? launchedProcessIds = null)
    {
        if (!OperatingSystem.IsWindows())
        {
            return ProcessEvidence.None("当前环境不是 Windows，暂不读取端口进程");
        }

        var listeningIds = GetListeningProcessIds(definition.Port);
        var effectiveLaunchedIds = ExpandDescendants(launchedProcessIds ?? new HashSet<int>());
        var identityIds = listeningIds.Count == 0
            ? effectiveLaunchedIds.ToArray()
            : listeningIds.Concat(effectiveLaunchedIds).Distinct().ToArray();
        var identities = ReadIdentities(identityIds);
        var owned = identities
            .Where(identity => IsProjectProcess(identity, definition, projectRoot, effectiveLaunchedIds))
            .ToArray();
        var projectTrees = owned
            .Select(identity => ExpandDescendants(new HashSet<int> { identity.ProcessId }))
            .ToArray();
        var portConfirmed = listeningIds.Count > 0
            && listeningIds.All(listeningId => projectTrees.Any(tree => tree.Contains(listeningId)));
        if (listeningIds.Count == 0)
        {
            // During startup a tracked launcher or the .pid file can be valid
            // before its socket enters LISTEN. Preserve that as "starting".
            if (owned.Length > 0)
            {
                return new ProcessEvidence(
                    Found: true,
                    BelongsToProject: true,
                    PortConfirmed: false,
                    PortListening: false,
                    ProcessIds: owned.Select(identity => identity.ProcessId).Distinct().ToArray(),
                    Detail: "已确认项目进程，正在等待目标端口就绪");
            }

            return ProcessEvidence.None("未发现监听该服务端口的进程");
        }

        if (portConfirmed)
        {
            return new ProcessEvidence(
                Found: true,
                BelongsToProject: true,
                PortConfirmed: true,
                PortListening: true,
                ProcessIds: owned.Select(identity => identity.ProcessId).Distinct().ToArray(),
                Detail: $"目标端口已由项目进程或其严格子进程链监听（{owned.Length} 个项目进程）");
        }

        return new ProcessEvidence(
            Found: true,
            BelongsToProject: owned.Length > 0,
            PortConfirmed: false,
            PortListening: true,
            ProcessIds: listeningIds,
            Detail: owned.Length > 0
                ? "项目进程存在，但目标端口由未确认进程监听"
                : "端口有响应进程，但未确认属于本项目");
    }

    public IReadOnlyList<ProcessIdentity> ReadProjectProcesses(
        ServiceDefinition definition,
        string? projectRoot,
        IReadOnlySet<int>? launchedProcessIds = null,
        IReadOnlyCollection<int>? additionalProcessIds = null)
    {
        if (!OperatingSystem.IsWindows())
        {
            return Array.Empty<ProcessIdentity>();
        }

        return FindProjectCandidates(definition, projectRoot, launchedProcessIds, additionalProcessIds)
            .Where(identity => IsProjectProcess(
                identity,
                definition,
                projectRoot,
                ExpandDescendants(launchedProcessIds ?? new HashSet<int>())))
            .ToArray();
    }

    /// <summary>
    /// Build the complete, fail-closed stop candidate set for one service.
    ///
    /// The port table, validated/recorded PIDs, and a narrow process command
    /// scan are all inspected before a caller sends a stop signal.  Listener
    /// and explicit PID candidates remain in the result even when Windows
    /// denies identity access; silently omitting either would turn an
    /// unknown process into a false "nothing to stop" result.
    /// </summary>
    public ProcessCleanupEvidence InspectCleanupCandidates(
        ServiceDefinition definition,
        string? projectRoot,
        IReadOnlySet<int>? launchedProcessIds = null,
        IReadOnlyCollection<int>? additionalProcessIds = null,
        PortTableReadResult? portTableResult = null)
    {
        if (!OperatingSystem.IsWindows())
        {
            return new ProcessCleanupEvidence(
                Array.Empty<ProcessCleanupCandidate>(),
                "当前环境不是 Windows，暂不读取停止候选");
        }

        var portTable = portTableResult ?? GetListeningProcessIdsResult(definition.Port);
        if (!portTable.Success)
        {
            return new ProcessCleanupEvidence(
                Array.Empty<ProcessCleanupCandidate>(),
                $"{definition.DisplayName}端口表读取失败：{portTable.Detail}");
        }

        var listeningIds = new HashSet<int>(portTable.ProcessIds);
        var trackedIds = launchedProcessIds is null
            ? new HashSet<int>()
            : launchedProcessIds.Where(processId => processId > 4).ToHashSet();
        var recordedIds = additionalProcessIds is null
            ? new HashSet<int>()
            : additionalProcessIds.Where(processId => processId > 4).ToHashSet();
        var knownIds = new HashSet<int>(listeningIds);
        knownIds.UnionWith(trackedIds);
        knownIds.UnionWith(recordedIds);

        // A tracked launcher may have children that own the socket.  Include
        // those children as explicit candidates so every process that could
        // be caught by a process-tree stop is checked before the first kill.
        var treeRoots = new HashSet<int>(trackedIds);
        treeRoots.UnionWith(recordedIds);
        var descendantIds = ExpandDescendants(treeRoots);
        knownIds.UnionWith(descendantIds);

        var identities = ReadIdentityMap(knownIds);
        var candidates = new Dictionary<int, ProcessCleanupCandidate>();
        foreach (var processId in knownIds)
        {
            if (processId <= 4)
            {
                continue;
            }

            identities.TryGetValue(processId, out var identity);
            var processExists = identity is not null || IsProcessAlive(processId);
            if (!processExists)
            {
                // A listener/PID can disappear between the port table and
                // identity read.  It is safe to ignore an actually-dead PID.
                continue;
            }

            if (!listeningIds.Contains(processId) && IsConsoleHostProcess(processId, identity))
            {
                // Windows attaches conhost.exe to console processes for stdio.
                // It does not hold ports and is terminated with the parent process tree.
                continue;
            }

            var belongsToProject = identity is not null
                && IsProjectProcess(identity, definition, projectRoot, trackedIds);
            var source = listeningIds.Contains(processId)
                ? "监听端口"
                : recordedIds.Contains(processId)
                    ? "PID 文件"
                    : trackedIds.Contains(processId)
                        ? "控制台记录"
                        : "已知项目进程子进程";
            candidates[processId] = new ProcessCleanupCandidate(
                processId,
                identity,
                true,
                listeningIds.Contains(processId),
                belongsToProject,
                source);
        }

        // Catch a manually launched project process when its PID file was
        // deleted and its socket is between states.  The exact --port value
        // is required before a process enters this candidate set; an
        // unrelated Python/Node process without this service's port is not a
        // reason to block all startup.
        foreach (var identity in ReadAllIdentities())
        {
            if (!LooksLikeServiceCandidate(identity, definition))
            {
                continue;
            }

            var belongsToProject = IsProjectProcess(identity, definition, projectRoot, trackedIds);
            candidates[identity.ProcessId] = new ProcessCleanupCandidate(
                identity.ProcessId,
                identity,
                true,
                listeningIds.Contains(identity.ProcessId),
                belongsToProject,
                "项目命令候选");
        }

        var ordered = candidates.Values
            .OrderByDescending(candidate => candidate.IsListening)
            .ThenBy(candidate => candidate.ProcessId)
            .ToArray();
        var detail = ordered.Length == 0
            ? "未发现监听者或项目进程候选"
            : $"发现 {ordered.Length} 个停止候选，其中 {ordered.Count(candidate => candidate.IsConfirmed)} 个归属已确认";
        return new ProcessCleanupEvidence(ordered, detail);
    }

    public ProcessIdentity? ReadIdentity(int processId)
    {
        return OperatingSystem.IsWindows() ? TryReadIdentity(processId) : null;
    }

    /// <summary>
    /// Reads identity through an already-held Process object. The caller uses
    /// this overload for a launch it created itself so a recycled PID is never
    /// re-opened and accidentally treated as the original process.
    /// </summary>
    public ProcessIdentity? ReadIdentity(Process process)
    {
        return OperatingSystem.IsWindows() ? TryReadIdentity(process) : null;
    }

    /// <summary>
    /// Distinguish a PID that disappeared during inspection from a live PID
    /// whose identity is unreadable.  The latter must remain fail-closed.
    /// </summary>
    public static bool IsProcessAlive(int processId)
    {
        if (!OperatingSystem.IsWindows() || processId <= 4)
        {
            return false;
        }

        try
        {
            using var process = Process.GetProcessById(processId);
            return !process.HasExited;
        }
        catch (ArgumentException)
        {
            return false;
        }
        catch (Exception exception) when (exception is InvalidOperationException or Win32Exception or UnauthorizedAccessException)
        {
            // Access failures are treated as live/unknown rather than as a
            // convenient reason to drop a candidate from the preflight.
            return true;
        }
    }

    public IReadOnlySet<int> ExpandProcessTree(IReadOnlySet<int> rootProcessIds)
    {
        return OperatingSystem.IsWindows()
            ? ExpandDescendants(rootProcessIds)
            : new HashSet<int>(rootProcessIds);
    }

    /// <summary>
    /// Read one Windows TCP listener table while preserving API failure as a
    /// separate outcome from a successful empty table.  Health/status callers
    /// can continue using GetListeningProcessIds, while destructive cleanup
    /// uses this result and refuses to act when the table is unavailable.
    /// </summary>
    public static PortTableReadResult GetListeningProcessIdsResult(int port)
    {
        if (!OperatingSystem.IsWindows())
        {
            return PortTableReadResult.Failed("当前环境不是 Windows");
        }

        if (port is < 1 or > 65535)
        {
            return PortTableReadResult.Failed("端口号无效");
        }

        var size = 0;
        IntPtr table = IntPtr.Zero;
        try
        {
            var result = GetExtendedTcpTable(IntPtr.Zero, ref size, true, AfInet, TcpTableOwnerPidAll, 0);
            if (result != ErrorInsufficientBuffer && result != 0)
            {
                return PortTableReadResult.Failed($"首次读取端口表失败（错误码 {result}）");
            }

            if (size == 0)
            {
                return result == 0
                    ? PortTableReadResult.Succeeded(Array.Empty<int>())
                    : PortTableReadResult.Failed("端口表大小不可用");
            }

            if (size < sizeof(uint))
            {
                return PortTableReadResult.Failed("端口表大小异常");
            }

            table = Marshal.AllocHGlobal(size);
            result = GetExtendedTcpTable(table, ref size, true, AfInet, TcpTableOwnerPidAll, 0);
            if (result != 0)
            {
                return PortTableReadResult.Failed($"读取端口表失败（错误码 {result}）");
            }

            if (size < sizeof(uint))
            {
                return PortTableReadResult.Failed("端口表返回大小异常");
            }

            var rowCount = Marshal.ReadInt32(table);
            var rowSize = Marshal.SizeOf<MibTcpRowOwnerPid>();
            var availableRows = (size - sizeof(uint)) / rowSize;
            if (rowCount < 0 || rowCount > availableRows)
            {
                return PortTableReadResult.Failed("端口表行数异常");
            }

            var pRow = IntPtr.Add(table, sizeof(uint));
            var owners = new HashSet<int>();
            for (var index = 0; index < rowCount; index++)
            {
                var row = Marshal.PtrToStructure<MibTcpRowOwnerPid>(pRow);
                pRow = IntPtr.Add(pRow, rowSize);
                if (row.State == TcpStateListen && ToHostPort(row.LocalPort) == port && row.OwningPid > 0)
                {
                    owners.Add(unchecked((int)row.OwningPid));
                }
            }

            return PortTableReadResult.Succeeded(owners.ToArray());
        }
        catch (Exception exception)
        {
            // A destructive caller must see every native/API failure instead
            // of receiving an empty list that could trigger a false cleanup.
            return PortTableReadResult.Failed($"端口表读取异常（{exception.GetType().Name}）");
        }
        finally
        {
            if (table != IntPtr.Zero)
            {
                Marshal.FreeHGlobal(table);
            }
        }
    }

    public static IReadOnlyList<int> GetListeningProcessIds(int port)
    {
        // Preserve the historical empty-list API for health/status callers.
        // Cleanup must call GetListeningProcessIdsResult instead.
        return GetListeningProcessIdsResult(port).ProcessIds;
    }

    public static bool IsProjectProcess(
        ProcessIdentity identity,
        ServiceDefinition definition,
        string? projectRoot,
        IReadOnlySet<int> launchedProcessIds)
    {
        // Tracked IDs only expand the candidate set. They never bypass the
        // evidence checks below: a recycled PID must not be trusted by name.
        _ = launchedProcessIds;
        if (identity.ProcessId <= 4)
        {
            return false;
        }

        if (string.IsNullOrWhiteSpace(projectRoot) || !IsAllowedProcessName(identity, definition))
        {
            return false;
        }

        if (string.IsNullOrWhiteSpace(identity.WorkingDirectory)
            || string.IsNullOrWhiteSpace(identity.CommandLine)
            || !HasPortArgument(identity.CommandLine, definition.Port))
        {
            return false;
        }

        var normalizedRoot = Normalize(projectRoot).TrimEnd('\\');
        var executable = Normalize(identity.ExecutablePath);
        var commandLine = Normalize(identity.CommandLine);
        var workingDirectory = Normalize(identity.WorkingDirectory);
        var marker = Normalize(definition.RootCommandMarker);
        var expectedWorkingDirectory = Normalize(Path.Combine(projectRoot, definition.RelativeWorkingDirectory));
        var executableUnderRoot = IsPathUnderRoot(executable, normalizedRoot);
        var workingDirectoryMatches = PathsEqual(workingDirectory, expectedWorkingDirectory);
        var commandMentionsProject = commandLine.Contains(marker, StringComparison.OrdinalIgnoreCase)
            && commandLine.Contains(normalizedRoot, StringComparison.OrdinalIgnoreCase);

        var isBossDirectory = workingDirectoryMatches
            || (IsPathUnderRoot(workingDirectory, normalizedRoot)
                && (PathsEqual(workingDirectory, normalizedRoot)
                    || PathsEqual(workingDirectory, Normalize(Path.Combine(normalizedRoot, "windows_scripts")))));

        // A Python/llama process must come from the checkout and carry the
        // service marker. Every process also needs the exact service working
        // directory and port argument. Boss's cmd/node processes are allowed
        // to use the inherited working directory when their executable is
        // outside the checkout.
        return definition.Kind switch
        {
            ServiceKind.LocalModel => executableUnderRoot && workingDirectoryMatches && commandMentionsProject,
            ServiceKind.Tax or ServiceKind.Rag or ServiceKind.Idp => (executableUnderRoot || IsAllowedProcessName(identity, definition))
                && workingDirectoryMatches
                && commandLine.Contains("uvicorn", StringComparison.OrdinalIgnoreCase)
                && commandLine.Contains("app.main:app", StringComparison.OrdinalIgnoreCase),
            ServiceKind.Boss => isBossDirectory
                && IsBossProcess(identity, definition, expectedWorkingDirectory),
            _ => false,
        };
    }

    public static bool SameProcessIdentity(ProcessIdentity expected, ProcessIdentity current)
    {
        if (expected.ProcessId != current.ProcessId
            || !expected.StartedAt.HasValue
            || !current.StartedAt.HasValue)
        {
            return false;
        }

        return expected.StartedAt.Value.UtcDateTime == current.StartedAt.Value.UtcDateTime
            && string.Equals(expected.ProcessName, current.ProcessName, StringComparison.OrdinalIgnoreCase)
            && PathsEqual(expected.ExecutablePath, current.ExecutablePath)
            && PathsEqual(expected.WorkingDirectory, current.WorkingDirectory)
            && string.Equals(
                Normalize(expected.CommandLine),
                Normalize(current.CommandLine),
                StringComparison.OrdinalIgnoreCase);
    }

    private static bool IsAllowedProcessName(ProcessIdentity identity, ServiceDefinition definition)
    {
        if (definition.AllowedProcessNames.Contains(identity.ProcessName))
        {
            return true;
        }

        var executableName = Path.GetFileName(identity.ExecutablePath);
        return !string.IsNullOrWhiteSpace(executableName)
            && definition.AllowedProcessNames.Contains(executableName);
    }

    private static bool IsBossProcess(
        ProcessIdentity identity,
        ServiceDefinition definition,
        string bossRoot)
    {
        // Negative contract: node.exe + project CWD +
        // C:\tmp\foreign-vite.js --port 5173 is never a project process.
        // Positive contract: node <boss>\node_modules\vite\bin\vite.js preview --port 5173
        // is accepted when the path is under this boss root.
        var tokens = ExpandShellCommandTokens(identity.CommandLine);
        if (!HasExactPortArgument(tokens, definition.Port))
        {
            return false;
        }

        return IsStrictNpmPreviewInvocation(tokens, definition.Port)
            || IsProjectViteEntrypoint(identity, tokens, bossRoot)
            || IsProjectServeWebInvocation(identity, tokens, bossRoot, definition.Port);
    }

    private static bool IsStrictNpmPreviewInvocation(
        IReadOnlyList<string> tokens,
        int port)
    {
        for (var index = 0; index + 2 < tokens.Count; index++)
        {
            if (!IsNpmCommandToken(tokens[index])
                || !string.Equals(tokens[index + 1], "run", StringComparison.OrdinalIgnoreCase)
                || !string.Equals(tokens[index + 2], "preview", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            var commandArguments = tokens.Skip(index + 3).ToArray();
            if (commandArguments.Any(ContainsShellControlCharacter)
                || !HasExactPortArgument(commandArguments, port))
            {
                continue;
            }

            return true;
        }

        return false;
    }

    private static bool IsNpmCommandToken(string token)
    {
        var normalized = NormalizeCommandToken(token);
        if (string.Equals(normalized, "npm", StringComparison.OrdinalIgnoreCase)
            || string.Equals(normalized, "npm.cmd", StringComparison.OrdinalIgnoreCase)
            || string.Equals(normalized, "npm.exe", StringComparison.OrdinalIgnoreCase)
            || string.Equals(normalized, "npm-cli.js", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }

        var fileName = Path.GetFileName(normalized);
        return string.Equals(fileName, "npm.cmd", StringComparison.OrdinalIgnoreCase)
            || string.Equals(fileName, "npm.exe", StringComparison.OrdinalIgnoreCase)
            || string.Equals(fileName, "npm-cli.js", StringComparison.OrdinalIgnoreCase);
    }

    private static bool IsProjectViteEntrypoint(
        ProcessIdentity identity,
        IReadOnlyList<string> tokens,
        string projectRoot)
    {
        var nodeModulesRoot = Path.Combine(projectRoot, "node_modules");
        var candidates = tokens.Append(identity.ExecutablePath);
        foreach (var token in candidates)
        {
            var path = ResolveProjectPathToken(token, projectRoot);
            if (string.IsNullOrWhiteSpace(path)
                || !IsPathUnderRoot(path, nodeModulesRoot)
                || !IsViteEntrypointPath(path, nodeModulesRoot))
            {
                continue;
            }

            return true;
        }

        return false;
    }

    private static bool IsViteEntrypointPath(string path, string nodeModulesRoot)
    {
        var normalizedPath = NormalizeAbsolutePath(path);
        var normalizedNodeModules = NormalizeAbsolutePath(nodeModulesRoot);
        if (!IsPathUnderRoot(normalizedPath, normalizedNodeModules))
        {
            return false;
        }

        var fileName = Path.GetFileName(normalizedPath);
        var isViteFile = string.Equals(fileName, "vite", StringComparison.OrdinalIgnoreCase)
            || string.Equals(fileName, "vite.cmd", StringComparison.OrdinalIgnoreCase)
            || string.Equals(fileName, "vite.exe", StringComparison.OrdinalIgnoreCase)
            || string.Equals(fileName, "vite.js", StringComparison.OrdinalIgnoreCase)
            || string.Equals(fileName, "vite.mjs", StringComparison.OrdinalIgnoreCase)
            || string.Equals(fileName, "vite.cjs", StringComparison.OrdinalIgnoreCase);
        if (!isViteFile)
        {
            return false;
        }

        var relative = normalizedPath[normalizedNodeModules.Length..].TrimStart('\\');
        return relative.StartsWith("vite\\", StringComparison.OrdinalIgnoreCase)
            || relative.StartsWith(".bin\\", StringComparison.OrdinalIgnoreCase)
            || relative.Contains("\\node_modules\\vite\\", StringComparison.OrdinalIgnoreCase);
    }

    private static bool IsConsoleHostProcess(int processId, ProcessIdentity? identity)
    {
        if (identity is not null)
        {
            if (string.Equals(identity.ProcessName, "conhost", StringComparison.OrdinalIgnoreCase)
                || string.Equals(identity.ProcessName, "conhost.exe", StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }

            var fileName = Path.GetFileName(identity.ExecutablePath);
            if (string.Equals(fileName, "conhost.exe", StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }

        try
        {
            using var process = Process.GetProcessById(processId);
            return string.Equals(process.ProcessName, "conhost", StringComparison.OrdinalIgnoreCase);
        }
        catch
        {
            return false;
        }
    }

    private static bool IsProjectServeWebInvocation(
        ProcessIdentity identity,
        IReadOnlyList<string> tokens,
        string bossRoot,
        int port)
    {
        var executableName = Path.GetFileName(identity.ExecutablePath);
        var isPython = string.Equals(identity.ProcessName, "python", StringComparison.OrdinalIgnoreCase)
            || string.Equals(identity.ProcessName, "python.exe", StringComparison.OrdinalIgnoreCase)
            || string.Equals(executableName, "python.exe", StringComparison.OrdinalIgnoreCase);
        if (!isPython)
        {
            return false;
        }

        var normalizedCommand = Normalize(identity.CommandLine);
        if (!normalizedCommand.Contains("serve_web.py", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        var projectRoot = ResolveProjectRootFromBossRoot(bossRoot);
        if (!string.IsNullOrWhiteSpace(projectRoot))
        {
            var normalizedRoot = Normalize(projectRoot).TrimEnd('\\');
            var scriptUnderRoot = normalizedCommand.Contains(normalizedRoot, StringComparison.OrdinalIgnoreCase)
                || IsPathUnderRoot(Normalize(identity.WorkingDirectory), normalizedRoot);
            if (!scriptUnderRoot)
            {
                return false;
            }
        }

        return HasExactPortArgument(tokens, port);
    }

    private static string? ResolveProjectRootFromBossRoot(string bossRoot)
    {
        try
        {
            var normalized = NormalizeAbsolutePath(bossRoot);
            var parent = Directory.GetParent(normalized);
            return parent?.Parent?.FullName;
        }
        catch
        {
            return null;
        }
    }

    private static string ResolveProjectPathToken(string token, string projectRoot)
    {
        var normalized = NormalizeCommandToken(token);
        if (string.IsNullOrWhiteSpace(normalized))
        {
            return string.Empty;
        }

        if (Path.IsPathFullyQualified(normalized))
        {
            return NormalizeAbsolutePath(normalized);
        }

        var relative = normalized.TrimStart('.', '\\');
        if (!relative.StartsWith("node_modules\\", StringComparison.OrdinalIgnoreCase))
        {
            return string.Empty;
        }

        return NormalizeAbsolutePath(Path.Combine(projectRoot, relative));
    }

    private static IReadOnlyList<string> ExpandShellCommandTokens(string commandLine)
    {
        var tokens = TokenizeWindowsCommandLine(commandLine);
        var expanded = new List<string>(tokens);
        for (var index = 0; index + 1 < tokens.Count; index++)
        {
            if (string.Equals(tokens[index], "/c", StringComparison.OrdinalIgnoreCase)
                || string.Equals(tokens[index], "/k", StringComparison.OrdinalIgnoreCase))
            {
                expanded.AddRange(TokenizeWindowsCommandLine(tokens[index + 1]));
            }
        }

        return expanded;
    }

    private static IReadOnlyList<string> TokenizeWindowsCommandLine(string commandLine)
    {
        var tokens = new List<string>();
        var token = new StringBuilder();
        var inQuotes = false;
        foreach (var character in commandLine)
        {
            if (character == '"')
            {
                inQuotes = !inQuotes;
                continue;
            }

            if (char.IsWhiteSpace(character) && !inQuotes)
            {
                if (token.Length > 0)
                {
                    tokens.Add(token.ToString());
                    token.Clear();
                }

                continue;
            }

            token.Append(character);
        }

        if (token.Length > 0)
        {
            tokens.Add(token.ToString());
        }

        return tokens;
    }

    private static bool HasExactPortArgument(IReadOnlyList<string> tokens, int port)
    {
        var portText = port.ToString(System.Globalization.CultureInfo.InvariantCulture);
        for (var index = 0; index < tokens.Count; index++)
        {
            var token = NormalizeCommandToken(tokens[index]);
            if (string.Equals(token, "--port", StringComparison.OrdinalIgnoreCase)
                && index + 1 < tokens.Count
                && string.Equals(tokens[index + 1], portText, StringComparison.Ordinal))
            {
                return true;
            }

            if (token.StartsWith("--port=", StringComparison.OrdinalIgnoreCase)
                && string.Equals(token[7..], portText, StringComparison.Ordinal))
            {
                return true;
            }

            if (string.Equals(token, portText, StringComparison.Ordinal))
            {
                return true;
            }
        }

        return false;
    }

    private static bool ContainsShellControlCharacter(string token)
    {
        return token.IndexOfAny(new[] { '&', '|', '<', '>' }) >= 0;
    }

    private static string NormalizeCommandToken(string token) => token.Trim().Trim('"');

    private static IReadOnlyList<ProcessIdentity> FindProjectCandidates(
        ServiceDefinition definition,
        string? projectRoot,
        IReadOnlySet<int>? launchedProcessIds,
        IReadOnlyCollection<int>? additionalProcessIds = null)
    {
        var candidates = new HashSet<int>(GetListeningProcessIds(definition.Port));
        if (additionalProcessIds is not null)
        {
            candidates.UnionWith(additionalProcessIds);
        }

        var effectiveLaunchedIds = ExpandDescendants(launchedProcessIds ?? new HashSet<int>());
        candidates.UnionWith(effectiveLaunchedIds);
        return ReadIdentities(candidates);
    }

    private static IReadOnlyList<ProcessIdentity> ReadIdentities(IEnumerable<int> processIds)
    {
        return processIds
            .Where(processId => processId > 4)
            .Select(TryReadIdentity)
            .Where(identity => identity is not null)
            .Cast<ProcessIdentity>()
            .ToArray();
    }

    private static IReadOnlyDictionary<int, ProcessIdentity> ReadIdentityMap(IEnumerable<int> processIds)
    {
        return ReadIdentities(processIds)
            .GroupBy(identity => identity.ProcessId)
            .ToDictionary(group => group.Key, group => group.First());
    }

    private static IReadOnlyList<ProcessIdentity> ReadAllIdentities()
    {
        Process[] processes;
        try
        {
            processes = Process.GetProcesses();
        }
        catch (Exception exception) when (exception is InvalidOperationException or Win32Exception or UnauthorizedAccessException)
        {
            return Array.Empty<ProcessIdentity>();
        }

        try
        {
            return processes
                .Select(TryReadIdentity)
                .Where(identity => identity is not null)
                .Cast<ProcessIdentity>()
                .ToArray();
        }
        finally
        {
            foreach (var process in processes)
            {
                process.Dispose();
            }
        }
    }

    private static bool LooksLikeServiceCandidate(ProcessIdentity identity, ServiceDefinition definition)
    {
        // Process names alone are too broad on Windows: many unrelated
        // Python/Node processes can be present.  Require the exact service
        // port argument before adding a process discovered by the command
        // scan; IsProjectProcess then applies the project path/marker checks.
        return identity.ProcessId > 4
            && IsAllowedProcessName(identity, definition)
            && !string.IsNullOrWhiteSpace(identity.CommandLine)
            && HasPortArgument(identity.CommandLine, definition.Port);
    }

    private static ProcessIdentity? TryReadIdentity(int processId)
    {
        if (processId <= 4)
        {
            return null;
        }

        try
        {
            using var process = Process.GetProcessById(processId);
            return TryReadIdentity(process);
        }
        catch (Exception exception) when (exception is ArgumentException or InvalidOperationException or Win32Exception or UnauthorizedAccessException)
        {
            return null;
        }
    }

    private static ProcessIdentity? TryReadIdentity(Process process)
    {
        try
        {
            var processId = process.Id;
            if (processId <= 4)
            {
                return null;
            }

            var name = process.ProcessName;
            var path = string.Empty;
            try
            {
                path = process.MainModule?.FileName ?? string.Empty;
            }
            catch (Exception exception) when (exception is InvalidOperationException or Win32Exception or NotSupportedException or UnauthorizedAccessException)
            {
                // Protected/system processes are intentionally treated as unknown.
            }

            DateTimeOffset? startedAt = null;
            try
            {
                startedAt = process.StartTime;
            }
            catch (Exception exception) when (exception is InvalidOperationException or Win32Exception or NotSupportedException or UnauthorizedAccessException)
            {
                // Process start time is optional evidence for an observation,
                // but tracked launch cleanup checks the original handle again.
            }

            return new ProcessIdentity(
                processId,
                name,
                path,
                NativeCommandLineReader.TryRead(process.Handle),
                NativeCommandLineReader.TryReadWorkingDirectory(process.Handle),
                startedAt);
        }
        catch (Exception exception) when (exception is ArgumentException or InvalidOperationException or Win32Exception or UnauthorizedAccessException)
        {
            return null;
        }
    }

    private static int ToHostPort(uint value)
    {
        return (ushort)IPAddress.NetworkToHostOrder(unchecked((short)(value & 0xffff)));
    }

    private static bool HasPortArgument(string commandLine, int port)
    {
        var portText = Regex.Escape(port.ToString(System.Globalization.CultureInfo.InvariantCulture));
        var patterns = new[]
        {
            $@"--port\s+{portText}(?![0-9A-Za-z])",
            $@"--port={portText}(?![0-9A-Za-z])",
            $@":{portText}(?![0-9A-Za-z])",
            $@"(?:^|\s){portText}(?![0-9A-Za-z])",
        };
        return patterns.Any(pattern => Regex.IsMatch(
            commandLine,
            pattern,
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant));
    }

    private static bool PathsEqual(string left, string right)
    {
        return string.Equals(
            Normalize(left).TrimEnd('\\'),
            Normalize(right).TrimEnd('\\'),
            StringComparison.OrdinalIgnoreCase);
    }

    private static bool IsPathUnderRoot(string path, string root)
    {
        var normalizedPath = NormalizeAbsolutePath(path);
        var normalizedRoot = NormalizeAbsolutePath(root);
        return !string.IsNullOrWhiteSpace(normalizedPath)
            && !string.IsNullOrWhiteSpace(normalizedRoot)
            && (PathsEqual(normalizedPath, normalizedRoot)
                || normalizedPath.StartsWith(normalizedRoot + "\\", StringComparison.OrdinalIgnoreCase));
    }

    private static string NormalizeAbsolutePath(string value)
    {
        var normalized = Normalize(value);
        if (string.IsNullOrWhiteSpace(normalized))
        {
            return string.Empty;
        }

        try
        {
            return Path.GetFullPath(normalized).TrimEnd('\\');
        }
        catch (Exception exception) when (exception is ArgumentException or IOException or NotSupportedException)
        {
            return normalized.TrimEnd('\\');
        }
    }

    private static string Normalize(string value) => value.Replace('/', '\\').Trim().Trim('"');

    private static IReadOnlySet<int> ExpandDescendants(IReadOnlySet<int> roots)
    {
        if (roots.Count == 0)
        {
            return new HashSet<int>();
        }

        var children = ReadProcessTree();
        var all = new HashSet<int>(roots);
        var queue = new Queue<int>(roots);
        while (queue.Count > 0)
        {
            var parent = queue.Dequeue();
            if (!children.TryGetValue(parent, out var childIds))
            {
                continue;
            }

            foreach (var child in childIds)
            {
                if (all.Add(child))
                {
                    queue.Enqueue(child);
                }
            }
        }

        return all;
    }

    private static IReadOnlyDictionary<int, IReadOnlyList<int>> ReadProcessTree()
    {
        var result = new Dictionary<int, List<int>>();
        var snapshot = CreateToolhelp32Snapshot(Th32csSnappProcess, 0);
        if (snapshot == IntPtr.Zero || snapshot == new IntPtr(-1))
        {
            return new Dictionary<int, IReadOnlyList<int>>();
        }

        try
        {
            var entry = new ProcessEntry32 { Size = (uint)Marshal.SizeOf<ProcessEntry32>() };
            if (!Process32First(snapshot, ref entry))
            {
                return new Dictionary<int, IReadOnlyList<int>>();
            }

            do
            {
                if (!result.TryGetValue(unchecked((int)entry.ParentProcessId), out var children))
                {
                    children = new List<int>();
                    result[unchecked((int)entry.ParentProcessId)] = children;
                }

                children.Add(unchecked((int)entry.ProcessId));
            }
            while (Process32Next(snapshot, ref entry));
        }
        finally
        {
            CloseHandle(snapshot);
        }

        return result.ToDictionary(pair => pair.Key, pair => (IReadOnlyList<int>)pair.Value);
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MibTcpRowOwnerPid
    {
        public int State;
        public uint LocalAddress;
        public uint LocalPort;
        public uint RemoteAddress;
        public uint RemotePort;
        public uint OwningPid;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct ProcessEntry32
    {
        public uint Size;
        public uint Usage;
        public uint ProcessId;
        public IntPtr DefaultHeapId;
        public uint ModuleId;
        public uint Threads;
        public uint ParentProcessId;
        public int BasePriority;
        public uint Flags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)]
        public string ExecutableFile;
    }

    [DllImport("iphlpapi.dll", SetLastError = true)]
    private static extern uint GetExtendedTcpTable(
        IntPtr tcpTable,
        ref int size,
        [MarshalAs(UnmanagedType.Bool)] bool order,
        int addressFamily,
        int tableClass,
        uint reserved);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint processId);

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool Process32First(IntPtr snapshot, ref ProcessEntry32 entry);

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool Process32Next(IntPtr snapshot, ref ProcessEntry32 entry);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CloseHandle(IntPtr handle);
}

internal static class NativeCommandLineReader
{
    private const uint ProcessQueryLimitedInformation = 0x1000;
    private const uint ProcessVmRead = 0x0010;
    private const int ProcessBasicInformationClass = 0;

    public static string TryRead(IntPtr processHandle)
    {
        if (!OperatingSystem.IsWindows() || processHandle == IntPtr.Zero)
        {
            return string.Empty;
        }

        // Process.Handle is already opened by System.Diagnostics with query
        // rights. Querying a protected process can fail and returns no evidence.
        try
        {
            var basicInfo = new ProcessBasicInformation();
            var status = NtQueryInformationProcess(
                processHandle,
                ProcessBasicInformationClass,
                ref basicInfo,
                Marshal.SizeOf<ProcessBasicInformation>(),
                out _);
            if (status != 0 || basicInfo.PebBaseAddress == IntPtr.Zero)
            {
                return string.Empty;
            }

            var pointerSize = IntPtr.Size;
            var parametersOffset = pointerSize == 8 ? 0x20 : 0x10;
            var commandLineOffset = pointerSize == 8 ? 0x70 : 0x40;
            var processParameters = ReadIntPtr(processHandle, IntPtr.Add(basicInfo.PebBaseAddress, parametersOffset));
            if (processParameters == IntPtr.Zero)
            {
                return string.Empty;
            }

            var unicodeAddress = IntPtr.Add(processParameters, commandLineOffset);
            var header = ReadBytes(processHandle, unicodeAddress, pointerSize == 8 ? 16 : 8);
            if (header.Length < (pointerSize == 8 ? 16 : 8))
            {
                return string.Empty;
            }

            var length = BitConverter.ToUInt16(header, 0);
            if (length == 0 || length > 32768)
            {
                return string.Empty;
            }

            var bufferOffset = pointerSize == 8 ? 8 : 4;
            var bufferAddress = pointerSize == 8
                ? new IntPtr(BitConverter.ToInt64(header, bufferOffset))
                : new IntPtr(BitConverter.ToInt32(header, bufferOffset));
            var bytes = ReadBytes(processHandle, bufferAddress, length);
            return bytes.Length == length ? System.Text.Encoding.Unicode.GetString(bytes) : string.Empty;
        }
        catch (Exception exception) when (exception is InvalidOperationException or Win32Exception or ArgumentException)
        {
            return string.Empty;
        }
    }

    public static string TryReadWorkingDirectory(IntPtr processHandle)
    {
        if (!OperatingSystem.IsWindows() || processHandle == IntPtr.Zero)
        {
            return string.Empty;
        }

        // RTL_USER_PROCESS_PARAMETERS.CurrentDirectory.DosPath is the only
        // reliable working-directory evidence available without launching a
        // shell or trusting a process name. Protected processes may deny the
        // read; an empty result intentionally fails closed at the caller.
        try
        {
            var basicInfo = new ProcessBasicInformation();
            var status = NtQueryInformationProcess(
                processHandle,
                ProcessBasicInformationClass,
                ref basicInfo,
                Marshal.SizeOf<ProcessBasicInformation>(),
                out _);
            if (status != 0 || basicInfo.PebBaseAddress == IntPtr.Zero)
            {
                return string.Empty;
            }

            var pointerSize = IntPtr.Size;
            var parametersOffset = pointerSize == 8 ? 0x20 : 0x10;
            var processParameters = ReadIntPtr(processHandle, IntPtr.Add(basicInfo.PebBaseAddress, parametersOffset));
            if (processParameters == IntPtr.Zero)
            {
                return string.Empty;
            }

            // RTL_USER_PROCESS_PARAMETERS.CurrentDirectory's DosPath
            // UNICODE_STRING starts at 0x38 on x64 and 0x24 on x86.
            var currentDirectoryOffset = pointerSize == 8 ? 0x38 : 0x24;
            var headerLength = pointerSize == 8 ? 16 : 8;
            var header = ReadBytes(
                processHandle,
                IntPtr.Add(processParameters, currentDirectoryOffset),
                headerLength);
            if (header.Length < headerLength)
            {
                return string.Empty;
            }

            var length = BitConverter.ToUInt16(header, 0);
            if (length == 0 || length > 32768)
            {
                return string.Empty;
            }

            var bufferOffset = pointerSize == 8 ? 8 : 4;
            var bufferAddress = pointerSize == 8
                ? new IntPtr(BitConverter.ToInt64(header, bufferOffset))
                : new IntPtr(BitConverter.ToInt32(header, bufferOffset));
            var bytes = ReadBytes(processHandle, bufferAddress, length);
            return bytes.Length == length ? System.Text.Encoding.Unicode.GetString(bytes) : string.Empty;
        }
        catch (Exception exception) when (exception is InvalidOperationException or Win32Exception or ArgumentException)
        {
            return string.Empty;
        }
    }

    private static IntPtr ReadIntPtr(IntPtr processHandle, IntPtr address)
    {
        var bytes = ReadBytes(processHandle, address, IntPtr.Size);
        if (bytes.Length != IntPtr.Size)
        {
            return IntPtr.Zero;
        }

        return IntPtr.Size == 8
            ? new IntPtr(BitConverter.ToInt64(bytes, 0))
            : new IntPtr(BitConverter.ToInt32(bytes, 0));
    }

    private static byte[] ReadBytes(IntPtr processHandle, IntPtr address, int count)
    {
        var bytes = new byte[count];
        return ReadProcessMemory(processHandle, address, bytes, count, out var read) && read.ToInt64() == count
            ? bytes
            : Array.Empty<byte>();
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ProcessBasicInformation
    {
        public IntPtr Reserved1;
        public IntPtr PebBaseAddress;
        public IntPtr Reserved2;
        public IntPtr Reserved3;
        public IntPtr UniqueProcessId;
        public IntPtr Reserved4;
    }

    [DllImport("ntdll.dll")]
    private static extern int NtQueryInformationProcess(
        IntPtr processHandle,
        int informationClass,
        ref ProcessBasicInformation information,
        int informationLength,
        out int returnLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ReadProcessMemory(
        IntPtr processHandle,
        IntPtr baseAddress,
        [Out] byte[] buffer,
        int size,
        out IntPtr numberOfBytesRead);
}
