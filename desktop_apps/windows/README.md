# 成都建工 V3.0 Windows 控制台

这是一个原生 .NET 8 WinForms 系统托盘应用，发布后文件名为“成都建工控制台.exe”。应用常驻 Windows 右下角系统托盘，关闭控制台不会停止业务服务。

## 功能

- 每 5 秒检查本地语言模型、税务系统、资料知识系统、文档录入引擎和老板驾驶舱。
- 本地语言模型必须同时满足 GET http://127.0.0.1:8930/health 与 GET /v1/models 已确认，并通过 .local_llm.pid 或项目进程归属校验；不以端口占用作为唯一依据。
- 启动全部服务、重启全部服务、停止全部服务。
- 打开税务系统、资料知识系统和老板驾驶舱。
- 查看运行日志、切换登录后自动运行。
- 停止操作只处理经过项目路径、命令行、端口和本次启动记录确认的项目进程；PostgreSQL 5432 不在控制范围内。
- 所有启动窗口隐藏，启停操作使用串行锁，重复点击不会并发启动。
- UI 优先使用本机 HymOS / HymOS Sans SC / HarmonyOS Sans SC，再回退到 Windows 中文字体；不下载字体。

## 构建

在 Windows PowerShell 中运行：

    cd .\V3.0\desktop_apps\windows
    .\build.ps1 -RunSmokeTests

也可以双击或在命令提示符中运行：

    build.cmd -RunSmokeTests

发布结果位于 publish\\win-x64\\成都建工控制台.exe。构建使用 .NET 8 SDK 的 WinForms 桌面目标，不需要额外的在线运行时服务。

当前 macOS 工作机可以运行：

    ./build.sh

该脚本先运行离线静态自检；如果本机没有 .NET SDK，会明确报告 Windows 构建限制并返回成功，不会安装任何依赖。

## 项目根目录

控制台从自身目录向上查找同时包含 windows_scripts 与 source_code 的目录作为 V3.0 根目录，因此不绑定某台机器的绝对路径。若发布到独立目录，可在当前用户环境变量中设置：

    CHENGDU_JIANGONG_ROOT=D:\成都建工\V3.0

环境变量只用于定位本地项目，不会写入日志或发送到外部服务。

## 启停语义

控制台自有 Windows 适配器直接调用项目已经存在的 Windows Python、模型运行时和老板端前端运行命令。它不调用会打开多个交互式窗口的旧一键脚本，也不执行依赖安装、模型下载、数据库迁移或数据库停止。

- 本地语言模型：使用已存在的 llama-server.exe 和已存在的 GGUF 模型，优先 Ling 3.0，缺失时使用已存在的 Qwen 2B；两者都缺失则拒绝启动。
- 税务、资料知识、文档录入：使用各自已存在的 .venv\\Scripts\\python.exe 运行 Uvicorn。
- 老板驾驶舱：在现有工程目录执行 npm run preview，不运行安装命令。
- 停止：先尝试关闭拥有窗口的项目进程，随后仅对再次确认归属的项目进程执行进程树停止；无法确认归属时跳过并写入安全日志。
- 退出托盘：保留所有业务进程运行；需要停止时使用菜单中的“停止全部服务（保留数据库）”。

旧的 .bat 文件、后端、中间件、数据库、迁移和业务 UI 均不由本目录修改。

## 日志与隐私

日志写入当前用户目录下的 ChengduConstructionController\\controller.log，写入前会隐藏密码、令牌、密钥、授权头和 Cookie 等值，并限制日志大小。日志不发送到网络。
