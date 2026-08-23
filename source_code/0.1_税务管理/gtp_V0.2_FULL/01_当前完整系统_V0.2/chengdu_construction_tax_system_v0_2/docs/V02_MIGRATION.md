# V0.1 → V0.2 迁移指南

V0.2 在保留 V0.1 业务语义的前提下完成全面重构。迁移 V0.1 数据到 V0.2：

## 1. 数据库

V0.2 沿用 V0.1 的 SQLAlchemy 实体结构，新增字段：

```sql
ALTER TABLE ai_review_jobs ADD COLUMN parse_failed BOOLEAN DEFAULT 0;
ALTER TABLE ai_review_jobs ADD COLUMN actor VARCHAR(80) DEFAULT 'anonymous';
ALTER TABLE ai_review_results ADD COLUMN parse_failed BOOLEAN DEFAULT 0;  -- 实际放在 ai_review_jobs
ALTER TABLE audit_logs ADD COLUMN actor VARCHAR(80) DEFAULT 'anonymous';
ALTER TABLE audit_logs ADD COLUMN ip VARCHAR(45) DEFAULT '';

-- 新表
CREATE TABLE risk_thresholds (
    id INTEGER PRIMARY KEY,
    code VARCHAR(50) UNIQUE NOT NULL,
    description VARCHAR(200),
    ratio NUMERIC(6,4),
    severity VARCHAR(10) DEFAULT 'YELLOW',
    enabled BOOLEAN DEFAULT 1
);
```

只需 `python -m app.seed` 触发 `Base.metadata.create_all()` 即可补齐表结构。

## 2. 金额类型

V0.1 字段 `Float` → V0.2 `Numeric(18, 2)`。

SQLite 列类型加载时自动转换（Python 侧），不会因列类型不一致抛错。
新数据按 Numeric 写入；老数据按 Float 读取后建议人工校验（金额量级差异 < 1e-9）。

## 3. 模板

V0.1 模板文件可平移到 V0.2 `app/templates/`（无需修改）。如有兼容问题可对照 `templates/base.html` 重写 -。

## 4. 启动脚本

V0.2 双 fallback `python3 / python`，已修复 `python3` 默认 Python 2 陷阱。

```bash
cd 01_当前完整系统_V0.2/chengdu_construction_tax_system_v0_2
./run_demo.sh
```

## 5. 测试

V0.1 `tests/smoke.py` + `tests/v01_flow.py` → V0.2 `tests/test_smoke.py` + `tests/test_v02_flow.py` + `tests/test_calc.py` + `tests/test_sanitize.py`。

```bash
DATABASE_URL=sqlite:///./data/v02_test.db python -m pytest -q
```

## 6. 业务规则变更

- 风险阈值：硬编码 → `RiskThreshold` 表
- `_note_category`：`if x in note` → ASCII 词边界正则
- 健康体检：串行 → ThreadPool 并行
- `rebuild_tax_ledger`：`O(4N)` → `O(N)` 单次 GROUP BY
- 审计：单字段 → `actor + ip + message`
- 模板：Jinja2 三方实例 → `app.templates` 单例

## 7. 主体编号

所有文档中的 E 已统一为 D（设备租赁公司）。

## 8. 不会破坏

- 业务结果（数字相同）
- 主体边界（ABCD / 甲乙丙丁）
- AI 安全边界（AI 不得修改确定性结果）
- 整改闭环（任务状态机相同）

## 9. 推荐升级路径

1. 复制 V0.1 数据库到 V0.2 `data/demo.db`
2. 启动 V0.2 → `python -m app.seed` 触发 create_all
3. 跑 `pytest` 验证 13 个用例
4. UI 关键页面手动回归：驾驶舱 / 四流 / 税务 / AI 体检 / 整改
5. 旧版 V0.1 保留 6 个月对照期，再下线。