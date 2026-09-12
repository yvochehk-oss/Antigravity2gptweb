#!/usr/bin/env python3
"""Offline contract checks for the V3.1 Windows control plane."""

from __future__ import annotations

from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_DIR.parents[1]
WINDOWS_SCRIPTS = REPO_ROOT / "windows_scripts"
INSTALLER_DIR = REPO_ROOT / "installer"


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"[FAIL] {message}")
        raise SystemExit(1)


def read(path: Path) -> str:
    """Read repository text without assuming every legacy BAT is UTF-8.

    Most V3.1 sources are UTF-8/UTF-8-SIG, but historical Windows batch files
    may exist in a CP936/GBK-compatible working-tree encoding.  Contract tests
    only need stable textual markers, so decode deterministically instead of
    crashing before assertions can run.
    """
    require(path.is_file(), f"缺少文件：{path.relative_to(REPO_ROOT)}")
    data = path.read_bytes()

    for encoding in ("utf-8-sig", "gb18030", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    require(
        False,
        f"无法解码文件：{path.relative_to(REPO_ROOT)}（已尝试 utf-8-sig / gb18030 / gbk）",
    )
    raise AssertionError("unreachable")


def main() -> int:
    required = [
        APP_DIR / "ChengduConstructionController.csproj",
        APP_DIR / "Program.cs",
        APP_DIR / "Services/ProjectRootResolver.cs",
        APP_DIR / "Services/ProcessInspector.cs",
        APP_DIR / "Services/HealthProbe.cs",
        APP_DIR / "Services/PostgreSqlPortNegotiator.cs",
        APP_DIR / "Services/ServiceOrchestrator.cs",
        APP_DIR / "Services/WindowsServiceAdapter.cs",
        APP_DIR / "UI/TrayApplicationContext.cs",
        APP_DIR / "build.ps1",
        APP_DIR / "build.sh",
        WINDOWS_SCRIPTS / "START_WINDOWS.bat",
        WINDOWS_SCRIPTS / "STOP_WINDOWS.bat",
        WINDOWS_SCRIPTS / "00_START_ALL.bat",
        WINDOWS_SCRIPTS / "00_START_POSTGRES.bat",
        WINDOWS_SCRIPTS / "01_START_LLM.bat",
        WINDOWS_SCRIPTS / "02_START_RAG.bat",
        WINDOWS_SCRIPTS / "03_START_TAX.bat",
        WINDOWS_SCRIPTS / "99_STOP_ALL.bat",
        INSTALLER_DIR / "setup.iss",
        INSTALLER_DIR / "build_installer.bat",
    ]
    for path in required:
        require(path.is_file(), f"缺少文件：{path.relative_to(REPO_ROOT)}")

    csproj = read(APP_DIR / "ChengduConstructionController.csproj").lower()
    require("<targetframework>net10.0-windows</targetframework>" in csproj, ".NET 目标框架不是 net10.0-windows")
    require("<assemblyname>成都建工控制台3.1</assemblyname>" in csproj, "控制台 AssemblyName 未锁定为 3.1")
    require("<runtimeidentifier>win-x64</runtimeidentifier>" in csproj, "Windows RID 未锁定为 win-x64")
    require('remove="tests\\**\\*.cs"' in csproj, "主工程未排除 tests 子项目源码")

    program = read(APP_DIR / "Program.cs")
    require('"--start-all"' in program, "控制台缺少 --start-all CLI 入口")
    require("orchestrator.StartAllAsync()" in program, "CLI 未复用 C# ServiceOrchestrator")

    orchestrator = read(APP_DIR / "Services/ServiceOrchestrator.cs")
    for marker in (
        "StopServicesBeforeStartAsync",
        "BuildCleanupPlan",
        "ExecuteCleanupPlanAsync",
        "EnsurePostgresRunningAsync",
        "StartupVerificationTimeout = TimeSpan.FromSeconds(90)",
    ):
        require(marker in orchestrator, f"ServiceOrchestrator 缺少门禁：{marker}")

    postgres = read(APP_DIR / "Services/PostgreSqlPortNegotiator.cs")
    for marker in (
        "PreferredPort = 54320",
        "PortScanRange = 50",
        "pg_isready.exe",
        r"runtime\state\postgres.json",
        "PersistStateAsync",
    ):
        require(marker in postgres, f"PostgreSQL SSOT 缺少契约：{marker}")

    adapter = read(APP_DIR / "Services/WindowsServiceAdapter.cs")
    require("DatabasePort" in adapter and "postgresql://postgres@127.0.0.1:{dbPort}/projectrag" in adapter, "Python 服务未使用协商数据库端口")
    require("build < MinimumSparkLlamaBuild" in adapter, "C# Spark runtime 未使用最低 build 比较")
    require("StaticPythonServer" in adapter and "serve_web.py" in adapter, "Boss Web 未使用静态 Python 服务契约")

    start_wrapper = read(WINDOWS_SCRIPTS / "START_WINDOWS.bat")
    stop_wrapper = read(WINDOWS_SCRIPTS / "STOP_WINDOWS.bat")
    start_all = read(WINDOWS_SCRIPTS / "00_START_ALL.bat")
    require(r"windows_scripts\windows_scripts" not in start_wrapper.lower(), "START_WINDOWS 仍存在重复 windows_scripts 路径")
    require(r"windows_scripts\windows_scripts" not in stop_wrapper.lower(), "STOP_WINDOWS 仍存在重复 windows_scripts 路径")
    require('"%controller_exe%" --start-all' in start_all.lower(), "00_START_ALL 未委托 C# 控制面")

    rag = read(WINDOWS_SCRIPTS / "02_START_RAG.bat")
    tax = read(WINDOWS_SCRIPTS / "03_START_TAX.bat")
    for name, script in (("RAG", rag), ("Tax", tax)):
        require("runtime\\state\\postgres.json" in script, f"{name} 未读取 postgres.json")
        require("127.0.0.1:54320/projectrag" not in script, f"{name} 仍硬编码数据库 54320")
        require("%DB_PORT%" in script, f"{name} 未使用协商 DB_PORT")

    llm = read(WINDOWS_SCRIPTS / "01_START_LLM.bat")
    require("MIN_LLAMA_BUILD=10828" in llm, "Spark-X2.5 最低 llama.cpp build 丢失")
    require("LSS %MIN_LLAMA_BUILD%" in llm, "BAT runtime 仍未使用 >= 最低 build 语义")
    require('findstr /i "build 10828"' not in llm.lower(), "BAT runtime 仍使用精确 build 10828 匹配")

    # User-approved operational contract: the legacy stop script intentionally
    # force-kills listeners on the service ports. Keep this behavior unchanged.
    stop_all = read(WINDOWS_SCRIPTS / "99_STOP_ALL.bat")
    require("taskkill /F /IM llama-server.exe" in stop_all, "强杀 llama-server 行为被意外移除")
    require("taskkill /F /PID" in stop_all, "强杀端口 PID 行为被意外移除")
    for port in ("8921", "8922", "8933", "5173"):
        require(port in stop_all, f"强杀停止契约缺少端口 {port}")

    build = read(APP_DIR / "build.ps1")
    require("成都建工控制台3.1.exe" in build, "build.ps1 仍使用旧 EXE 名称")
    require(".NET 10 SDK" in build, "build.ps1 SDK 提示未升级到 .NET 10")
    require("Copy-Item $publishedExe" not in build, "build.ps1 仍把构建产物复制到仓库根目录")

    setup = read(INSTALLER_DIR / "setup.iss")
    require('#define MyAppExeName "成都建工控制台3.1.exe"' in setup, "Installer EXE 名称与 csproj 不一致")
    require(r"publish\win-x64\{#MyAppExeName}" in setup, "Installer 未从 canonical publish 目录取主程序")
    require(r"SetupIconFile=..\app.ico" in setup, "Installer 图标未使用现有 app.ico")
    require("LicenseFile=" not in setup, "Installer 仍引用不存在的 LICENSE")

    print("[PASS] V3.1 Windows Runtime/Build/Installer SSOT 静态门禁通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
