# 成都建工 V3.1 — Windows 安装完成

## 1. 当前数据位置

V3.1 当前运行代码以安装/项目根目录为事实来源：

| 内容 | 路径 |
|---|---|
| PostgreSQL 数据 | `database\data` |
| PostgreSQL 运行事实 | `runtime\state\postgres.json` |
| PostgreSQL 日志 | `logs\postgres.log` |
| 业务源码 | `source_code\...` |

`runtime\state\postgres.json` 是本机运行态，不属于发布源码，也不会提交到 Git。

> 卸载前如需保留业务数据库，请先备份 `database\data` 或使用数据库备份流程。当前版本不再宣称存在尚未实现的“设置 → 数据目录 / NTFS Junction”迁移功能。

## 2. 启动顺序与单一控制面

推荐直接启动“成都建工控制台”。点击“启动全部”或运行 `windows_scripts\START_WINDOWS.bat` 时，最终都会进入同一个 C# `ServiceOrchestrator`：

1. 安全清理本项目上一轮业务进程；
2. PostgreSQL 在 `54320..54369` 中查找/协商可用端口并完成身份确认；
3. 写入 `runtime\state\postgres.json`；
4. 启动 LLM (8930) → RAG (8922) → IDP (8933) → Tax (8921) → Boss (5173)；
5. 完成端口、进程归属和健康检查后才判定启动成功。

RAG / Tax 的兼容 BAT 会读取 `postgres.json`，不再硬编码 54320。

## 3. 停止语义

`windows_scripts\STOP_WINDOWS.bat` 继续调用现有 `99_STOP_ALL.bat`。V3.1 当前明确保留该脚本按服务端口取得 PID 并执行 `taskkill /F` 的强制停止行为；本次 Runtime SSOT 收口没有修改它。

## 4. 本地模型

默认模型为 `Spark-X2.5-4B-Q4_K_M.gguf`，缺失时可回退 `Qwen3.5-2B-Q4_K_M.gguf`。Spark-X2.5 需要 llama.cpp build >= 10828。

## 5. 故障排查

- 控制台 EXE：`成都建工控制台3.1.exe`
- 开发发布目录：`desktop_apps\windows\publish\win-x64`
- PostgreSQL 实际端口：查看 `runtime\state\postgres.json`
- 服务日志：查看控制台日志和根目录 `logs` 目录
- 如果 `postgres.json` 不存在，不要手工猜 54320；请通过控制台/`START_WINDOWS.bat` 让 C# 重新协商并建立运行事实。
