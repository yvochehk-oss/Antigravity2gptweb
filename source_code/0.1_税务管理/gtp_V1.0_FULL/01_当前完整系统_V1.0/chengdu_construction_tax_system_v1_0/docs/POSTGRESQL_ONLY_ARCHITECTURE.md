# PostgreSQL-only 双系统架构约束

Tax 与 ProjectRAG 共享同一个 PostgreSQL `projectrag` 数据库。

- 正式运行、迁移、Facts 查询只允许 PostgreSQL。
- Tax 拥有确定性业务事实表；RAG Analytics 只读这些真实事实，不建立第二套 Accounting SOT。
- `projects`、`entities` 为共享主数据；同义字段通过 PostgreSQL BEFORE trigger 同事务对齐。
- Tax 使用 `alembic_version_tax`，RAG 使用 `alembic_version_rag`。
- 26 个系统内单位为 A01-A11、B01-B10、C01-C02、D01-D03；外部单位只进 `external_parties`。
- 应用启动不调用 `Base.metadata.create_all()` 修改正式 schema；Alembic 是唯一 schema writer。
- AI Review 做 Reasoning，不做 Accounting Calculation；税收优惠必须先通过确定性资格审核。
- 缺少可靠上游的现金预测、综合税负、健康分返回 NULL，不补 0、不伪造精确度。
