# macOS V0.2 安装

## 推荐方式

```bash
./setup_v02_mac.sh
./install_mineru_mac.sh
./run.sh
```

## PostgreSQL

V0.2 安装脚本使用 Homebrew：

```bash
brew install postgresql@18 pgvector
brew services start postgresql@18
```

然后创建 `projectrag` 本地开发数据库并执行：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

## MinerU

MinerU 使用独立 `.mineru-venv`，避免和 FlagEmbedding/PyTorch 依赖相互污染。

Apple Silicon 可让 MinerU 自动选择后端。若只使用 CPU：

```bash
MINERU_BACKEND=pipeline ./run.sh
```

## 注意

`setup_v02_mac.sh` 创建的 `projectrag/projectrag` 密码只适合本机开发。正式服务器必须更换强密码并使用受控网络访问。
