# 成都建工 V2.0 Agent 开发与分支同步规则 (AGENTS.md)

本文件规定了 AI Agent 在本项目中进行代码修改、Git 同步、版本提交和跨平台治理的核心原则与行为准则。

---

## 1. 单一主体代码与分支镜像架构 (Canonical Branch Mirror)

* **唯一代码基线**：`main` 是项目的唯一主体基线。`windows` 和 `macos` 是对同一代码基线的镜像引用。三者正常情况下必须始终指向**同一个 Commit SHA**。
* **业务源码零分叉**：所有子系统（`0.1_税务管理`、`0.2_RAG系统`、`0.3_老板端安卓App_天府掌舵`、前端 UI、数据库模型与迁移）在 Windows 与 macOS 上共用**同一套共享源码**。
* **平台差异严格收敛在启动脚本**：
  * macOS / Linux：`start_all.sh`、`stop_all.sh` 等 Shell 脚本；
  * Windows：`00_一键启动` ~ `99_停止全部`、`START_WINDOWS.bat`、`windows_scripts/` 等批处理脚本。
  * 严禁在 `source_code/` 内部为不同操作系统建立分叉目录或分支特异性代码！

---

## 2. Agent 提交与推送原则

1. **工作分支**：
   * 在 Windows 机器上开发时，本地处于 `windows` 分支；
   * 提交代码必须使用标准清晰的 Conventional Commits 格式（如 `feat(tax): ...`、`fix(rag): ...`、`docs(sync): ...`）。
2. **快速线性提交与自动镜像**：
   * 本地提交后直接推送到远程：`git push origin windows`；
   * 远程 GitHub Actions（`.github/workflows/cross-platform-sync.yml`）会自动触发 **Canonical Branch Mirror**，在校验通过后以 fast-forward 方式将 `main` 和 `macos` 原子同步至同一 Commit SHA。
3. **安全同步命令**：
   * 本地拉取远程最新代码时，必须使用 **`--ff-only`** 线性快进同步（或运行 `windows_scripts/sync_from_macos.bat`），避免生成无意义的 merge commit。

---

## 3. 严格禁止的破坏性操作 (Prohibited Actions)

Agent 在任何情况下**绝对禁止**执行以下操作：
❌ **禁止** 使用 `git checkout --theirs source_code/` 或 `git checkout --ours source_code/` 盲目覆盖业务代码。
❌ **禁止** 在发生冲突时未经业务代码逐行审查直接 `git add -A` 并无脑 commit。
❌ **禁止** 使用 `--allow-unrelated-histories` 强行将不同历史的分支拼接到一起。
❌ **禁止** 将虚拟环境（`.venv`、`.mineru-venv`）、GGUF 大模型权重（>100MB）、Postgres 数据库二进制目录（`database/`）或超大备份文件（`*.dump`）提交到 Git。

---

## 4. 分叉与冲突应急处理机制

如果由于双端同时开发导致远程分支分叉，CI 自动镜像将自动熔断并停止同步。
此时的处理流程为：
1. 检出 `main` 分支，人工审查并精准整合双端的有效业务修改；
2. 运行系统全量回归验证与测试；
3. 将统一后的提交同步到 `main`，并依次将 `windows` 和 `macos` 以 `--ff-only` 恢复为相同 SHA。
