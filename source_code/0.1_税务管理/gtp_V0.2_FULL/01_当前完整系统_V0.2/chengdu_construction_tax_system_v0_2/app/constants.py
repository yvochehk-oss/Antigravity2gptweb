"""V0.2: 统一常量定义。"""
from __future__ import annotations

# 主体边界
INTERNAL: frozenset[str] = frozenset({"A", "B", "C", "D"})
EXTERNAL: frozenset[str] = frozenset({"甲", "乙", "丙", "丁"})

# 系统内主体职能
ENTITY_ROLES: dict[str, str] = {
    "A": "施工企业",
    "B": "劳务公司",
    "C": "商贸公司",
    "D": "设备租赁公司",
    "甲": "第三方劳务公司",
    "乙": "第三方设备租赁公司",
    "丙": "外部材料供应商",
    "丁": "外部专业分包/其他供应商",
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
    "invoice_over_contract": "发票超额超合同",
    "paid_over_invoice": "付款超额超发票",
    "fulfilled_over_contract": "履约超额超合同",
}

# 默认风险阈值（可被 RiskThreshold 表覆盖）
DEFAULT_RISK_THRESHOLDS: dict[str, float] = {
    "invoice_over_contract": 1.05,
    "paid_over_invoice": 1.05,
    "fulfilled_over_contract": 1.10,
}

# 输入预览最大字符（防止 SQLite TEXT 溢出）
INPUT_PREVIEW_MAX = 12_000
RAW_RESPONSE_MAX = 100_000