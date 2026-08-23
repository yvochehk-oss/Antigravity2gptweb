# regulations_data

> 成都建工 RAG 系统核心法规知识库（**v3 — 合并版**）
>
> 来源：0.2 RAG 抓取 + 0.3 人工整理
> 更新：2026-08-20

四类目录是 `business_role` 业务分类，不是法人主体，也不是
`entity_code`。实际适用的 26 家公司由 Canonical Entity Master 按
`business_role` 映射；法规正文不为业务分类虚构公司。

## 文件总量

| 目录 | 份数 | 来源 |
|------|------|------|
| `national_vat/` | 9 | 0.2 抓取（国家增值税） |
| `national_other/` | 6 | 0.2 抓取（其他税种） |
| `sichuan/` | 4 | 0.2 抓取（四川省） |
| `chengdu/` | 3 | 0.2 抓取（成都市） |
| `business_roles/construction/` | 8 | 0.3 补充（建筑施工业务角色） |
| `business_roles/trade/` | 6 | 0.3 补充（建材商贸业务角色） |
| `business_roles/labor/` | 5 | 0.3 补充（建筑劳务业务角色） |
| `business_roles/equipment/` | 5 | 0.3 补充（设备租赁业务角色） |
| **合计** | **46** | |

## 状态

| 状态 | 数量 | 说明 |
|------|------|------|
| 现行有效/有效 | 39 | 可直接引用 |
| 部分失效 | 7 | 保留，标注失效条款 |
| 全文废止 | 0 | 入库时跳过 |

> 详细状态与文件列表见 `INDEX.md`

## 运行抓取

```bash
pip install httpx pdfplumber --break-system-packages

# 运行
cd regulations_data/scripts
python fetcher.py

# 或从项目根目录
python -m regulations_data.scripts.fetcher
```

## 下一步

1. **清理入库** → 解析 22 份 Markdown 写入 `regulations` 表（跳过全文废止）
2. **向量入库** → 对 `business_roles/*` 结构化文件做条款级切片 + Embedding
3. **PostgreSQL FTS** → 对全文建立全文索引
4. **长上下文检索** → 命中条款 → 自动扩展到章节/全文
5. **缺失法规补充** → 通过北大法宝 MCP API 补充地方规定（见 `DATA_SOURCES.md`）
