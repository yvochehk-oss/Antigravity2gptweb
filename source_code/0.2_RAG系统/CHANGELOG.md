# Changelog

## 0.2.0

- PostgreSQL + pgvector 成为默认数据库
- Chunk增加 `vector(1024)` embedding
- 增加HNSW cosine索引
- BGE-M3成为默认语义Embedding
- 增加bge-reranker-v2-m3
- 检索升级为BM25 + pgvector候选融合 + rerank
- 增加DB-backed异步IngestJob worker
- 上传/重解析改为入队执行
- 增加本机文件夹批量扫描导入
- 自动Metadata规则增强
- Knowledge Audit增加资料类别覆盖检查
- 增加macOS PostgreSQL/pgvector与AI依赖安装脚本
- 保持V0.1核心REST API兼容

## 0.1.0

- 独立ProjectRAG MVP
- MinerU、文件去重、Chunk、轻量混合检索和REST API
