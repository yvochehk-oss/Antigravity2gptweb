# 成本分类维度

## 基本信息

- dimension_id: cost_category
- version: 1.0
- name_cn: 成本分类
- name_en: Cost Category

## 定义

建筑项目成本的多级分类。

## 一级分类

- DIRECT - 直接成本：可直接计入项目成本的费用
- INDIRECT - 间接成本：需要分摊计入的费用

## 二级分类

直接成本 (DIRECT)：
- MATERIAL - 材料费：建筑材料采购
- LABOR - 人工费：劳务人员工资
- EQUIPMENT - 机械设备费：机械设备租赁/折旧
- SUBCONTRACT - 专业分包：专业分包工程款

间接成本 (INDIRECT)：
- MANAGEMENT - 管理费用：项目部管理费用
- OTHER - 其他费用：其他间接费用

## 内部交易特殊处理

- 内部设备租赁：在计算真实成本时需要抵消内部收入，恢复底层真实经济成本
- 参见 internal_elimination 指标

## 使用场景

- 成本偏差分析
- 成本结构分析
- AI Review 成本异常识别
