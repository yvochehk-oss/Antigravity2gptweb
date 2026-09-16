# Contributing

平台实现只在对应分支修改：Windows 使用 `windows`，macOS 使用 `macos`。不要直接在 `main` 添加平台运行代码。

每个改动应说明目标平台、浏览器、Node/Python 版本、测试命令和结果。提交前不得包含凭据、浏览器 profile、真实 ChatGPT 会话 URL、私有仓库地址或未脱敏日志。

桥接器必须保持三项不变量：目标 URL 精确绑定、同一 Tab 不能并发写入、GitHub 远端改码而本地只做 fast-forward 同步与验收。
