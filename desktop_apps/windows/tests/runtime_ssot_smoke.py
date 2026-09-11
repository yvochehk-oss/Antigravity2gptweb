#!/usr/bin/env python3
"""Regression gate for Windows runtime SSOT ordering and output pumping."""

from __future__ import annotations

from pathlib import Path
import re


APP_DIR = Path(__file__).resolve().parents[1]
MODELS = APP_DIR / "Models" / "ServiceModels.cs"
ADAPTER = APP_DIR / "Services" / "WindowsServiceAdapter.cs"
ORCHESTRATOR = APP_DIR / "Services" / "ServiceOrchestrator.cs"
TRAY = APP_DIR / "UI" / "TrayApplicationContext.cs"


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"[FAIL] {message}")
        raise SystemExit(1)


def read(path: Path) -> str:
    require(path.is_file(), f"缺少文件：{path.relative_to(APP_DIR)}")
    return path.read_text(encoding="utf-8-sig")


def parse_order(source: str, array_name: str) -> list[str]:
    match = re.search(
        rf"{array_name}\s*=\s*\{{(?P<body>.*?)\}};",
        source,
        flags=re.DOTALL,
    )
    require(match is not None, f"缺少顺序契约：{array_name}")
    assert match is not None
    return re.findall(r"ServiceKind\.(\w+)", match.group("body"))


def main() -> int:
    models = read(MODELS)
    adapter = read(ADAPTER)
    orchestrator = read(ORCHESTRATOR)
    tray = read(TRAY)

    startup_order = parse_order(models, "StartupOrderKinds")
    menu_order = parse_order(models, "MenuOrderKinds")

    require(
        startup_order == ["LocalModel", "Rag", "Tax", "Idp", "Boss"],
        f"Windows StartupOrder 漂移：{startup_order}",
    )
    require(
        menu_order == ["LocalModel", "Tax", "Rag", "Idp", "Boss"],
        f"托盘 MenuOrder 漂移：{menu_order}",
    )
    require(startup_order != menu_order, "StartupOrder 与 MenuOrder 再次被耦合")
    require(
        'return OrderByKinds(definitions, StartupOrderKinds, "Windows 启动")' in models,
        "ServiceCatalog.Create() 没有显式使用 StartupOrder",
    )
    require(
        "ServiceCatalog.OrderForMenu(_orchestrator.Definitions)" in tray,
        "托盘菜单没有显式使用独立 MenuOrder",
    )
    require(
        "new StatusForm(\n                ServiceCatalog.OrderForMenu(_orchestrator.Definitions)," in tray,
        "状态窗口没有沿用独立 MenuOrder",
    )

    require(
        "RedirectStandardOutput = true" in adapter
        and "RedirectStandardError = true" in adapter,
        "长驻服务输出流未开启重定向",
    )
    require(
        "StandardOutputEncoding = Encoding.UTF8" in adapter
        and "StandardErrorEncoding = Encoding.UTF8" in adapter,
        "长驻服务输出流未固定 UTF-8 编码",
    )
    require(
        "StartOutputPump(definition, process)" in orchestrator
        and "process.StandardOutput" in orchestrator
        and "process.StandardError" in orchestrator,
        "Orchestrator 输出 Pump 契约缺失",
    )
    require(
        "RedirectStandardOutput = false" not in adapter
        and "RedirectStandardError = false" not in adapter,
        "检测到关闭重定向但仍保留输出 Pump 的 P0 回归模式",
    )

    print("[PASS] Windows Runtime SSOT 回归门禁通过：Startup/Menu 顺序解耦，stdout/stderr Pump 契约一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
