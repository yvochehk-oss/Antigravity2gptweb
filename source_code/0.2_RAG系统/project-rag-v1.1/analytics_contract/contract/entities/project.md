# 项目实体

## 基本信息

- entity_id: project
- source_table: projects
- version: 1.0

## 数据库字段

- id (INT): 主键
- project_code (VARCHAR): 项目编码（唯一），如 YB001
- name (VARCHAR): 项目名称，如 宜宾住宅项目
- entity_code (VARCHAR): 所属真实法人实体编码，如 `A01`；必须引用 Canonical Entity Master
- business_role (VARCHAR, derived): 由实体主数据映射的业务角色，仅用于分析分组，不作为实体标识
- external_system (VARCHAR): 外部系统标识，如 SAP
- external_project_id (VARCHAR): 外部系统项目ID
- status (VARCHAR): 项目状态，如 ACTIVE
- contract_amount (DECIMAL): 合同金额
- start_date (DATE): 开工日期
- expected_end_date (DATE): 预计完工日期
- location (VARCHAR): 项目地点
- project_type (VARCHAR): 项目类型

## 主键

- 主键: id
- 业务键: project_code

## 与 Metrics 的关系

- 所有 project 粒度指标的来源实体
