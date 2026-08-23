# PostgreSQL-only V2 修改报告

## 目标
Tax 与 RAG 只使用共享 PostgreSQL `projectrag` 作为业务数据库；SQLite 不再作为运行、同步、迁移或测试回退路径。

## Tax 本分支修改
- 数据库连接 fail-closed：缺少 `DATABASE_URL` 或非 PostgreSQL 后端直接拒绝启动。
- Alembic 使用独立版本表 `alembic_version_tax`。
- 新增共享主数据 migration：44 家内部主体闭集、项目/主体 alias 字段、事务级 alias trigger。
- canonical 内部主体统一为 A01-A14 / B01-B14 / C01-C07 / D01-D09；A/B/C/D 只表示业务角色。
- 系统外单位使用 `external_parties`，不进入 44 家内部主体主表。
- seed 改为 PostgreSQL 幂等主数据 bootstrap，不再清库重写项目交易，也不读取旧 RAG SQLite。
- 四流付款类别文本猜测只作为风险提示，不再作为发票/履约证据。
- AI 对西部大开发 15% 等优惠只给资格核验候选；未审核规则不得进入确定性税额计算。
- PostgreSQL 集成测试必须显式提供数据库名包含 `test` 的 `TEST_DATABASE_URL`，没有 SQLite fallback。
- 清理历史 SQLite DB、一次性 patch/fix/migrate 脚本和 SQLite 专属迁移测试。

## 验证边界
本分支已完成 Python 静态编译和纯逻辑回归；当前 ChatGPT 执行环境没有 PostgreSQL/pgvector 服务，因此真实数据库 migration 必须先在 disposable `projectrag_test` 中执行 Tax migrations → RAG migrations → reconciliation，再考虑合并。

## 不变原则
AI Review 只基于确定性计算结果和 RAG 证据进行 Reasoning，不重新计算 Accounting 数字；证据不足的指标保持未知/降级，不制造假精确值。
