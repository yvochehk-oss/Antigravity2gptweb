#!/usr/bin/env python3
"""Offline checks for the Windows tray controller.

The checks intentionally avoid package installation, network access, and
Windows-only APIs so they can run on the current macOS development host.
"""

from __future__ import annotations

from pathlib import Path
import sys


APP_DIR = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"[FAIL] {message}")
    raise SystemExit(1)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def main() -> int:
    required = [
        "ChengduConstructionController.csproj",
        "Program.cs",
        "app.manifest",
        "Models/ServiceModels.cs",
        "Services/ProjectRootResolver.cs",
        "Services/SafeLogger.cs",
        "Services/ProcessInspector.cs",
        "Services/HealthProbe.cs",
        "Services/StartupRegistration.cs",
        "Services/ServiceOrchestrator.cs",
        "Services/WindowsServiceAdapter.cs",
        "UI/TrayIconFactory.cs",
        "UI/StatusForm.cs",
        "UI/TrayApplicationContext.cs",
        "build.ps1",
        "build.cmd",
        "build.sh",
    ]
    for relative in required:
        require((APP_DIR / relative).is_file(), f"缺少文件：{relative}")

    source_extensions = {".cs", ".csproj", ".manifest", ".ps1", ".cmd", ".sh"}
    source_files = [
        path
        for path in APP_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in source_extensions
    ]
    source = "\n".join(path.read_text(encoding="utf-8-sig") for path in source_files)
    lower_source = source.lower()

    for forbidden in (
        "google",
        "fonts.googleapis",
        "gstatic",
        "taskkill",
        "stop-process",
        "pip install",
        "npm install",
        "alembic",
        "truncate ",
    ):
        require(forbidden not in lower_source, f"发现禁止依赖或破坏性命令：{forbidden}")

    cs = "\n".join(
        path.read_text(encoding="utf-8-sig")
        for path in APP_DIR.rglob("*.cs")
    )
    cs_lower = cs.lower()
    for port in ("8930", "8921", "8922", "8933", "5173"):
        require(port in cs, f"缺少服务端口：{port}")
    for marker in (
        "Mutex",
        "SemaphoreSlim",
        ".local_llm.pid",
        "GetExtendedTcpTable",
        "ProcessInspector.IsProjectProcess",
        "process.Kill(entireProcessTree: true)",
        "Registry.CurrentUser",
        "TimeSpan.FromSeconds(5)",
        "UseShellExecute = false",
        "CreateNoWindow = true",
        "退出控制台",
        "数据库保持运行",
        "RollbackStartedThisRound",
        "StopTrackedLaunch",
        "ProcessIdentity? Identity",
        "ProcessInspector.SameProcessIdentity",
        "TryReadWorkingDirectory",
        "HasLivePortEvidence",
        "GetListeningProcessIds(port)",
        "原先已运行服务和 PostgreSQL 未纳入回滚",
        "PID 已复用",
        "PortConfirmed",
        "PortListening",
        "var currentDirectoryOffset = pointerSize == 8 ? 0x38 : 0x24",
        "VerifyStartupAsync",
        "IsStartupReady",
        "StartupVerificationTimeout = TimeSpan.FromSeconds(30)",
        "StartupVerificationPoll = TimeSpan.FromMilliseconds(500)",
        "ExpectedExecutablePath",
        "ExpectedArguments",
        "ExpectedWorkingDirectory",
        "status.Process.PortListening && !status.Process.PortConfirmed",
        "目标端口由未确认进程监听，视为端口冲突",
        "五项服务健康、目标端口监听和项目归属均已确认",
        "status.Http.ModelReady",
        "&& !HasLivePortEvidence(",
        "launch.Process.HasExited",
        "launch.StartedAt.Value.UtcDateTime",
        "EstablishTrackedLaunchAsync",
        "TrackedIdentityAttempts",
        "TrackedIdentityPoll",
        "RequiredStableIdentityReads",
        "TerminateDirectLaunch",
        "原始 Process 句柄",
        "身份建立失败",
        "InspectCleanupCandidates",
        "PortTableReadResult",
        "GetListeningProcessIdsResult",
        "ProcessCleanupCandidate",
        "UnconfirmedCandidates",
        "ProcessCleanupEvidence",
        "BuildCleanupPlan",
        "StopServicesBeforeStartAsync",
        "ExecuteCleanupPlanAsync",
        "WaitForPortsReleasedAsync",
        "PortReleaseTimeout = TimeSpan.FromSeconds(15)",
        "allowCommandPortEvidence",
        "IsProcessAlive",
        "candidate.IsConfirmed",
        "未发送停止信号",
        "PID 文件格式无效",
        "ServiceKind.Boss => \".app.pid\"",
        "RegexOptions.CultureInvariant",
        "IsStrictNpmPreviewInvocation",
        "IsProjectViteEntrypoint",
        "TokenizeWindowsCommandLine",
        "ResolveProjectPathToken",
        "IsViteEntrypointPath",
        "HasExactPortArgument",
        "NormalizeAbsolutePath",
        "npm-cli.js",
        r"C:\tmp\foreign-vite.js --port 5173",
        r"node <boss>\node_modules\vite\bin\vite.js preview --port 5173",
        "五项服务端口表读取失败",
        "无法确认五项服务端口是否已释放",
        "catch (OperationCanceledException)",
        "catch (Exception exception)",
    ):
        require(marker in cs, f"缺少安全/交互约束：{marker}")
    require(
        "currentDirectoryOffset + pointerSize" not in cs,
        "工作目录读取仍错误地向 UNICODE_STRING 偏移了指针大小",
    )
    require(
        cs.count("RollbackStartedThisRound(startedThisRound)") == 1,
        "启动失败回滚没有收敛到唯一出口",
    )
    require(
        "launchedProcessIds.Contains(identity.ProcessId)" not in cs,
        "tracked PID 仍存在仅凭进程名/路径的认领旁路",
    )
    inspector = (APP_DIR / "Services/ProcessInspector.cs").read_text(encoding="utf-8-sig")
    require(
        'commandLine.Contains("vite", StringComparison.OrdinalIgnoreCase)' not in inspector,
        "Boss 归属不能通过任意 vite 文本认领",
    )
    require(
        "IsStrictNpmPreviewInvocation(tokens, definition.Port)" in inspector
        and "IsProjectViteEntrypoint(identity, tokens, bossRoot)" in inspector
        and "IsBossProcess(identity, definition, expectedWorkingDirectory)" in inspector
        and "IsPathUnderRoot(path, nodeModulesRoot)" in inspector,
        "Boss 归属缺少严格 npm/Vite 路径边界",
    )
    require(
        r"C:\tmp\foreign-vite.js --port 5173" in inspector,
        "缺少 foreign-vite 路径负例契约",
    )
    require(
        r"node <boss>\node_modules\vite\bin\vite.js preview --port 5173" in inspector,
        "缺少老板端 Vite 路径正例契约",
    )
    orchestrator = (APP_DIR / "Services/ServiceOrchestrator.cs").read_text(encoding="utf-8-sig")
    require(
        orchestrator.count("new HashSet<int> { evidence.ProcessId.Value }") == 1,
        "PID 文件证据集合表达式必须恰好出现一次",
    )
    start_all = orchestrator[orchestrator.index("StartAllAsync"):orchestrator.index("RestartAllAsync")]
    restart_all = orchestrator[orchestrator.index("RestartAllAsync"):orchestrator.index("StopAllAsync")]
    start_core = orchestrator[
        orchestrator.index("private async Task<OperationResult> StartAllCoreAsync"):
        orchestrator.index("VerifyStartupAsync")
    ]
    preflight = orchestrator[
        orchestrator.index("private CleanupPreflightResult BuildCleanupPlan"):
        orchestrator.index("private async Task<OperationResult> ExecuteCleanupPlanAsync")
    ]
    wait_for_ports = orchestrator[
        orchestrator.index("private async Task<OperationResult> WaitForPortsReleasedAsync"):
        orchestrator.index("private async Task<ServiceStatus> CheckOneAsync")
    ]
    require("StartAllCoreAsync(cancellationToken)" in start_all, "启动全部没有进入统一启动事务")
    require("StartAllCoreAsync(cancellationToken)" in restart_all, "重启全部没有进入统一启动事务")
    require("StopAllCoreAsync" not in restart_all, "重启全部仍通过旧的分离停止路径")
    require("StopServicesBeforeStartAsync" in start_core, "启动前没有执行统一安全清理")
    require("_adapter.Start(definition)" in start_core, "统一启动事务没有启动服务")
    require(
        start_core.index("StopServicesBeforeStartAsync") < start_core.index("_adapter.Start(definition)"),
        "启动服务发生在启动前清理之前",
    )
    require("alreadyRunning" not in start_core, "启动逻辑仍存在复用旧进程分支")
    require(
        preflight.index("InspectCleanupCandidates") < preflight.index("candidate.IsConfirmed"),
        "候选归属检查顺序不正确",
    )
    require(
        preflight.index("var portTables = _definitions.ToDictionary")
        < preflight.index("InspectCleanupCandidates"),
        "清理预检没有先收集全部端口表结果",
    )
    require("failedPortTables" in preflight, "端口表读取失败没有在预检阶段阻断")
    require("StopOwnedProcess" not in preflight, "预检阶段不应发送停止信号")
    execute_cleanup = orchestrator[
        orchestrator.index("private async Task<OperationResult> ExecuteCleanupPlanAsync"):
        orchestrator.index("private async Task<OperationResult> WaitForPortsReleasedAsync")
    ]
    require(
        execute_cleanup.index("StopOwnedProcess") < execute_cleanup.index("WaitForPortsReleasedAsync"),
        "清理没有先停止确认进程",
    )
    require(
        "BuildCleanupPlan" in orchestrator[orchestrator.index("StopServicesBeforeStartAsync"):],
        "启动前清理没有构建完整预检计划",
    )
    stop_before_start = orchestrator[
        orchestrator.index("private async Task<OperationResult> StopServicesBeforeStartAsync"):
        orchestrator.index("private CleanupPreflightResult BuildCleanupPlan")
    ]
    require(
        "var preflight = BuildCleanupPlan()" in stop_before_start,
        "启动前清理路由没有进入预检构建阶段",
    )
    require(
        "ExecuteCleanupPlanAsync(preflight.Targets" in stop_before_start,
        "启动前清理路由没有执行已预检的清理计划",
    )
    require("GetListeningProcessIdsResult" in wait_for_ports, "端口释放检查没有使用可区分失败的端口表结果")
    require("failedPortTables" in wait_for_ports, "端口释放检查没有在端口表失败时阻断")
    require("未启动新服务" in wait_for_ports, "端口释放检查失败时缺少不启动保护")
    for marker in (
        "public bool AllStopped",
        "public bool HasMixedStopped",
        "public ServiceCondition OverallCondition",
        "部分服务未运行",
        "StatusColor(snapshot.OverallCondition)",
    ):
        require(marker in cs, f"缺少统一状态聚合逻辑：{marker}")

    tray = (APP_DIR / "UI/TrayApplicationContext.cs").read_text(encoding="utf-8-sig")
    root_resolver = (APP_DIR / "Services/ProjectRootResolver.cs").read_text(encoding="utf-8-sig")
    program = (APP_DIR / "Program.cs").read_text(encoding="utf-8-sig")
    require('new ToolStripMenuItem("服务状态")' not in tray, "五项服务状态仍被折叠到二级菜单")
    for marker in (
        "总体状态：",
        "项目目录：已连接",
        "项目目录：未选择",
        "打开智能财税管理系统",
        "打开资料输入管理系统",
        "打开移动端管理系统",
        "启动全部",
        "重启全部",
        "停止全部业务服务（保留数据库）",
        "重新检查状态",
        "查看运行日志",
        "选择项目目录…",
        "登录后自动运行",
        "退出控制台",
        "FolderBrowserDialog",
        "ShortcutKeyDisplayString",
        "ShowItemToolTips = true",
        "BuildServiceToolTip",
        "status.Process.ProcessIds",
        "menu.KeyDown",
        "target.PerformClick()",
    ):
        require(marker in tray, f"缺少 macOS 菜单对齐契约：{marker}")
    for shortcut in (
        'CreateActionItem("打开智能财税管理系统", "1")',
        'CreateActionItem("打开资料输入管理系统", "2")',
        'CreateActionItem("打开移动端管理系统", "3")',
        'CreateActionItem("重新检查状态", "R")',
        'CreateActionItem("查看运行日志", "L")',
        'CreateActionItem("退出控制台", "Q")',
        "Keys.D1 or Keys.NumPad1",
        "Keys.D2 or Keys.NumPad2",
        "Keys.D3 or Keys.NumPad3",
        "Keys.R => refresh",
        "Keys.L => openLog",
        "Keys.Q => exit",
    ):
        require(shortcut in tray, f"缺少菜单快捷键契约：{shortcut}")
    for marker in (
        'RootFileName = "project_root.txt"',
        '"ChengduConstruction"',
        "TrySetRoot",
        "PersistRoot",
        "File.Move(temporaryPath, _persistencePath, true)",
        'Path.Combine(full, "windows_scripts")',
        'Path.Combine(full, "source_code")',
    ):
        require(marker in root_resolver, f"缺少项目目录持久化/校验契约：{marker}")
    require(
        "new TrayApplicationContext(rootResolver, orchestrator, monitor, logger)" in program,
        "TrayApplicationContext 没有共享动态 ProjectRootResolver",
    )

    require("http://127.0.0.1" in cs_lower, "健康检查没有限制到本机回环地址")
    require("net8.0-windows" in source, "工程没有配置 Windows 目标框架")
    require("<usewindowsforms>true</usewindowsforms>" in lower_source, "工程没有启用 WinForms")
    require("assemblyname>成都建工控制台</assemblyname>" in lower_source, "应用名称没有设置为成都建工控制台")
    require("/d\", \"/s\", \"/c\"" in cs, "老板端没有通过隐藏命令适配器启动")

    print(f"[PASS] Windows 控制台离线静态自检通过（检查 {len(source_files)} 个工程文件）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
