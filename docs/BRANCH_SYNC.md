# 成都建工跨平台分支同步规范

> ⚠️ **重要架构调整**：
> - **V2.0 已停止维护并归档**：历史版本已由 Tag `v2.0` 与 `archive/v2.0-*` 永久封存，旧 `macos` / `windows` 裸分支已下线废弃；
> - **V3.0 为唯一活跃生产基线**：GitHub 默认主分支 `main` 与 V3 版本镜像保持一致：
>   ```text
>   main == v3.0 == v3.0-macos == v3.0-windows
>   ```

## 核心原则

平台分支不是两套业务源码。macOS 与 Windows 只承担部署入口和运行环境差异；Tax、RAG、IDP、数据库迁移、业务规则与 API 必须保持同一套源码。

### 活跃开发组 (V3.0)

- `main`：GitHub 默认生产与开发基线
- `v3.0`：V3 主体代码基线（镜像引用）
- `v3.0-macos`：V3 macOS 镜像引用
- `v3.0-windows`：V3 Windows 镜像引用

正常状态必须满足：

```text
main == v3.0 == v3.0-macos == v3.0-windows
```

## 平台差异如何表达

平台差异只能通过同一代码树中的脚本、路径和部署文档表达，不允许复制业务源码后分别维护。

典型差异包括：

- macOS：shell / Homebrew / Apple Silicon 或 Intel Python 环境；
- Windows：BAT / PowerShell / Windows Python 与服务启动方式；
- 本地模型服务地址、模型文件绝对路径、PostgreSQL 安装位置；
- 文件系统路径和进程管理方式。

以下内容必须跨平台共用：

- `source_code/0.1_税务管理/`
- `source_code/0.2_RAG系统/`
- `source_code/0.4_IDP文档录入引擎_V3.0/app/`
- PostgreSQL schema / migrations
- Python 业务规则、Pydantic Schema、API 契约
- Ling / Granite / BGE-M3 / Reranker 的职责边界

## 自动同步

`.github/workflows/cross-platform-sync.yml` 同时监听 V2 与 V3 两组三分支。

一次提交只在所属版本组三个分支之间镜像：

- V2 的提交不会推进 V3；
- V3 的提交不会覆盖 V2；
- 同一版本组只允许 fast-forward；
- 自动同步不会解决冲突，也不会用 `ours/theirs` 覆盖独立修改。

如果同组两个平台分支产生独立提交，工作流必须失败，等待人工把有效修改归并到该版本的主体基线后，再恢复三个引用到同一 SHA。

## V3 平台部署入口

V3 的平台启动文件也保存在同一代码树：

```text
source_code/0.4_IDP文档录入引擎_V3.0/
  START_IDP_MACOS.sh
  START_IDP_WINDOWS.bat
  platform/
    macos/
    windows/
```

这些文件只处理环境准备、路径、Python 虚拟环境和进程启动，不复制 `app/` 业务源码。

## 禁止事项

禁止：

- 在 `v3.0-macos` 和 `v3.0-windows` 分别修改业务逻辑；
- 使用整目录 `ours/theirs` 自动解决源码冲突；
- 让某个平台拥有独立数据库 schema；
- 为平台差异复制一套 IDP/RAG Python 源码；
- 用 `--allow-unrelated-histories` 强行拼接正式平台分支。

## 历史归档

V2 分支治理前历史保留：

- `archive/macos-pre-unify-20260828`
- `archive/windows-pre-unify-20260828`

归档只用于追溯，不参与 V2/V3 日常同步。
