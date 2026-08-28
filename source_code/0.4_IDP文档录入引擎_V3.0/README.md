# 成都建工 IDP 文档录入引擎 V3.0

V3.0 是面向合同、发票、收据、银行回单等业务材料的轻量 IDP 数据录入引擎，与 RAG 知识问答解耦。业务录入链路不依赖 BGE-M3 或 Reranker。

## 最终架构

```text
文件
  -> SHA256
  -> 有文字层 PDF: PyMuPDF
  -> 扫描 PDF / 图片: PaddleOCR（按需懒加载）
  -> 文档分类
  -> Python/正则确定性抽取
  -> Ling-3.0-tiny 只补语义型/缺失字段
  -> Python 业务校验
  -> 可选 Granite 4.2 3B 条件风险二审
  -> 自动通过 / 人工复核
  -> PostgreSQL 确认业务表
```

MinerU 不属于最小运行依赖，只保留为复杂排版 PDF 的可选 fallback。

## 模型职责

| 组件 | 默认 | 职责 |
|---|---:|---|
| Ling-3.0-tiny | 开启 | 合同/发票语义字段补全，严格 JSON |
| PaddleOCR | 开启、按需加载 | 扫描 PDF 和图片文字识别 |
| Granite 4.2 3B | **关闭** | 高金额/校验异常材料的第二道风险审计 |
| BGE-M3 | 不使用 | 仅属于 RAG 检索系统 |
| Reranker | 不使用 | 仅属于 RAG，且 RAG 中默认关闭 |

Granite 是审计员，不是录入员。Granite 调用失败不会毁掉已经完成的规则/Ling 抽取结果，而是把材料路由到人工复核。

## 目录

- `app/parsers.py`：PyMuPDF -> OCR -> 可选 MinerU 的解析路由
- `app/ocr.py`：PaddleOCR 懒加载适配器
- `app/extractors.py`：规则优先 + Ling 语义补全
- `app/ling_client.py`：Ling OpenAI-compatible 客户端
- `app/granite_client.py`：Granite 条件风险审计
- `app/validators.py`：金额、税号、付款比例等确定性校验
- `app/pipeline.py`：分类、抽取、校验、审计与状态决定
- `app/repository.py`：PostgreSQL 持久化、业务去重、人工复核
- `app/main.py`：FastAPI 入口
- `database/schema_v3.sql`：V3 数据库结构
- `.env.example`：完整本地配置示例
- `requirements-v3.txt`：最小运行依赖

## Windows 本地启动

在本目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-v3.txt
Copy-Item .env.example .env
```

扫描件/图片需要 OCR 时再安装：

```powershell
pip install paddleocr paddlepaddle
```

初始化 PostgreSQL（数据库需先创建）：

```powershell
psql -d chengdu_construction -f database/schema_v3.sql
```

编辑 `.env` 中的 `DATABASE_URL`，然后启动：

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8930
```

健康检查：`GET /health`。

## 默认配置

```text
LING_ENABLED=1
LING_BASE_URL=http://127.0.0.1:8000/v1
LING_MODEL=Ling-3.0-tiny

OCR_ENABLED=1
OCR_LANG=ch
OCR_PDF_DPI=180

GRANITE_ENABLED=0
GRANITE_BASE_URL=http://127.0.0.1:8001/v1
GRANITE_MODEL=granite-4.2-3b
GRANITE_MIN_AMOUNT=500000

DATABASE_URL=postgresql://postgres:password@127.0.0.1:5432/chengdu_construction
STORE_ORIGINALS=1
IDP_STORAGE_DIR=./storage/originals
```

`.env` 会在 FastAPI 初始化模型客户端之前自动读取。

## API

### 处理材料

`POST /api/v3/documents/process`

上传 PDF/PNG/JPG/TIFF/BMP。响应包括：

- `sha256`
- `document_type`
- `parser`
- `page_count`
- `ocr_confidence`
- `data`
- `validation`
- `audit`
- `status`
- `persistence`

原文只在内部传给持久化层，不直接跟随处理响应返回。

### 文件 SHA 查询

`GET /api/v3/documents/by-sha/{sha256}`

### 待复核列表

`GET /api/v3/reviews?status=pending&limit=100`

### 复核详情

`GET /api/v3/reviews/{review_id}`

### 完成人工复核

`POST /api/v3/reviews/{review_id}/complete`

示例：

```json
{
  "action": "approve",
  "reviewer": "财务复核员",
  "review_data": null
}
```

`approve` 会再次检查业务重复，并写入确认后的 `contracts_v3` / `invoices_v3`；`reject` 会把材料状态转为 `correction`。

## PostgreSQL 分层

- `documents`：原文件元数据、SHA256、解析原文、状态
- `document_extractions`：每一次抽取历史，不覆盖历史结果
- `contracts_v3` / `invoices_v3`：确认后的结构化业务事实
- `document_reviews`：人工复核队列与修订结果

发票在业务层按 `invoice_no + seller_tax_id` 做重复检查。原文件按 SHA256 唯一。

## 状态原则

```text
uploaded / extracting
  -> approved
  -> committed

validation_failed / low confidence / Ling failed / Granite risk
  -> needs_review
  -> approve -> committed
  -> reject  -> correction

DB 写入异常
  -> persistence_failed
```

Ling/Granite 是辅助判断层；确定性金额校验、税号校验、重复判断和最终入库边界由 Python/PostgreSQL 控制。

## 低资源 Windows 原则

1. 有文字层的 PDF 永不 OCR。
2. PaddleOCR 引擎只在真正需要时初始化。
3. Ling 只接收候选段落，不默认把整份合同全文塞给模型。
4. Granite 默认关闭，只对高价值或异常材料按需开启。
5. Ling 与 Granite 建议不要在 16GB CPU 机器上同时高并发常驻。
6. IDP 不加载 BGE-M3/Reranker。
7. PostgreSQL 保存确认事实；RAG 的向量库保存非结构化知识，两条链路不要混在一起。
