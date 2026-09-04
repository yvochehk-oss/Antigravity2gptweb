using System.ComponentModel;
using System.Diagnostics;
using System.Net;
using System.Runtime.InteropServices;
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
            ? effectiveLaunchedIds
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

    public IReadOnlySet<int> ExpandProcessTree(IReadOnlySet<int> rootProcessIds)
    {
        return OperatingSystem.IsWindows()
            ? ExpandDescendants(rootProcessIds)
            : new HashSet<int>(rootProcessIds);
    }

    public static IReadOnlyList<int> GetListeningProcessIds(int port)
    {
        if (!OperatingSystem.IsWindows() || port is < 1 or > 65535)
        {
            return Array.Empty<int>();
        }

        var size = 0;
        var result = GetExtendedTcpTable(IntPtr.Zero, ref size, true, AfInet, TcpTableOwnerPidAll, 0);
        if (result != ErrorInsufficientBuffer && result != 0)
        {
            return Array.Empty<int>();
        }

        if (size <= 0)
        {
            return Array.Empty<int>();
        }

        var table = Marshal.AllocHGlobal(size);
        try
        {
            result = GetExtendedTcpTable(table, ref size, true, AfInet, TcpTableOwnerPidAll, 0);
            if (result != 0)
            {
                return Array.Empty<int>();
            }

            var rowCount = Marshal.ReadInt32(table);
            var rowSize = Marshal.SizeOf<MibTcpRowOwnerPid>();
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

            return owners.ToArray();
        }
        catch (Exception exception) when (exception is ExternalException or ArgumentException)
        {
            return Array.Empty<int>();
        }
        finally
        {
            Marshal.FreeHGlobal(table);
        }
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

        var normalizedRoot = Normalize(projectRoot).TrimEnd('\\') + "\\";
        var executable = Normalize(identity.ExecutablePath);
        var commandLine = Normalize(identity.CommandLine);
        var workingDirectory = Normalize(identity.WorkingDirectory);
        var marker = Normalize(definition.RootCommandMarker);
        var expectedWorkingDirectory = Normalize(Path.Combine(projectRoot, definition.RelativeWorkingDirectory));
        var executableUnderRoot = executable.StartsWith(normalizedRoot, StringComparison.OrdinalIgnoreCase);
        var workingDirectoryMatches = PathsEqual(workingDirectory, expectedWorkingDirectory);
        var commandMentionsProject = commandLine.Contains(marker, StringComparison.OrdinalIgnoreCase)
            && commandLine.Contains(normalizedRoot.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);

        // A Python/llama process must come from the checkout and carry the
        // service marker. Every process also needs the exact service working
        // directory and port argument. Boss's cmd/node processes are allowed
        // to use the inherited working directory when their executable is
        // outside the checkout.
        return definition.Kind switch
        {
            ServiceKind.LocalModel => executableUnderRoot && workingDirectoryMatches && commandMentionsProject,
            ServiceKind.Tax or ServiceKind.Rag or ServiceKind.Idp => executableUnderRoot
                && workingDirectoryMatches
                && commandLine.Contains("uvicorn", StringComparison.OrdinalIgnoreCase)
                && commandLine.Contains("app.main:app", StringComparison.OrdinalIgnoreCase),
            ServiceKind.Boss => workingDirectoryMatches
                && (commandMentionsProject
                    || commandLine.Contains("npm run preview", StringComparison.OrdinalIgnoreCase)
                    || commandLine.Contains("vite", StringComparison.OrdinalIgnoreCase)
                    || executableUnderRoot),
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
        var portText = port.ToString(System.Globalization.CultureInfo.InvariantCulture);
        return commandLine.Contains($"--port {portText}", StringComparison.OrdinalIgnoreCase)
            || commandLine.Contains($"--port={portText}", StringComparison.OrdinalIgnoreCase)
            || commandLine.Contains($":{portText}", StringComparison.OrdinalIgnoreCase);
    }

    private static bool PathsEqual(string left, string right)
    {
        return string.Equals(
            Normalize(left).TrimEnd('\\'),
            Normalize(right).TrimEnd('\\'),
            StringComparison.OrdinalIgnoreCase);
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
    private const int ProcessBasicInformation = 0;

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
                ProcessBasicInformation,
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
                ProcessBasicInformation,
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
