# 成都建工 V3.1 — 安装完成

## 1. 数据目录（重要）

V3.1 起所有用户数据放在系统用户目录，**不再随安装目录升级/卸载而丢失**：

| 内容 | 路径 |
|---|---|
| PostgreSQL 数据 | `%LOCALAPPDATA%\ChengduConstructionConsole\database\data` |
| PostgreSQL 控制日志 | `%LOCALAPPDATA%\ChengduConstructionConsole\database\logs` |
| PostgreSQL 运行事实 | `%LOCALAPPDATA%\ChengduConstructionConsole\runtime\state\postgres.json` |
| RAG / Tax 数据库备份 | `%LOCALAPPDATA%\ChengduConstructionConsole\database\backups` |

如需迁移到其它盘符，请在「控制台 → 设置 → 数据目录」中指定；
控制台会通过 PowerShell 的 NTFS Junction 把外部数据目录挂到上面路径，
无需手动修改。

## 2. 启动顺序

1. 双击桌面「成都建工控制台」图标。
2. 主程序启动后会读取 `%LOCALAPPDATA%\ChengduConstructionConsole\runtime\state\postgres.json`，
   若不存在则 PostgreSQLPortNegotiator 会扫描 54320..54369 区间并写回运行事实。
3. 启动 LLM (8930) → RAG (8922) → IDP (8933) → Tax (8921) → Boss (5173)。

## 3. 卸载

卸载会保留 `%LOCALAPPDATA%\ChengduConstructionConsole\` 下的所有数据；
若要彻底清除，请在卸载前手动备份后再删除该目录。

## 4. 端口冲突

V3.1 已弃用硬编码 54320 的设计。所有端口（PG: 54320..54369、LLM: 8930、
RAG: 8922、Tax: 8921、IDP: 8933、Boss: 5173）均可在控制台设置页调整；
运行事实文件 `postgres.json` 是单一可信源。

