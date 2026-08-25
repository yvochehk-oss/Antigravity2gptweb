"""V0.2: 统一常量定义。"""
from __future__ import annotations

from .domain.entities import CANONICAL_ENTITY_CODES

# 业务角色（不是法人代码）。法人代码必须来自 canonical Entity master
# （A01-A11、B01-B10、C01-C02、D01-D03）。外部交易方由
# app.models.ExternalParty 单独维护，不能加入本集合。
BUSINESS_ROLES: frozenset[str] = frozenset({"A", "B", "C", "D"})

# 兼容既有展示/报表调用方的角色标签；A/B/C/D 仅表示业务职能。
ENTITY_ROLES: dict[str, str] = {
    "A": "建筑施工企业",
    "B": "商贸物资公司",
    "C": "建筑劳务公司",
    "D": "机械租赁公司",
}

# 角色到现有真实企业的主展示主体。此映射只用于展示/兼容旧报表，
# 不能被当作跨模块过滤全集；计算引擎应从 entities 表读取 active 主数据。
ROLE_PRIMARY_ENTITY: dict[str, str] = {
    "A": "A08",
    "B": "B01",
    "C": "C01",
    "D": "D01",
}

# 成本一级科目（代码值，中文仅用于界面展示）
COST_CATEGORIES: tuple[str, ...] = (
    "材料",
    "劳务",
    "设备",
    "专业分包",
    "项目管理",
    "税金",
)

# 业务类型 → 成本科目（代码值）
KIND_TO_CATEGORY: dict[str, str] = {
    "material_acceptance": "材料",
    "labor_settlement": "劳务",
    "equipment_shift": "设备",
}

# AI 检查范围（与 seed/模板 scope 名称保持一致）
SCOPES: dict[str, str] = {
    "overview": "项目总体经营",
    "budget": "预算与成本偏差",
    "contract": "合同",
    "fulfillment": "履约证据",
    "invoice": "发票",
    "cashflow": "资金与付款",
    "cost": "真实成本穿透",
    "tax": "税务管理测算",
    "eac": "EAC预计完工",
    "risk": "风险事件",
    "material": "材料",
    "labor": "劳务",
    "equipment": "设备",
    "subcontract": "专业分包",
    "whole_project": "整个项目",
}

# 类别范围映射
CATEGORY_SCOPE: dict[str, str] = {
    "材料": "material",
    "劳务": "labor",
    "设备": "equipment",
    "专业分包": "subcontract",
}

# 整改任务状态 → 中文标签
TASK_STATUS_LABELS: dict[str, str] = {
    "open": "待处理",
    "in_progress": "进行中",
    "done": "已完成",
    "rechecked": "待复核",
    "verified": "已核实",
    "closed": "已关闭",
}

# 体检档位
HEALTH_PROFILES: dict[str, list[str]] = {
    "quick": ["overview", "risk", "eac"],
    "standard": [
        "contract", "fulfillment", "invoice", "cashflow",
        "cost", "tax", "eac", "risk",
    ],
    "deep": [
        "contract", "fulfillment", "invoice", "cashflow",
        "cost", "tax", "eac", "risk",
        "material", "labor", "equipment", "subcontract",
    ],
}

# 整改任务状态机
TASK_STATUSES: frozenset[str] = frozenset({
    "open", "in_progress", "done", "rechecked", "verified", "closed",
})

# 风险等级排序
RISK_ORDER: dict[str, int] = {
    "UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4,
}

# 优先级
PRIORITIES: tuple[str, ...] = ("P0", "P1", "P2", "P3")

# 严重度 → 中文标签（用于界面展示）
SEVERITY_LABELS: dict[str, str] = {
    "RED": "高危",
    "YELLOW": "关注",
    "UNKNOWN": "未知",
    "LOW": "低危",
    "MEDIUM": "中等",
    "HIGH": "高危",
    "CRITICAL": "严重",
}

# 交易分类代码 → 中文标签
CATEGORY_LABELS: dict[str, str] = {
    "material": "材料",
    "labor": "人工",
    "equipment": "设备",
    "subcontract": "分包",
    "project_management": "项目管理",
    "未分类": "未分类",
}

# 风险代码 → 中文标签（用于界面展示）
RISK_CODE_LABELS: dict[str, str] = {
    "FOUR_STREAM_MISMATCH": "四流不匹配",
    "MISSING_TAX_RATE": "发票缺失税率",
    "EQUIPMENT_RATE_REVIEW": "设备税率需复核",
    "RAG_EVIDENCE_LOGISTICS_GAP": "物资物流入库凭据链缺口",
    "CROSS_REGION_FILING_AUDIT": "跨区施工预缴与所得税分摊核查",
    "invoice_over_contract": "发票超额超合同",
    "paid_over_invoice": "付款超额超发票",
    "fulfilled_over_contract": "履约超额超合同",
}

# 风险代码常量（calc/risk.py 与 RISK_CODE_LABELS 的 key 同源，避免飘移）
RISK_CODE_FOUR_STREAM_MISMATCH = "FOUR_STREAM_MISMATCH"
RISK_CODE_MISSING_TAX_RATE = "MISSING_TAX_RATE"
RISK_CODE_EQUIPMENT_RATE_REVIEW = "EQUIPMENT_RATE_REVIEW"

# 默认风险阈值（可被 RiskThreshold 表覆盖）
DEFAULT_RISK_THRESHOLDS: dict[str, float] = {
    "invoice_over_contract": 1.05,
    "paid_over_invoice": 1.05,
    "fulfilled_over_contract": 1.10,
}

# 输入预览最大字符（限制异常超长输入并控制日志/响应体体积）
INPUT_PREVIEW_MAX = 12_000
RAW_RESPONSE_MAX = 100_000
