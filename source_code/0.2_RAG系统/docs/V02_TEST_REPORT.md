# ProjectRAG V0.2 测试报告

## 自动测试

当前代码使用 SQLite 兼容测试模式完成 5 个自动用例：

1. 外部项目幂等同步
2. Markdown异步入队 → Chunk → 索引
3. SHA-256重复识别
4. 元数据过滤 + 混合检索
5. 文件夹批量导入 + Knowledge Audit
6. MinerU content_list页码/标题保持（包含在测试组）
7. Fake MinerU CLI完整子进程适配（包含在测试组）
8. Web首页/Search/Health HTTP检查（包含在测试组）

结果：`5 passed`。

## PostgreSQL专项说明

当前执行环境没有运行 PostgreSQL 服务，因此没有伪造“真实PostgreSQL集成测试已通过”的结论。

V0.2生产路径代码已经实现：

- `pgvector.sqlalchemy.Vector(1024)`
- `CREATE EXTENSION IF NOT EXISTS vector`
- HNSW `vector_cosine_ops`
- SQLAlchemy `cosine_distance()`候选查询
- Psycopg 3连接串

在用户Mac执行 `setup_v02_mac.sh` 后，可通过 `/api/v1/health` 检查数据库与pgvector扩展状态。


## Uvicorn运行验收

使用 SQLite 兼容模式实际启动 V0.2 Uvicorn 服务：

- `GET /api/v1/health` → HTTP 200，version = 0.2.0
- `GET /` → HTTP 200
- `GET /search` → HTTP 200

同时对所有 macOS shell 安装/启动脚本执行 `bash -n` 语法检查，均通过。
