# IDP V3.0 macOS / Windows 部署说明

## 分支原则

V3 使用三分支镜像，而不是两套平台源码：

```text
v3.0 == v3.0-macos == v3.0-windows
```

三个分支正常状态必须指向同一个 commit SHA。macOS 与 Windows 的差异只存在于启动脚本、Python 环境、本地模型路径和系统服务管理方式中；`app/`、数据库 schema、API 和业务规则不允许平台分叉。

## macOS

进入 IDP 目录后运行：

```bash
bash START_IDP_MACOS.sh
```

脚本会：

1. 使用 `python3`（可通过 `PYTHON_BIN` 覆盖）；
2. 首次创建 `.venv`；
3. 安装/更新 `requirements-v3.txt`；
4. `.env` 不存在时从 `.env.example` 创建；
5. 默认在 `127.0.0.1:8930` 启动 IDP。

如该机器需要处理扫描件，可在启动前显式安装 OCR：

```bash
IDP_INSTALL_OCR=1 bash START_IDP_MACOS.sh
```

正式环境建议先单独完成依赖安装，再正常启动，避免每次部署时临时下载大型 OCR 依赖。

## Windows

进入 IDP 目录后双击：

```text
START_IDP_WINDOWS.bat
```

或在命令行运行：

```bat
START_IDP_WINDOWS.bat
```

BAT 会调用 `platform\windows\setup_and_start.ps1`。PowerShell 脚本兼容 Windows PowerShell 5.1，会优先使用 `py -3`，其次使用 `python`，并自动建立 `.venv`、安装核心依赖、创建 `.env` 后启动 IDP。

扫描件 OCR 可在当前 CMD 会话中显式启用：

```bat
set IDP_INSTALL_OCR=1
START_IDP_WINDOWS.bat
```

PowerShell 中可使用：

```powershell
$env:IDP_INSTALL_OCR="1"
.\START_IDP_WINDOWS.bat
```

## 公共环境变量

两个平台使用相同 `.env` 语义：

```text
LING_ENABLED=1
LING_BASE_URL=http://127.0.0.1:8000/v1
LING_MODEL=Ling-3.0-tiny

OCR_ENABLED=1
OCR_LANG=ch
OCR_PDF_DPI=180

GRANITE_ENABLED=0
GRANITE_BASE_URL=http://127.0.0.1:8001/v1
GRANITE_MIN_AMOUNT=

DATABASE_URL=postgresql://...
STORE_ORIGINALS=1
IDP_STORAGE_DIR=./storage/originals
```

`IDP_HOST` 与 `IDP_PORT` 是启动脚本变量，默认分别为 `127.0.0.1` 和 `8930`。

## 本地模型原则

- Ling 是 IDP 主语义补全模型；模型服务不可用时规则抽取仍保留，材料转人工复核。
- Granite 默认关闭；只在明确启用并命中风险条件时调用。
- IDP 不加载 BGE-M3 和 Reranker。
- RAG 保留 BGE-M3，Reranker 默认关闭。
- 16GB 无独显 Windows 不建议 Ling + Granite + Reranker 同时常驻。

## PostgreSQL

两个平台共享同一 `database/schema_v3.sql`，不得维护平台独立 schema。

初始化示例：

```text
psql -d chengdu_construction -f database/schema_v3.sql
```

数据库连接差异只通过 `DATABASE_URL` 表达。

## 验证

启动后检查：

```text
GET http://127.0.0.1:8930/health
```

测试依赖与回归测试：

```text
pip install -r requirements-dev-v3.txt
pytest tests -q
```

平台分支同步规则见仓库根目录 `BRANCH_SYNC.md`。
