# ChatGPT Web Reasoner

一个面向桌面 Agent 的双平面协作工具：ChatGPT 网页端通过已授权的 GitHub 工具规划、修改并推送代码；本地 bridge 只同步远端提交、运行测试并把客观证据回传审查。

> 这是社区维护的浏览器自动化项目，不隶属或不代表 OpenAI。请只在你拥有权限的 ChatGPT、GitHub 仓库和本机项目上使用。

## 选择平台

| 平台 | 分支 | 运行时 | 浏览器通道 |
| --- | --- | --- | --- |
| Windows 10/11 | [`windows`](https://github.com/yvochehk-oss/Antigravity2gptweb/tree/windows) | Node.js 18+，无 npm 依赖 | Chrome / Edge CDP |
| macOS | [`macos`](https://github.com/yvochehk-oss/Antigravity2gptweb/tree/macos) | Python 3 | Safari AppleScript；也支持 Chromium CDP |

分支是可安装的 Skill 实现；`main` 只保存公开项目的入口文档和治理文件，避免两套平台源码重复维护。

## 下载与安装

Windows：

```powershell
git clone --depth 1 --branch windows https://github.com/yvochehk-oss/Antigravity2gptweb.git chatgpt-web-reasoner
cd chatgpt-web-reasoner
.\check_env_windows.bat
.\start_edge_cdp.bat
```

macOS：

```bash
git clone --depth 1 --branch macos https://github.com/yvochehk-oss/Antigravity2gptweb.git chatgpt-web-reasoner
cd chatgpt-web-reasoner
python3 scripts/safari_chatgpt.py --help
```

打开并登录 ChatGPT，进入目标会话后，将最终的 `https://chatgpt.com/c/...` URL 显式传给 bridge。不要把 Cookie、密码、API Key、私有仓库地址或完整敏感日志提交到 Issue。

## 工作方式

```text
Custom GPT + GitHub 工具：规划、远端改码、提交、推送
                     ↓
本地 bridge：git pull --ff-only、测试、JSONL 证据
                     ↓
Custom GPT：APPROVED / NEEDS_FIX / BLOCKED
```

本地 bridge 在工作区不干净或本地/远端 HEAD 不一致时必须停止，不会把网页回复直接写入业务代码。

## 当前边界

- Windows 分支是纯 Node.js，支持 Node bridge 与单任务编排器。
- macOS 分支包含 Safari 与 Python Chromium bridge，且拥有完整的方案 refine / 批量任务编排能力。
- 浏览器 UI、ChatGPT 权限、GitHub Custom GPT 工具的可用性均可能变化；先在非生产仓库验证。

## 反馈与贡献

- 运行异常：使用 [Bug report](https://github.com/yvochehk-oss/Antigravity2gptweb/issues/new?template=bug_report.md)。
- 使用建议：使用 [Feature request](https://github.com/yvochehk-oss/Antigravity2gptweb/issues/new?template=feature_request.md)。
- 安全问题不要公开提交，见 [SECURITY.md](SECURITY.md)。
- 提交前请保留平台边界、运行相应语法检查，并避免提交任何凭据或真实会话 URL。

## License

[MIT](LICENSE)
