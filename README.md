# 成都建工 AI 财税智控、IDP 与 RAG 中枢 (V3.0)

本分支为成都建工智能文档与财税系统 **V3.0**。

V3.0 的核心变化：将“业务字段录入”从 MinerU/RAG 主链路中拆出，新增轻量 IDP 文档录入引擎；RAG 继续负责知识问答，PostgreSQL 负责可信结构化业务数据。

## V3.0 架构

```text
                    原始文件
                       │
              ┌────────┴────────┐
              ▼                 ▼
         IDP 数据录入          RAG 知识问答
              │                 │
      PyMuPDF / OCR          Parser/Chunk
              │                 │
      规则 + 本地 Qwen       Embedding
              │                 │
      Validator             Vector DB
              │                 │
      Review / PGSQL          Qwen QA
```

**MinerU 在 V3.0 中降级为复杂 PDF fallback，不再是业务录入主解析器。**

## 目录结构

- `source_code/0.1_税务管理/`：现有财税智控系统，保留兼容。
- `source_code/0.2_RAG系统/`：现有 RAG 知识引擎，V3.0 暂不破坏其主链路。
- `source_code/0.3_老板端安卓App_天府掌舵/`：移动决策端。
- `source_code/0.4_IDP文档录入引擎_V3.0/`：**V3.0 新核心模块**，负责合同、发票、单据结构化抽取、校验、人工复核与 PostgreSQL 入库。
- `database/`：现有 PostgreSQL 工程资产；V3 IDP 新表定义位于 `source_code/0.4_IDP文档录入引擎_V3.0/database/schema_v3.sql`。
- `project_materials/`：原始合同、发票等项目材料。

## V3.0 IDP 主链路

```text
文件上传
  -> SHA256 去重
  -> PDF 文字层检测
  -> 有文字：PyMuPDF
  -> 无文字：OCR
  -> 极复杂文档：MinerU fallback
  -> 文档分类
  -> Python 规则抽取
  -> Qwen 语义补充
  -> Pydantic Schema
  -> 业务规则校验
  -> 高置信自动通过 / 低置信人工复核
  -> PostgreSQL
```

## 第一阶段支持

- 合同
- 增值税发票 / 普通发票
- 收据
- 银行回单
- 付款及结算类单据（逐步补充 extractor）

## 低资源部署原则

面向 Windows 16GB 内存、无独显环境：

- Qwen 单实例，建议最大并发 `1`。
- OCR 按任务运行，避免长期占 RAM。
- 有文字层 PDF 不 OCR。
- LLM 只接收候选段落，不默认读取整份合同全文。
- MinerU 默认不常驻。

## IDP 快速启动

```bash
cd source_code/0.4_IDP文档录入引擎_V3.0
pip install -r requirements-v3.txt
uvicorn app.main:app --host 0.0.0.0 --port 8930
```

健康检查：`GET /health`

文档处理：`POST /api/v3/documents/process`

> 当前 `v3.0` 分支作为 V3.0 开发基线；`main` 继续保留 V2.0 稳定版本，便于对比和回退。
