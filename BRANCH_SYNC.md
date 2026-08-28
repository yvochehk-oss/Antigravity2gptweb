# 成都建工 V2.0 分支同步规范

## 唯一主体代码

`main` 是唯一主体代码基线。`macos` 与 `windows` 是同一主体代码的镜像引用，三者正常状态必须指向同一个 commit SHA。

平台差异通过同一代码树中的启动/部署脚本表达，而不是通过业务源码分叉：

- macOS：`start_all.sh`、`stop_all.sh` 等脚本；
- Windows：`START_WINDOWS.bat`、`windows_scripts/` 等脚本；
- Tax / RAG / Boss / 前端 / 数据库迁移均只有一套共享源码。

## 自动同步

`.github/workflows/cross-platform-sync.yml` 监听 `main`、`macos`、`windows`。

任意一个分支产生正常的单向新提交时，工作流会：

1. 校验另外两个分支是否都是该提交的祖先；
2. 只允许 fast-forward，不自动解决冲突、不覆盖独立提交；
3. 原子更新另外两个分支到同一个 SHA；
4. 最终验证 `main == macos == windows`。

GitHub Actions 使用 `GITHUB_TOKEN` 推送镜像引用，不会递归触发新的同步运行，因此不会产生同步循环。

## 分叉处理原则

如果两个平台在尚未同步完成时分别产生独立提交，工作流会明确失败，而不是选择 `ours/theirs` 或整目录覆盖。

此时必须把双方有效修改人工归并到 `main`，验证后再将三个正式分支恢复到同一 SHA。

禁止使用以下方式自动处理业务源码冲突：

- `git checkout --theirs source_code/`
- `git checkout --ours source_code/`
- `git add -A` 后无审查自动提交冲突结果
- `--allow-unrelated-histories` 强行拼接平台分支

## 本地同步

macOS 可运行：

```bash
./sync_from_windows.sh
```

Windows 可运行：

```bat
一键同步_macOS最新功能.bat
```

保留上述旧文件名是为了兼容已有使用习惯；脚本内部已经不再执行平台间 merge，而是验证远程三分支同 SHA 后，仅 fast-forward 本机对应分支并重新构建前端。

## 2026-08-28 治理归档

分支历史统一前保留：

- `archive/macos-pre-unify-20260828`
- `archive/windows-pre-unify-20260828`

归档仅用于追溯旧平台历史，不参与日常开发或自动镜像。
