# 对接建筑项目经营税务系统

## 原则

项目管理系统掌握结构化经营事实；ProjectRAG 掌握原始资料证据。两套数据库独立。

## 建议调用链

```text
用户：检查宜宾项目D设备环节
    |
    v
项目管理系统
    |-- SQL：合同/发票/付款/真实成本/EAC
    |-- 本地规则：四流、税务、风险
    |
    +--> POST ProjectRAG /api/v1/retrieve
              project=YB001
              entity=D
              category=equipment
              query=台班、结算、合同履约...
    |
    v
Evidence Pack
    |
    v
项目管理系统 AI Review
    |
    v
问题 + SQL证据 + 文档证据 + 建议
```

## RAGClient 建议接口

```python
class RAGClient:
    def sync_project(...): ...
    def retrieve(project_code, query, filters, top_k=10): ...
    def get_document(document_id): ...
    def original_url(document_id): ...
```

V0.1 已经提供以上服务对应的 REST API。

## V0.2 compatibility

V0.2保持 `/api/v1/projects/sync`、`/api/v1/retrieve`、`/api/v1/query` 的主要请求结构兼容。
新增的 `rerank` 字段有默认值，因此V0.1客户端无需修改即可继续调用。
上传现在是异步的：调用方如需要等待索引完成，应读取上传返回的 `job_id`，轮询 `GET /api/v1/jobs/{job_id}`。
