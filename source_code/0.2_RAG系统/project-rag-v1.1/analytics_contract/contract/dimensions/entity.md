# 法人实体维度（Canonical Entity Dimension）

## 基本信息

- dimension_id: entity
- version: 1.0
- name_cn: 法人实体
- name_en: Legal Entity
- owner: finance

## 定义

项目中涉及的真实组织实体，用于区分不同法律实体及其分支机构的财务数据。
`entity_code` 必须来自当前实体主数据（Canonical Entity Master），不得由业务角色、项目简称或外部交易对手名称代替。

## Canonical entity code

当前有效编码范围为：

- `A01`–`A11`
- `B01`–`B10`
- `C01`–`C02`
- `D01`–`D03`

示例（名称来自实体主数据）：

| entity_code | entity_name | business_role | legal_entity | parent_entity_code |
| --- | --- | --- | --- | --- |
| `A01` | 中镌（湖北）建筑有限公司 | `A` / construction | true | — |
| `A04` | 四川屹明汇建设工程有限公司重庆分公司 | `A` / construction | false | `A03` |
| `B01` | 四川乾润和贸易有限公司 | `B` / trade | true | — |
| `C01` | 四川本盛劳务有限公司 | `C` / labor | true | — |
| `D01` | 四川乾润和机械设备租赁有限公司 | `D` / equipment | true | — |

`business_role` 是分析分组标签，不是法人编码。`A`、`B`、`C`、`D` 只能出现在该字段（分别表示 construction、trade、labor、equipment），不能作为 `entity_code` 或公司名称。

## 内部交易边界

内部交易的两端必须分别解析为实体主数据中的真实 `entity_code`，且双方均为有效内部法律实体；判断依据是实体主数据，而不是单字角色。例如 `A08`（四川锐宝建设工程有限公司）向 `B01`（四川乾润和贸易有限公司）开票，才是可进入内部抵消流程的内部交易。

外部交易对手不创建为 `Entity`。其名称、统一社会信用代码和交易关系应通过 `external_party_id` / `external_party_name` / `external_credit_code` 等外部交易方字段保存；未识别的对手方必须保持 unresolved，不得伪造法人编码。

## 内部交易抵消规则

真实内部实体之间：
- 内部收入成本抵消
- 恢复交易链条底层真实经济成本

外部交易对手：
- 永远按外部经济成本处理
- 不做内部实体穿透

分支机构（例如 `A04`）是否在法人层汇总，必须按 `parent_entity_code` 和具体指标契约执行；不能把分支机构编码改写成业务角色。

## 使用场景

- 合并报表编制
- 内部交易抵消
- 税务计算
- AI Review 实体级别分析
