# 成都建工 V3.1 Windows 控制台

V3.1 Windows 控制台是基于 **.NET 10 / WinForms / win-x64** 的本地运行控制面。正式发布文件名固定为 `成都建工控制台3.1.exe`。

## Runtime SSOT

Windows 端以 C# `ServiceOrchestrator` 为唯一的“启动全部”控制面：

- `START_WINDOWS.bat` / `00_START_ALL.bat` 只负责定位控制台并调用 `--start-all`。
- PostgreSQL 由 `PostgreSqlPortNegotiator` 在 `54320..54369` 中协商端口，并把事实写入 `runtime\state\postgres.json`。
- RAG / Tax 等兼容 BAT 不再自行假定 54320；需要手工运行时必须读取上述运行事实。
- LLM：8930；RAG：8922；IDP：8933；Tax：8921；Boss：5173。
- 本地模型优先 `Spark-X2.5-4B-Q4_K_M.gguf`，回退 `Qwen3.5-2B-Q4_K_M.gguf`。
- Spark-X2.5 要求 llama.cpp build **>= 10828**，C# 与 BAT 均采用最低版本比较，而不是只接受 build 10828。
- Python 服务正式交付优先使用 `runtime\python\Scripts\python.exe`；老板端使用预构建 `dist` + `serve_web.py`，客户机不要求 Node.js。

## 构建

Windows PowerShell：

```powershell
cd desktop_apps\windows
.\build.ps1 -RunSmokeTests
```

或：

```bat
cd desktop_apps\windows
build.cmd -RunSmokeTests
```

Canonical 发布产物：

```text
desktop_apps\windows\publish\win-x64\成都建工控制台3.1.exe
```

构建脚本不再把 EXE 复制到仓库根目录。

## 安装包

先完成控制台发布，再运行：

```bat
installer\build_installer.bat
```

安装包输出：

```text
installer\output\ChengduConstructionConsole-v3.1.0-Setup.exe
```

需要 Inno Setup 6。安装器从 canonical `publish\win-x64` 目录取主程序，并使用仓库现有 `app.ico`。

## 启停入口

- `windows_scripts\START_WINDOWS.bat`：通过 C# 控制面启动全部服务。
- `windows_scripts\START_TRAY_WINDOWS.bat`：启动托盘控制台，不预先另起一套数据库/服务控制逻辑。
- `windows_scripts\STOP_WINDOWS.bat`：调用 `99_STOP_ALL.bat`。

> V3.1 当前明确保留 `99_STOP_ALL.bat` 的强制终止语义，包括按服务端口取得 PID 后 `taskkill /F`。这一行为是现行 Windows 运维契约，不由 Runtime SSOT 收口任务修改。

## PostgreSQL 数据与运行状态

当前代码事实：

- 数据目录：项目/安装根目录下 `database\data`
- 运行事实：项目/安装根目录下 `runtime\state\postgres.json`
- 控制台日志：当前用户本地应用数据目录中的控制台日志

`runtime\state` 属于机器运行态，已加入 `.gitignore`，不得提交到 Git。

## CI

`.github/workflows/ci.yml` 已覆盖 `v3.1-windows`，Windows job 使用 `windows-latest` + `.NET 10` 执行：

1. `tests/static_smoke.py`
2. `dotnet restore`
3. `dotnet test`
4. `build.ps1 -RunSmokeTests`
5. 验证 `publish\win-x64\成都建工控制台3.1.exe`

## 项目根目录

控制台会从自身目录/当前目录向上寻找同时包含 `windows_scripts` 与 `source_code` 的 V3.1 根目录；也可设置 `CHENGDU_JIANGONG_ROOT` 或从托盘菜单选择项目目录。项目选择记录保存在当前用户 LocalApplicationData，不绑定开发机盘符。
