# V2.2 测试报告

## 测试环境
- SQLite临时数据库
- FastAPI TestClient
- 宜宾示范工业项目

## 已通过的计算测试
- 项目累计确认收入：23,000,000
- 项目真实底层成本：22,000,000
- 项目经营利润：1,000,000
- 受控体系对外真实成本标记：22,000,000
- EAC计算正常
- 四流匹配生成7组交易对手/业务类别结果
- 乙设备公司示例因履约证据不完整被识别为黄色风险

## 法人独立管理利润测试
2026-08：
- A：按A自身开票及法人账面采购口径计算，示例管理利润为负
- B：独立收入与B真实工资社保成本
- C：独立收入与C真实外采材料成本
- D：独立收入与D真实设备运营成本

项目综合利润与法人利润口径已分离。

## Web页面测试
全部返回HTTP 200：
- /
- /project/1
- /manage
- /matching
- /tax-ledger?period=2026-08
- /risks
- /imports
- /audit
- /docs
- /api/projects/1
- /api/projects/1/matching

## 写入测试
- Web新增真实成本：HTTP 303并进入项目重算
- Web新增发票：HTTP 303
- 操作进入AuditLog

## 当前限制
- Demo税务规则未标记为专业复核，不可直接作为申报依据
- VAT尚未实现完整期初留抵、异地预缴连续抵减
- CIT仍是管理预测
- CSV尚无staging与回滚
- SQLite仅用于Demo，生产建议PostgreSQL
