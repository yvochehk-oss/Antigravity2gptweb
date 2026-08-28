# 成都建工 AI 财税智控、IDP 与 RAG 中枢 (V3.0)

本分支为成都建工智能文档与财税系统 **V3.0**。

V3.0 将“业务字段录入”和“知识检索问答”明确拆成两条链路：IDP 负责合同、发票、回单等结构化业务事实进入 PostgreSQL；RAG 负责非结构化知识检索与问答。这样可以在 Windows 16GB、无独显环境下降低常驻模型数量和内存压力。

## V3.0 最终架构

```text
                         原始文件
                            │
                 ┌──────────┴──────────┐
                 ▼                     ▼
              IDP 业务录入           RAG 知识问答
                 │                     │
        PyMuPDF / 按需 OCR          Parser / Chunk
                 │                     │
        Python 确定性规则           BGE-M3 Embedding
                 │                     │
          Ling-3.0-tiny              pgvector + BM25/RRF
          语义字段补全                 │
                 │              Optional Reranker
          Pydantic/业务校验           默认关闭
                 │                     │
        ┌────────┴────────┐        Ling-3.0-tiny
        │                 │          生成答案
   自动确认/PGSQL      条件风险二审
                          │
                   Granite 4.2 3B
                     默认关闭/按需
                          │
                       人工复核
```

**模型职责固定如下：**

- `Ling-3.0-tiny`：IDP 语义字段补全 + RAG 最终生成模型。
- `Granite 4.2 3B`：仅作为可选第二道风险审计，不参与普通字段录入。
- `BGE-M3`：仅用于 RAG embedding，不进入 IDP。
- `BGE reranker`：仅用于 RAG，`PROJECT_RAG_RERANKER_ENABLED=0` 默认关闭，需要时再开启。
- `MinerU`：复杂 PDF 的可选 fallback，不再是 IDP 主链路常驻组件。

## 目录结构

- `source_code/0.1_税务管理/`：现有财税智控系统，保留兼容。
- `source_code/0.2_RAG系统/`：RAG 知识引擎；V3 保留 BGE-M3，Reranker 默认关闭，生成模型默认 Ling-3.0-tiny。
- `source_code/0.3_老板端安卓App_天府掌舵/`：移动决策端。
- `source_code/0.4_IDP文档录入引擎_V3.0/`：**V3.0 新核心模块**，负责结构化抽取、校验、去重、人工复核与 PostgreSQL 入库。
- `database/`：现有 PostgreSQL 工程资产；V3 IDP 新表定义位于 `source_code/0.4_IDP文档录入引擎_V3.0/database/schema_v3.sql`。
- `project_materials/`：原始合同、发票等项目材料。

## V3.0 IDP 主链路

```text
文件上传
  -> SHA256 去重
  -> 重复文件默认直接返回已有结果
  -> force=true 时允许重新抽取并保留 extraction 历史
  -> PDF 文字层检测
  -> 有文字：PyMuPDF
  -> 无文字/图片：PaddleOCR 按需加载
  -> 文档分类
  -> Python 规则抽取
  -> Ling-3.0-tiny 语义补充
  -> Pydantic/业务规则校验
  -> 可选 Granite 条件风险二审
  -> 高置信自动确认 / 异常进入人工复核
  -> PostgreSQL
```

数据库配置并开启原件存储后，还支持按 `document_id` 对历史材料重新处理，不需要用户再次上传文件。

## 第一阶段支持

- 合同
- 增值税发票 / 普通发票
- 收据
- 银行回单
- 付款及结算类单据（逐步补充 extractor）

## 低资源部署原则

面向 Windows 16GB 内存、无独显环境：

- IDP 默认语义模型为 `Ling-3.0-tiny`，单实例、低并发运行。
- OCR 仅在扫描件/图片需要时初始化；有文字层 PDF 不 OCR。
- LLM 只接收候选段落，不默认读取整份合同全文。
- Granite 默认关闭，仅在高金额、规则异常等场景按需开启。
- IDP 不加载 BGE-M3 或 Reranker。
- RAG 保留 BGE-M3；Cross-Encoder Reranker 默认关闭，不加载第二套 Transformer。
- MinerU 默认不常驻。

## IDP 快速启动

从 V3.0 根目录运行 `./start_all.sh install` 会同时准备 Tax、RAG、IDP 与老板端依赖；随后运行 `./start_all.sh start` 会启动全部本地服务。IDP 默认监听 `8933`，本地 LLM 仍监听 `8930`，两者不会冲突。

```bash
cd source_code/0.4_IDP文档录入引擎_V3.0
pip install -r requirements-v3.txt
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8933
```

Windows PowerShell 可使用：

```powershell
Copy-Item .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8933
```

健康检查：`GET /health`

文档处理：`POST /api/v3/documents/process`

强制重新处理相同 SHA：`POST /api/v3/documents/process?force=true`

按已存原件重新处理：`POST /api/v3/documents/{document_id}/reprocess`

人工复核队列：`GET /api/v3/reviews?status=pending`

## RAG V3 模型配置

`source_code/0.2_RAG系统/project-rag-v1.1/.env.example` 的默认模型策略：

```text
PROJECT_RAG_EMBEDDING_BACKEND=bge_m3
PROJECT_RAG_RERANKER_ENABLED=0
RAG_LLM_BASE_URL=http://127.0.0.1:8000/v1
RAG_LLM_MODEL=Ling-3.0-tiny
```

RAG 运行时代码同样以 `PROJECT_RAG_RERANKER_ENABLED=0` 为默认；关闭时不会加载 reranker 的 torch/transformers 模型。

> 当前 `v3.0` 分支作为 V3.0 开发基线；`main` 继续保留 V2.0 稳定版本，便于对比和回退。
