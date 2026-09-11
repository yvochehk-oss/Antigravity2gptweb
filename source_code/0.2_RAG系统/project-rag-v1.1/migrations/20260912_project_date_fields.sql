-- 20260912_project_date_fields.sql
-- 新增合同日期与付款日期字段，用于自动生成项目编号：地点首字母 + YYYYMMDD
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS contract_date VARCHAR(20),
    ADD COLUMN IF NOT EXISTS first_payment_date VARCHAR(20);

COMMENT ON COLUMN projects.contract_date IS '甲方签订合同日期 YYYY-MM-DD，用于项目编号自动生成';
COMMENT ON COLUMN projects.first_payment_date IS '第一笔付款日期 YYYY-MM-DD，找不到合同时使用此日期';
