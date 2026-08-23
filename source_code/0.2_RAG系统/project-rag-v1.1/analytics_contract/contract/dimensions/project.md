# 项目维度

## 基本信息

- dimension_id: project
- version: 1.0
- name_cn: 项目
- name_en: Project
- owner: PMO

## 定义

建筑施工项目的唯一标识，是 analytics 层的主要粒度。

## 项目状态

- ACTIVE - 进行中：正在施工的项目
- COMPLETED - 已完工：已完成竣工验收的项目
- SUSPENDED - 已暂停：因故暂停的项目
- TERMINATED - 已终止：已终止合同的项目

## 重要字段

- project_code: 项目唯一编码（如 YB001、CD002）
- project_name: 项目名称
- entity_code: 所属真实法人实体编码（如 `A01`），引用 Canonical Entity Master
- business_role: 从实体主数据派生的业务角色；不得替代 entity_code
- status: 项目状态
- location: 项目地点
- project_type: 项目类型（房屋建筑/市政/安装等）
- contract_amount: 合同金额
- start_date: 开工日期
- expected_end_date: 预计完工日期

## 使用场景

- 所有 analytics 指标的核心维度
- AI Review 项目级别分析
