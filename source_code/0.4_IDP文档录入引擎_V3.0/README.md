# 成都建工 IDP 文档录入引擎 V3.0

V3.0 将结构化业务录入与 RAG 知识问答彻底解耦。

主链路：

```text
文件上传
  -> SHA256 去重
  -> PDF 文字层检测
  -> PyMuPDF / OCR
  -> 文档分类
  -> 规则抽取
  -> Ling-3.0-tiny 语义补充
  -> Schema 校验
  -> 业务规则校验
  -> 自动通过 / 人工复核
  -> PostgreSQL
```

MinerU 不再作为主解析器，仅作为复杂 PDF fallback。

## 目录

- `app/schemas.py`：合同、发票及抽取结果 Pydantic Schema
- `app/parsers.py`：PyMuPDF 优先、OCR 次之、MinerU fallback 的解析路由
- `app/extractors.py`：规则优先 + Ling 语义补充的字段抽取器
- `app/ling_client.py`：Ling-3.0-tiny 本地 OpenAI-compatible 客户端
- `app/validators.py`：金额、税号、付款比例等确定性校验
- `app/pipeline.py`：统一 IDP 状态流转
- `app/main.py`：FastAPI V3 API 入口
- `database/schema_v3.sql`：V3 PostgreSQL 核心表
- `requirements-v3.txt`：V3 最小依赖

## 运行原则

1. 有文字层的 PDF 不 OCR。
2. 能用规则抽取的字段不调用 LLM。
3. Ling-3.0-tiny 只接收候选段落，不默认读取整份合同全文。
4. 每个字段保留置信度和来源。
5. 原始文档、抽取记录和最终业务表分层存储。
6. 低置信字段进入人工复核，不允许静默猜测后入库。

## Ling 本地模型配置

默认按照本机 OpenAI-compatible API 调用：

```text
LING_ENABLED=1
LING_BASE_URL=http://127.0.0.1:8000/v1
LING_MODEL=Ling-3.0-tiny
LING_API_KEY=local
LING_TIMEOUT_SECONDS=120
```

如果推理服务地址不同，只需要修改环境变量，不需要改 IDP 业务代码。

## Windows 16GB 无独显建议

- Ling-3.0-tiny 单实例运行，IDP 侧保持低并发。
- OCR Worker 按需启动，任务完成后可释放。
- MinerU 默认不常驻。
- FastAPI 与 PostgreSQL 常驻，AI/OCR 任务串行或轻量排队。
- 规则抽取优先，只有缺失或语义型字段进入 Ling。
