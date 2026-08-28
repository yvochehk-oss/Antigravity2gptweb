# V3 本地模型运行策略

本策略用于成都建工 V3 在低资源本地 Windows 机器上的默认部署。

## 默认组合

```text
RAG 检索:
  BM25 + pgvector(BGE-M3)
  -> Reranker 默认关闭
  -> Ling-3.0-tiny 生成答案

IDP 业务录入:
  PyMuPDF/OCR -> 规则 -> Ling-3.0-tiny -> Python 校验 -> PostgreSQL
  不使用 BGE-M3 / Reranker
```

### BGE-M3

BGE-M3 只负责 RAG 文档/查询 embedding。它不参与合同、发票字段抽取。

### Reranker

Reranker 是可选精排层，不是 RAG 正确运行的前提。V3 默认：

```text
PROJECT_RAG_RERANKER_ENABLED=0
```

关闭时 `app/services/reranker.py` 直接保持混合检索原顺序，不导入 torch/transformers，也不加载 BGE reranker 模型。

只有经过真实检索评测确认 Top-K 排序质量不足时才建议开启：

```text
PROJECT_RAG_RERANKER_ENABLED=1
PROJECT_RAG_RERANKER_BACKEND=bge_v2_m3
PROJECT_RAG_RERANKER_MODEL=<local path or configured model>
```

### Ling-3.0-tiny

默认 OpenAI-compatible 本地端点：

```text
RAG_LLM_BASE_URL=http://127.0.0.1:8000/v1
RAG_LLM_MODEL=Ling-3.0-tiny
RAG_LLM_API_KEY=local
```

Ling 负责 RAG 最终回答；IDP 中同一模型负责语义字段补全。

### Granite 4.2 3B

Granite 不属于 RAG 检索模型。它仅在 IDP 中作为可选的条件风险二审，默认关闭，不与 Ling 同时高并发常驻。

## 低资源原则

1. BGE-M3 常用于 RAG embedding/retrieval；不要把它复制到 IDP 进程。
2. Reranker 默认关闭，减少第二套 Transformer 的内存占用。
3. Ling 与 Granite 使用独立 OpenAI-compatible 服务，按业务需要启停。
4. 16GB CPU 机器优先串行 AI 请求，避免多个模型同时高并发。
5. 模型职责分离：embedding、生成、审计不要用一个进程混装。
