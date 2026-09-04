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
        "退出控制台（不停止服务）",
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
        "trackedLaunch is null && !HasLivePortEvidence",
        "launch.Process.HasExited",
        "launch.StartedAt.Value.UtcDateTime",
        "EstablishTrackedLaunchAsync",
        "TrackedIdentityAttempts",
        "TrackedIdentityPoll",
        "RequiredStableIdentityReads",
        "TerminateDirectLaunch",
        "原始 Process 句柄",
        "身份建立失败",
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
    for marker in (
        "public bool AllStopped",
        "public bool HasMixedStopped",
        "public ServiceCondition OverallCondition",
        "部分服务未运行",
        "StatusColor(snapshot.OverallCondition)",
    ):
        require(marker in cs, f"缺少统一状态聚合逻辑：{marker}")
    require("http://127.0.0.1" in cs_lower, "健康检查没有限制到本机回环地址")
    require("net8.0-windows" in source, "工程没有配置 Windows 目标框架")
    require("<usewindowsforms>true</usewindowsforms>" in lower_source, "工程没有启用 WinForms")
    require("assemblyname>成都建工控制台</assemblyname>" in lower_source, "应用名称没有设置为成都建工控制台")
    require("/d\", \"/s\", \"/c\"" in cs, "老板端没有通过隐藏命令适配器启动")

    print(f"[PASS] Windows 控制台离线静态自检通过（检查 {len(source_files)} 个工程文件）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
