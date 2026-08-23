# 测试 Fixture 数据

## 项目测试数据 (YB001)

project:
  project_code: YB001
  project_name: 宜宾住宅项目
  entity_code: A01
  entity_name: 中镌（湖北）建筑有限公司
  business_role: A
  contract_amount: 50000000
  start_date: "2024-01-15"
  expected_end_date: "2026-06-30"

# 收入数据
revenue:
  recognized_revenue: 12500000  # 已确认收入

# 成本数据
cost:
  raw_cost: 11200000            # 原始成本
  internal_elimination: 200000  # 内部抵消
  real_economic_cost: 190000   # 真实经济成本
  real_project_cost: 11100000  # 真实项目成本

# 收款数据
collection:
  collected_amount: 8875000     # 已收款
  planned_collection: 10000000  # 计划收款

# EAC 数据
eac:
  bac_cost: 45000000            # 完工预算
  bac_revenue: 50000000        # 预算收入
  earned_value: 12000000        # 挣值
  actual_cost: 11800000         # 实际成本
  cpi: 1.0169                  # 成本绩效指数
  eac_cost: 44262000            # 预计完工成本
  eac_revenue: 48780000         # 预计完工收入
  eac_profit: 4518000           # 预计利润
  eac_margin: 0.0926            # 预计利润率

# 现金流数据
cashflow:
  current_cash: 3000000         # 当前现金
  inflow_30d: 2000000           # 30天流入
  outflow_30d: 8000000           # 30天流出
  cash_gap_30d: 3000000         # 30天缺口

## 预期计算结果

expected_results:
  # 项目真实利润
  real_profit:
    value: 1400000              # 12500000 - 11100000
    formula: "recognized_revenue - real_project_cost"
    passes: true

  # EAC 利润率
  eac_margin:
    value: 0.0926
    formula: "eac_profit / eac_revenue"
    passes: true

  # 回款率
  collection_rate:
    value: 0.71                 # 8875000 / 12500000
    formula: "collected_amount / recognized_revenue"
    passes: true
    warning_threshold: 0.5
    critical_threshold: 0.3

  # 30天现金缺口
  cash_gap_30d:
    value: 3000000              # 正值表示缺口
    formula: "outflow - inflow - current_cash"
    passes: false               # 存在缺口
    warning_threshold: 0
