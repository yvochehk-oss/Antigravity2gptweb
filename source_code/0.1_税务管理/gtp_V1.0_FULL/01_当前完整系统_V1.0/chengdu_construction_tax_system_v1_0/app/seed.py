"""Bootstrap canonical master data and optional demo data into PostgreSQL.

Tax and RAG share the same PostgreSQL database.  This seed never reads from a
secondary database and never creates schema objects; Alembic owns schema state.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, inspect, text

_logger = logging.getLogger(__name__)

_ENVIRONMENT = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).strip().lower()
_SEED_MODE = os.getenv("TAX_SEED_MODE", "production").strip().lower()

from .auth import hash_password
from .db import SessionLocal
from .domain.entities import CANONICAL_ENTITY_CODES
from .models import (
    AIModelEndpoint,
    AIPromptTemplate,
    Budget,
    CashFlow,
    Contract,
    CostAccount,
    Entity,
    EntityBankAccount,
    ExternalParty,
    Fulfillment,
    Invoice,
    Progress,
    Project,
    RealCost,
    RiskEvent,
    RiskThreshold,
    TaxLedger,
    TaxPaymentRecord,
    TaxRule,
    User,
    AIReviewJob,
    AIReviewResult,
    AIReviewBatch,
    AIConsensusReport,
    FactsSnapshot,
    FactsRequestLog,
    RemediationTask,
    SyncLog,
    SyncPending,
    ProjectRAGMap,
    AuditLog,
)


def _current_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _get_initial_admin_password() -> str:
    """Read the initial admin password from environment, falling back to the
    hard-coded development default ``888888``.

    In production environments the environment variable is mandatory so that the
    deployment pipeline can inject a strong credential at startup.  In
    development (when ``APP_ENV`` is unset or ``development``) the well-known
    default ``888888`` is accepted.
    """
    env_password = os.getenv("INITIAL_ADMIN_PASSWORD", "").strip()
    if env_password:
        from .auth import validate_password_strength
        try:
            validate_password_strength(env_password, username="admin")
        except ValueError as exc:
            raise RuntimeError(
                f"INITIAL_ADMIN_PASSWORD does not meet the minimum strength policy "
                f"(at least 12 characters, with upper/lowercase, digit, and special "
                f"character; no username): {exc}"
            ) from exc
        return env_password

    if _ENVIRONMENT in {"production", "prod", "staging"}:
        raise RuntimeError(
            "INITIAL_ADMIN_PASSWORD environment variable must be set in production; "
            "refusing the insecure development default."
        )
    # Development fallback: well-known default so the demo can start without env setup.
    return "888888"


# 26 个真实单位的 canonical 主数据。
# tuple: code, name, short_name, business_role, tax_id, legal_entity,
#        parent_entity_code, active
from .domain.entity_master import ENTITIES_MASTER


def _seed_entities_from_master(db) -> None:
    """Upsert the closed 26-unit internal master into the shared PostgreSQL row."""
    existing = {
        (entity.code or entity.entity_code): entity
        for entity in db.query(Entity).all()
        if (entity.code or entity.entity_code)
    }
    for code, name, short_name, role, tax_id, legal_entity, parent_code, active in ENTITIES_MASTER:
        entity = existing.get(code)
        if entity is None:
            entity = Entity(code=code, entity_code=code)
            db.add(entity)
        entity.code = code
        entity.entity_code = code
        entity.name = name
        entity.short_name = short_name
        entity.kind = role
        entity.internal = True
        entity.tax_id = tax_id or None
        entity.business_role = role
        entity.legal_entity = bool(legal_entity)
        entity.parent_entity_code = parent_code
        entity.active = bool(active)
        entity.entity_kind = "internal_company"
        entity.status = "active" if active else "inactive"
    print(f"✓ PostgreSQL 内部主体主数据已对齐：{len(ENTITIES_MASTER)} 家")


# External parties are explicit named records, not fake Entity rows.  The
# names below already occur as project owners in the demonstration data.
EXTERNAL_PARTIES = [
    ("EXT-TF", "成都市天府新区金融城投公司", "天府金融城投", "construction"),
    ("EXT-CY", "成渝高速公路开发投资集团", "成渝高速投资", "construction"),
    ("EXT-GY", "广元市利州区水务局城投平台", "广元利州水务", "construction"),
    ("EXT-GX", "国家电网四川省电力公司成都供电公司", "国网成都供电", "construction"),
    ("EXT-GEM", "青海盐湖工业股份有限公司", "青海盐湖工业", "industrial"),
    ("EXT-YB", "宜宾市三江新区开发集团", "宜宾三江开发", "construction"),
    ("EXT-ABB-ELECTRIC", "ABB(中国)特种电气设备公司", "ABB电气", "supplier"),
    ("EXT-NARI", "南京南瑞继保电气有限公司", "南瑞继保", "supplier"),
    ("EXT-QH-RAILWAY", "青海地方铁路建设投资有限公司", "青海铁投", "construction"),
    ("EXT-RAIL", "中铁特种轨道工程有限公司", "特种轨道", "construction"),
    ("EXT-SHIP", "长航特种工程潜水与打捞公司", "长航潜水", "construction"),
    ("EXT-CONC", "西南特种混凝土骨料直供站", "特种商砼", "supplier"),
    ("EXT-XN-CONCRETE", "西南特种混凝土骨料直供站", "特种商砼", "supplier"),
    ("EXT-CQ-HEAVY-CRANE", "重庆巨力重型起重设备吊装公司", "重庆巨力吊装", "construction"),
    ("EXT-CRANE", "重庆巨力重型起重设备吊装公司", "重庆巨力吊装", "construction"),
    ("EXT-EXP", "中建西南地勘院技术专家组", "西南地勘", "service"),
    ("EXT-TREE", "四川省生态林业苗木繁育中心", "生态林业", "supplier"),
    ("EXT-PG", "攀钢集团特种钢材直销部", "攀钢直销", "supplier"),
    ("EXT-PG-STEEL", "攀钢集团特种钢材直销部", "攀钢直销", "supplier"),
    ("EXT-EXPERT-LABOR", "特种作业专家劳务派遣中心", "特种劳务", "service"),
    ("EXT-CY-001", "外部专业劳务协作与技术支持", "劳务协作", "service"),
    ("EXT-GX-001", "特种电力配套调试与试验配合", "特种电力", "service"),
    ("EXT-GEM-001", "特种防腐耐磨地坪材料直销", "特种地坪", "supplier"),
    ("EXT-GY-001", "厂区雨污水管网生态开挖分包", "管网开挖", "construction"),
    ("EXT-GY-FOREST", "国家天然林保护工程林木中心", "国家天然林", "supplier")
]

# 5大标杆示范工程
DEMO_PROJECTS = [
    {
        "code": "CD-TF-001",
        "name": "成都天府国际金融中心二期大厦工程",
        "city": "成都市",
        "contract_total": Decimal("1450000000"),
        "tax_method": "general",
        "progress": {
            "output_value": Decimal("890452100"),
            "settlement": Decimal("820000000"),
            "recognized_revenue": Decimal("850000000"),
            "collection": Decimal("680000000"),
        },
        "budgets": [
            ("材料", 550_000_000), ("劳务", 280_000_000),
            ("设备", 120_000_000), ("专业分包", 320_000_000),
            ("项目管理", 90_000_000),
        ],
        "contracts": [
            ("TF-A08-B01", "A08", "B01", "材料", 450_000_000),
            ("TF-A08-C01", "A08", "C01", "劳务", 260_000_000),
            ("TF-A08-D01", "A08", "D01", "设备", 110_000_000),
            ("TF-A08-A11", "A08", "A11", "专业分包", 280_000_000),
            ("TF-A08-A05", "A08", "A05", "专业分包", 40_000_000),
        ],
        "fulfillments": [
            ("B01", "material_acceptance", "材料", 320_000_000, 1),
            ("C01", "labor_settlement", "劳务", 185_000_000, 1),
            ("D01", "equipment_shift", "设备", 78_000_000, 1),
            ("A11", "subcontract_acceptance", "专业分包", 195_000_000, 1),
            ("A05", "subcontract_acceptance", "专业分包", 28_000_000, 1),
        ],
        "invoices_in": [
            ("IN-TF-B01", "B01", "材料", 300_000_000, 39_000_000, 0.13),
            ("IN-TF-C01", "C01", "劳务", 180_000_000, 16_200_000, 0.09),
            ("IN-TF-D01", "D01", "设备", 75_000_000, 9_750_000, 0.13),
            ("IN-TF-A11", "A11", "专业分包", 190_000_000, 17_100_000, 0.09),
            ("IN-TF-A05", "A05", "专业分包", 27_000_000, 2_430_000, 0.09),
        ],
        "invoices_out": [
            ("OUT-TF-001", "A08", "EXT-TF", "construction", 850_000_000, 76_500_000, 0.09)
        ],
        "cashflows": [
            ("B01", "材料", 280_000_000),
            ("C01", "劳务", 170_000_000),
            ("D01", "设备", 70_000_000),
            ("A11", "专业分包", 175_000_000),
            ("A05", "专业分包", 25_000_000),
        ],
        "real_costs": [
            ("A08", "", "项目管理", "site_salary", 35_000_000, 1, "锐宝建设天府项目部现场管理成本"),
            ("B01", "", "材料", "external_purchase", 260_000_000, 1, "乾润和贸易钢材商砼外部集采成本"),
            ("C01", "", "劳务", "salary_social", 160_000_000, 1, "本盛劳务实名制工资金流"),
            ("D01", "", "设备", "depr_fuel_maintenance", 55_000_000, 1, "乾润和机械塔吊及周转材折旧与维保"),
            ("A11", "", "专业分包", "steel_structure", 165_000_000, 1, "成都巨邦钢结构加工与吊装成本"),
            ("A05", "", "专业分包", "it_integration", 22_000_000, 1, "帆亿通信弱电智能化施工成本"),
        ]
    },
    {
        "code": "CY-CQ-002",
        "name": "成渝双城经济圈跨江特大桥及连接线工程",
        "city": "重庆市",
        "contract_total": Decimal("880000000"),
        "tax_method": "general",
        "progress": {
            "output_value": Decimal("520000000"),
            "settlement": Decimal("480000000"),
            "recognized_revenue": Decimal("500000000"),
            "collection": Decimal("420000000"),
        },
        "budgets": [
            ("材料", 380_000_000), ("劳务", 160_000_000),
            ("设备", 90_000_000), ("专业分包", 150_000_000),
            ("项目管理", 60_000_000),
        ],
        "contracts": [
            ("CY-A03-B10", "A03", "B10", "材料", 320_000_000),
            ("CY-A03-C02", "A03", "C02", "劳务", 140_000_000),
            ("CY-A03-D02", "A03", "D02", "设备", 80_000_000),
            ("CY-A03-A04", "A03", "A04", "专业分包", 120_000_000),
        ],
        "fulfillments": [
            ("B10", "material_acceptance", "材料", 210_000_000, 1),
            ("C02", "labor_settlement", "劳务", 95_000_000, 1),
            ("D02", "equipment_shift", "设备", 52_000_000, 1),
            ("A04", "subcontract_acceptance", "专业分包", 85_000_000, 1),
        ],
        "invoices_in": [
            ("IN-CY-B10", "B10", "材料", 200_000_000, 26_000_000, 0.13),
            ("IN-CY-C02", "C02", "劳务", 90_000_000, 8_100_000, 0.09),
            ("IN-CY-D02", "D02", "设备", 50_000_000, 6_500_000, 0.13),
            ("IN-CY-A04", "A04", "专业分包", 80_000_000, 7_200_000, 0.09),
        ],
        "invoices_out": [
            ("OUT-CY-001", "A03", "EXT-CY", "construction", 500_000_000, 45_000_000, 0.09)
        ],
        "cashflows": [
            ("B10", "材料", 180_000_000),
            ("C02", "劳务", 85_000_000),
            ("D02", "设备", 45_000_000),
            ("A04", "专业分包", 75_000_000),
        ],
        "real_costs": [
            ("A03", "", "项目管理", "site_salary", 22_000_000, 1, "屹明汇建设成渝项目部现场管理"),
            ("B10", "", "材料", "external_purchase", 175_000_000, 1, "朗德乾润商贸特种桥梁钢材采购成本"),
            ("C02", "", "劳务", "salary_social", 80_000_000, 1, "灏琅劳务桥梁高空与泥瓦作业人员工资"),
            ("D02", "", "设备", "depr_fuel_maintenance", 38_000_000, 1, "乾诺机械重型水上浮吊与桩机成本"),
            ("A04", "", "专业分包", "branch_cost", 70_000_000, 1, "屹明汇重庆分公司施工执行成本"),
        ]
    },
    {
        "code": "GY-LZ-003",
        "name": "广元利州产城融合与生态河道综合治理工程",
        "city": "广元市",
        "contract_total": Decimal("360000000"),
        "tax_method": "general",
        "progress": {
            "output_value": Decimal("210000000"),
            "settlement": Decimal("195000000"),
            "recognized_revenue": Decimal("200000000"),
            "collection": Decimal("165000000"),
        },
        "budgets": [
            ("材料", 150_000_000), ("劳务", 65_000_000),
            ("设备", 45_000_000), ("专业分包", 70_000_000),
            ("项目管理", 25_000_000),
        ],
        "contracts": [
            ("GY-A10-B05", "A10", "B05", "材料", 130_000_000),
            ("GY-A10-A09", "A10", "A09", "专业分包", 60_000_000),
            ("GY-A10-D03", "A10", "D03", "设备", 38_000_000),
            ("GY-A10-B06", "A10", "B06", "材料", 8_000_000),
        ],
        "fulfillments": [
            ("B05", "material_acceptance", "材料", 85_000_000, 1),
            ("A09", "subcontract_acceptance", "专业分包", 42_000_000, 1),
            ("D03", "equipment_shift", "设备", 25_000_000, 1),
            ("B06", "material_acceptance", "材料", 5_000_000, 1),
        ],
        "invoices_in": [
            ("IN-GY-B05", "B05", "材料", 80_000_000, 10_400_000, 0.13),
            ("IN-GY-A09", "A09", "专业分包", 40_000_000, 3_600_000, 0.09),
            ("IN-GY-D03", "D03", "设备", 24_000_000, 3_120_000, 0.13),
            ("IN-GY-B06", "B06", "材料", 4_800_000, 624_000, 0.13),
        ],
        "invoices_out": [
            ("OUT-GY-001", "A10", "EXT-GY", "construction", 200_000_000, 18_000_000, 0.09)
        ],
        "cashflows": [
            ("B05", "材料", 75_000_000),
            ("A09", "专业分包", 36_000_000),
            ("D03", "设备", 22_000_000),
            ("B06", "材料", 4_500_000),
        ],
        "real_costs": [
            ("A10", "", "项目管理", "site_salary", 12_000_000, 1, "鼎新源建筑水利项目部现场管理"),
            ("B05", "", "材料", "external_purchase", 68_000_000, 1, "广元玖硕商贸砂石骨料地磅采购成本"),
            ("A09", "", "专业分包", "earthwork", 32_000_000, 1, "顺程源建筑土石方与边坡支护"),
            ("D03", "", "设备", "depr_fuel_maintenance", 18_000_000, 1, "惠润农业水利清淤机械租赁成本"),
            ("B06", "", "材料", "signboard", 3_800_000, 1, "采云广告文明施工定型化标识物资"),
        ]
    },
    {
        "code": "CD-GX-004",
        "name": "成都高新西区绿色低碳微电网与变电站工程",
        "city": "成都市",
        "contract_total": Decimal("180000000"),
        "tax_method": "general",
        "progress": {
            "output_value": Decimal("115000000"),
            "settlement": Decimal("105000000"),
            "recognized_revenue": Decimal("110000000"),
            "collection": Decimal("95000000"),
        },
        "budgets": [
            ("材料", 85_000_000), ("劳务", 35_000_000),
            ("设备", 25_000_000), ("专业分包", 25_000_000),
            ("项目管理", 10_000_000),
        ],
        "contracts": [
            ("GX-A07-B08", "A07", "B08", "材料", 65_000_000),
            ("GX-A07-B02", "A07", "B02", "材料", 20_000_000),
            ("GX-A07-A02", "A07", "A02", "专业分包", 22_000_000),
        ],
        "fulfillments": [
            ("B08", "material_acceptance", "材料", 45_000_000, 1),
            ("B02", "material_acceptance", "材料", 15_000_000, 1),
            ("A02", "subcontract_acceptance", "专业分包", 16_000_000, 1),
        ],
        "invoices_in": [
            ("IN-GX-B08", "B08", "材料", 42_000_000, 5_460_000, 0.13),
            ("IN-GX-B02", "B02", "材料", 14_000_000, 1_820_000, 0.13),
            ("IN-GX-A02", "A02", "专业分包", 15_000_000, 1_350_000, 0.09),
        ],
        "invoices_out": [
            ("OUT-GX-001", "A07", "EXT-GX", "construction", 110_000_000, 9_900_000, 0.09)
        ],
        "cashflows": [
            ("B08", "材料", 38_000_000),
            ("B02", "材料", 12_000_000),
            ("A02", "专业分包", 13_500_000),
        ],
        "real_costs": [
            ("A07", "", "项目管理", "site_salary", 5_500_000, 1, "铁安电力项目部工程技术人员薪资"),
            ("B08", "", "材料", "external_purchase", 35_000_000, 1, "鑫晨鼎升高低压电缆与配电柜集采"),
            ("B02", "", "材料", "external_purchase", 11_000_000, 1, "兴誉诚商贸电气五金安装辅材成本"),
            ("A02", "", "专业分包", "civil_structure", 12_500_000, 1, "中恒腾鸣建筑变电站房土建施工成本"),
        ]
    },
    {
        "code": "QY-GEM-005",
        "name": "格尔木盐湖工业园区特种仓储与综合配套工程",
        "city": "格尔木市",
        "contract_total": Decimal("250000000"),
        "tax_method": "general",
        "progress": {
            "output_value": Decimal("160000000"),
            "settlement": Decimal("145000000"),
            "recognized_revenue": Decimal("150000000"),
            "collection": Decimal("130000000"),
        },
        "budgets": [
            ("材料", 110_000_000), ("劳务", 45_000_000),
            ("设备", 30_000_000), ("专业分包", 50_000_000),
            ("项目管理", 15_000_000),
        ],
        "contracts": [
            ("GEM-A01-B09", "A01", "B09", "材料", 55_000_000),
            ("GEM-A01-B03", "A01", "B03", "材料", 35_000_000),
            ("GEM-A01-B07", "A01", "B07", "材料", 20_000_000),
            ("GEM-A01-B04", "A01", "B04", "材料", 15_000_000),
            ("GEM-A01-A06", "A01", "A06", "专业分包", 45_000_000),
        ],
        "fulfillments": [
            ("B09", "material_acceptance", "材料", 38_000_000, 1),
            ("B03", "material_acceptance", "材料", 24_000_000, 1),
            ("B07", "material_acceptance", "材料", 14_000_000, 1),
            ("B04", "material_acceptance", "材料", 10_000_000, 1),
            ("A06", "subcontract_acceptance", "专业分包", 32_000_000, 1),
        ],
        "invoices_in": [
            ("IN-GEM-B09", "B09", "材料", 35_000_000, 4_550_000, 0.13),
            ("IN-GEM-B03", "B03", "材料", 22_000_000, 2_860_000, 0.13),
            ("IN-GEM-B07", "B07", "材料", 13_000_000, 1_690_000, 0.13),
            ("IN-GEM-B04", "B04", "材料", 9_000_000, 1_170_000, 0.13),
            ("IN-GEM-A06", "A06", "专业分包", 30_000_000, 2_700_000, 0.09),
        ],
        "invoices_out": [
            ("OUT-GEM-001", "A01", "EXT-GEM", "construction", 150_000_000, 13_500_000, 0.09)
        ],
        "cashflows": [
            ("B09", "材料", 32_000_000),
            ("B03", "材料", 20_000_000),
            ("B07", "材料", 12_000_000),
            ("B04", "材料", 8_500_000),
            ("A06", "专业分包", 27_000_000),
        ],
        "real_costs": [
            ("A01", "", "项目管理", "site_salary", 7_500_000, 1, "中镌建筑格尔木项目部现场管理"),
            ("B09", "", "材料", "external_purchase", 29_000_000, 1, "青泽贸易高寒特种耐寒防冻建材直采"),
            ("B03", "", "材料", "external_purchase", 18_000_000, 1, "坤珀贸易特种耐候钢材采购成本"),
            ("B07", "", "材料", "external_purchase", 10_500_000, 1, "恒创嘉泰大宗物资集采供应链成本"),
            ("B04", "", "材料", "external_purchase", 7_200_000, 1, "矗佳商贸预拌砂浆与外加剂成本"),
            ("A06", "", "专业分包", "structure", 25_000_000, 1, "裕合荣建筑大型特种钢构仓储厂房施工"),
        ]
    }
]


def run() -> None:
    db = SessionLocal()
    try:
        # Historical test rows may exist in a database created by an older
        # release.  Preserve them for audit/history, but fail closed in every
        # non-test process before the idempotent early return below.  New
        # production seeds create only real OpenAI-compatible endpoints.
        runtime_environment = os.getenv(
            "APP_ENV", os.getenv("ENVIRONMENT", "development")
        ).strip().lower()
        if runtime_environment != "test":
            legacy_test_endpoints = db.query(AIModelEndpoint).filter(
                func.lower(func.trim(AIModelEndpoint.adapter)) == "mock"
            ).all()
            for endpoint in legacy_test_endpoints:
                endpoint.enabled = False

        # ⚠️ 清库区（仅在 TAX_SEED_MODE=demo 时执行）
        # 每次演示启动重置数据库状态，清除所有业务数据。
        # 生产环境必须使用 TAX_SEED_MODE=production（或不设置，默认不清理）。
        if _SEED_MODE == "demo":
            _logger.warning("[SEED] TAX_SEED_MODE=demo，执行数据库清库操作")
            tables_to_clean = [
                "audit_logs", "sync_pending", "sync_logs", "project_rag_map",
                "facts_request_logs", "facts_snapshots", "ai_consensus_reports",
                "ai_review_batches", "ai_review_results", "ai_review_jobs",
                "remediation_tasks", "tax_ledgers", "tax_payment_records",
                "entity_bank_accounts", "risk_events", "real_costs", "cashflows",
                "invoices", "fulfillment", "contracts", "progress", "budgets",
                "projects", "external_parties", "entities"
            ]
            existing_tables = set(inspect(db.bind).get_table_names())
            targets = [name for name in tables_to_clean if name in existing_tables]
            if targets:
                quoted = ", ".join(f'"{name}"' for name in targets)
                db.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
                _logger.warning("[SEED] PostgreSQL demo 清库完成: %s", ", ".join(targets))
            db.commit()
        else:
            _logger.warning(f"[SEED] TAX_SEED_MODE={_SEED_MODE}，跳过清库操作（仅在空库时插入初始数据）")

        # 1. 默认用户（密码由 INITIAL_ADMIN_PASSWORD 环境变量提供，启动时已校验强度）
        if db.query(User).count() == 0:
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            admin_password = _get_initial_admin_password()
            operator_password = os.getenv("INITIAL_OPERATOR_PASSWORD", admin_password).strip() or admin_password
            if operator_password == admin_password:
                operator_password = admin_password
            else:
                from .auth import validate_password_strength
                try:
                    validate_password_strength(operator_password, username="operator")
                except ValueError as exc:
                    raise RuntimeError(
                        f"INITIAL_OPERATOR_PASSWORD does not meet the minimum strength policy: {exc}"
                    ) from exc
            for username, role, display_name, password in [
                ("admin",    "admin",    "系统管理员",  admin_password),
                ("operator", "operator", "业务操作员",  operator_password),
            ]:
                pw_hash, pw_salt = hash_password(password)
                db.add(User(
                    username=username,
                    password_hash=f"{pw_hash}:{pw_salt}",
                    role=role,
                    display_name=display_name,
                    active=True,
                    created_at=ts,
                ))

        # 2. 内部主体只有一份 canonical master；Tax/RAG 共用同一 PostgreSQL 行。
        _seed_entities_from_master(db)

        existing_external = {p.code: p for p in db.query(ExternalParty).all()}
        for code, name, short_name, kind in EXTERNAL_PARTIES:
            party = existing_external.get(code)
            if party is None:
                party = ExternalParty(code=code)
                db.add(party)
            party.name = name
            party.short_name = short_name
            party.kind = kind
            party.active = True
        db.flush()

        # Production bootstrap is idempotent: never duplicate demonstration business data.
        if _SEED_MODE != "demo" and db.query(Project).count() > 0:
            db.commit()
            print("✓ PostgreSQL 主数据已对齐；检测到既有项目，跳过示范业务数据")
            return

        period = _current_period()

        # 3. 写入项目 1：宜宾示范工业项目（基准回归项目）
        p1 = Project(
            code="YB-DEMO-001", name="宜宾示范工业项目",
            city="宜宾", contract_total=Decimal("100000000"),
            tax_method="general",
        )
        db.add(p1)
        db.flush()

        for cat, amt in [
            ("材料", 35_000_000), ("劳务", 18_000_000),
            ("设备", 8_000_000), ("专业分包", 12_000_000),
            ("项目管理", 7_000_000),
        ]:
            db.add(Budget(project_id=p1.id, category=cat, amount=Decimal(str(amt))))
        db.add(Progress(
            project_id=p1.id, period=period,
            output_value=Decimal("25000000"),
            settlement=Decimal("22000000"),
            recognized_revenue=Decimal("23000000"),
            collection=Decimal("18000000"),
        ))

        for no, buyer, seller, cat, amt in [
            ("YB-B-001", "A08", "B01", "劳务", 8_000_000),
            ("YB-C-001", "A08", "C01", "材料", 12_000_000),
            ("YB-D-001", "A08", "D01", "设备", 3_000_000),
            ("YB-EXT-CY-001", "A08", "EXT-CY", "劳务", 2_000_000),
            ("YB-EXT-GX-001", "A08", "EXT-GX", "设备", 1_500_000),
            ("YB-EXT-GEM-001", "A08", "EXT-GEM", "材料", 5_000_000),
            ("YB-EXT-GY-001", "A08", "EXT-GY", "专业分包", 3_000_000),
        ]:
            db.add(Contract(
                project_id=p1.id, contract_no=no, buyer_code=buyer,
                seller_code=seller, category=cat,
                amount=Decimal(str(amt)),
                internal_trade=(
                    buyer in CANONICAL_ENTITY_CODES
                    and seller in CANONICAL_ENTITY_CODES
                ),
            ))

        for cp, kind, cat, amt, ok in [
            ("B01", "labor_settlement", "劳务", 5_800_000, 1),
            ("C01", "material_acceptance", "材料", 9_300_000, 1),
            ("D01", "equipment_shift", "设备", 1_850_000, 1),
            ("EXT-CY", "labor_settlement", "劳务", 1_100_000, 1),
            ("EXT-GX", "equipment_shift", "设备", 650_000, 0),  # 履约证据缺失
            ("EXT-GEM", "material_acceptance", "材料", 4_300_000, 1),
            ("EXT-GY", "other", "专业分包", 1_600_000, 1),
        ]:
            db.add(Fulfillment(
                project_id=p1.id, counterparty_code=cp, kind=kind,
                category=cat, amount=Decimal(str(amt)),
                evidence_complete=bool(ok),
            ))

        db.add(Invoice(
            project_id=p1.id, invoice_no="OUT-YB-001", period=period,
            entity_code="A08", direction="out",
            counterparty_code="EXT-TF", category="construction",
            net=Decimal("23000000"), vat=Decimal("2070000"),
            rate=Decimal("0.09"), deductible=False,
        ))
        for no, cp, cat, net, vat, rate in [
            ("IN-B-001", "B01", "劳务", 5_500_000, 495_000, 0.09),
            ("IN-C-001", "C01", "材料", 9_000_000, 1_170_000, 0.13),
            ("IN-D-001", "D01", "设备", 1_800_000, 234_000, 0.13),
            ("IN-EXT-CY-001", "EXT-CY", "劳务", 1_000_000, 90_000, 0.09),
            ("IN-EXT-GX-001", "EXT-GX", "设备", 600_000, 78_000, 0.13),
            ("IN-EXT-GEM-001", "EXT-GEM", "材料", 4_000_000, 520_000, 0.13),
            ("IN-EXT-GY-001", "EXT-GY", "专业分包", 1_500_000, 135_000, 0.09),
        ]:
            db.add(Invoice(
                project_id=p1.id, invoice_no=no, period=period,
                entity_code="A08", direction="in",
                counterparty_code=cp, category=cat,
                net=Decimal(str(net)), vat=Decimal(str(vat)),
                rate=Decimal(str(rate)), deductible=True,
            ))
        for code, cp, cat, net, vat, rate, no in [
            ("B01", "A08", "劳务", 5_500_000, 495_000, 0.09, "B-OUT-001"),
            ("C01", "A08", "材料", 9_000_000, 1_170_000, 0.13, "C-OUT-001"),
            ("D01", "A08", "设备", 1_800_000, 234_000, 0.13, "D-OUT-001"),
        ]:
            db.add(Invoice(
                project_id=p1.id, invoice_no=no, period=period,
                entity_code=code, direction="out",
                counterparty_code=cp, category=cat,
                net=Decimal(str(net)), vat=Decimal(str(vat)),
                rate=Decimal(str(rate)), deductible=False,
            ))

        for owner, source, cat, sub, amt, ext, note in [
            ("A08", "", "项目管理", "site_salary", 1_200_000, 1, "建筑施工项目部工资管理"),
            ("B01", "", "材料", "external_purchase", 7_550_000, 1, "商贸物资对外采购真实成本"),
            ("C01", "", "劳务", "salary_social", 4_350_000, 1, "建筑劳务工资社保真实成本"),
            ("D01", "", "设备", "depr_fuel_maintenance", 1_250_000, 1, "机械租赁折旧维修燃料真实成本"),
            ("A08", "EXT-CY", "专业分包", "external_construction", 1_100_000, 1, "外部建筑施工真实外部成本"),
            ("A08", "EXT-GX", "材料", "external_material", 4_300_000, 1, "外部商贸物资材料采购成本"),
            ("A08", "EXT-GEM", "劳务", "external_labor", 1_600_000, 1, "外部建筑劳务真实用工成本"),
            ("A08", "EXT-GY", "设备", "external_equipment", 650_000, 1, "外部机械租赁真实设备成本"),
        ]:
            db.add(RealCost(
                project_id=p1.id, entity_code=owner,
                counterparty_code=source, category=cat,
                subcategory=sub, period=period,
                amount=Decimal(str(amt)),
                external_cash=bool(ext), note=note,
            ))

        for cp, cat, amt in [
            ("B01", "劳务", 5_000_000), ("C01", "材料", 8_000_000),
            ("D01", "设备", 1_500_000), ("EXT-CY", "劳务", 900_000),
            ("EXT-GX", "设备", 700_000), ("EXT-GEM", "材料", 3_800_000),
            ("EXT-GY", "专业分包", 1_200_000),
        ]:
            db.add(CashFlow(
                project_id=p1.id, entity_code="A08",
                counterparty_code=cp, direction="out",
                amount=Decimal(str(amt)), period=period,
                note=f"{cat}付款",
            ))
        db.add(CashFlow(
            project_id=p1.id, entity_code="A08",
            counterparty_code="EXT-TF", direction="in",
            amount=Decimal("18000000"), period=period,
            note="工程回款",
        ))

        # 4. 写入 5 大标杆示范项目（项目 2 ~ 6）
        for p_data in DEMO_PROJECTS:
            p = Project(
                code=p_data["code"],
                name=p_data["name"],
                city=p_data["city"],
                contract_total=p_data["contract_total"],
                tax_method=p_data["tax_method"],
            )
            db.add(p)
            db.flush()

            for cat, amt in p_data["budgets"]:
                db.add(Budget(project_id=p.id, category=cat, amount=Decimal(str(amt))))

            prog = p_data["progress"]
            db.add(Progress(
                project_id=p.id,
                period=period,
                output_value=prog["output_value"],
                settlement=prog["settlement"],
                recognized_revenue=prog["recognized_revenue"],
                collection=prog["collection"],
            ))

            for no, buyer, seller, cat, amt in p_data["contracts"]:
                db.add(Contract(
                    project_id=p.id,
                    contract_no=no,
                    buyer_code=buyer,
                    seller_code=seller,
                    category=cat,
                    amount=Decimal(str(amt)),
                    internal_trade=True,
                    note="系统内关联方交易"
                ))

            for cp, kind, cat, amt, ok in p_data["fulfillments"]:
                db.add(Fulfillment(
                    project_id=p.id,
                    counterparty_code=cp,
                    kind=kind,
                    category=cat,
                    amount=Decimal(str(amt)),
                    evidence_complete=bool(ok),
                ))

            for no, cp, cat, net, vat, rate in p_data["invoices_in"]:
                db.add(Invoice(
                    project_id=p.id,
                    invoice_no=no,
                    period=period,
                    entity_code=p_data["contracts"][0][1],
                    direction="in",
                    counterparty_code=cp,
                    category=cat,
                    net=Decimal(str(net)),
                    vat=Decimal(str(vat)),
                    rate=Decimal(str(rate)),
                    deductible=True,
                ))
                db.add(Invoice(
                    project_id=p.id,
                    invoice_no=f"OUT-{no}",
                    period=period,
                    entity_code=cp,
                    direction="out",
                    counterparty_code=p_data["contracts"][0][1],
                    category=cat,
                    net=Decimal(str(net)),
                    vat=Decimal(str(vat)),
                    rate=Decimal(str(rate)),
                    deductible=False,
                ))

            for no, entity, owner, cat, net, vat, rate in p_data["invoices_out"]:
                db.add(Invoice(
                    project_id=p.id,
                    invoice_no=no,
                    period=period,
                    entity_code=entity,
                    direction="out",
                    counterparty_code=owner,
                    category=cat,
                    net=Decimal(str(net)),
                    vat=Decimal(str(vat)),
                    rate=Decimal(str(rate)),
                    deductible=False,
                ))

            for cp, _cat, amt in p_data["cashflows"]:
                db.add(CashFlow(
                    project_id=p.id,
                    entity_code=p_data["contracts"][0][1],
                    counterparty_code=cp,
                    direction="out",
                    amount=Decimal(str(amt)),
                    period=period,
                    note=f"支付系统内关联方{cp}款项",
                ))
            db.add(CashFlow(
                project_id=p.id,
                entity_code=p_data["contracts"][0][1],
                counterparty_code="EXT-TF",
                direction="in",
                amount=p_data["progress"]["collection"],
                period=period,
                note="收到业主工程进度款",
            ))

            for owner, source, cat, sub, amt, ext, note in p_data["real_costs"]:
                db.add(RealCost(
                    project_id=p.id,
                    entity_code=owner,
                    counterparty_code=source,
                    category=cat,
                    subcategory=sub,
                    period=period,
                    amount=Decimal(str(amt)),
                    external_cash=bool(ext),
                    note=note,
                ))

        # 5. 税务规则
        if db.query(TaxRule).count() == 0:
            for code, rate, note in [
                ("VAT_CONSTRUCTION_GENERAL", 0.09, "建筑服务一般计税税率9%"),
                ("VAT_MOVABLE_RENTAL", 0.13, "有形动产租赁与建材销售税率13%"),
                ("VAT_SIMPLIFIED", 0.03, "建筑服务简易计税征收率3%"),
                ("CIT_GENERAL", 0.25, "企业所得税法定税率25%"),
                ("CIT_WESTERN_DEV", 0.15, "西部大开发鼓励类产业企业所得税优惠税率15%"),
            ]:
                db.add(TaxRule(
                    code=code, rate=Decimal(str(rate)),
                    effective_from="2026-01-01", reviewed=True, note=note,
                ))

        # 6. 风险阈值
        if db.query(RiskThreshold).count() == 0:
            for t in RiskThreshold.defaults():
                db.add(t)

        # 7. 成本科目树
        if db.query(CostAccount).count() == 0:
            accounts = [
                ("1000", "", "材料", "材料"),
                ("1101", "1000", "大宗钢材", "材料"),
                ("1102", "1000", "水泥商砼", "材料"),
                ("2000", "", "劳务", "劳务"),
                ("2101", "2000", "主体劳务工资", "劳务"),
                ("3000", "", "设备", "设备"),
                ("3101", "3000", "起重机械周转", "设备"),
                ("4000", "", "专业分包", "专业分包"),
                ("4101", "4000", "钢结构安装", "专业分包"),
                ("4102", "4000", "弱电智能化", "专业分包"),
                ("5000", "", "项目管理", "项目管理"),
                ("6000", "", "税费", "税金"),
            ]
            for code, parent, name, cat in accounts:
                db.add(CostAccount(
                    code=code, parent_code=parent, name=name,
                    category=cat, active=True,
                ))

        # 8. AI 端点与提示词（1: 本地 Ling-3.0-tiny 保底模型，2: 本地 Qwen3.5-2B 保底模型）
        if db.query(AIModelEndpoint).count() == 0:
            db.add(AIModelEndpoint(
                name="本地 Ling-3.0-tiny 保底模型", adapter="openai_compatible",
                base_url="http://127.0.0.1:8930", chat_path="/v1/chat/completions",
                model="ling-3.0-tiny", api_key_env="",
                enabled=True, timeout_seconds=60, priority=100, routing_group="default",
                note="V2.0 llama.cpp 本地 Ling-3.0-tiny 离线保底模型；用于无外网时离线 AI 辅助研判",
            ))
            db.add(AIModelEndpoint(
                name="本地 Qwen3.5-2B 保底模型", adapter="openai_compatible",
                base_url="http://127.0.0.1:8930", chat_path="/v1/chat/completions",
                model="local-qwen3.5-2b", api_key_env="",
                enabled=True, timeout_seconds=60, priority=200, routing_group="default",
                note="V2.0 llama.cpp 本地 Qwen3.5-2B 备选保底模型",
            ))

        if db.query(AIPromptTemplate).count() == 0:
            prompts = [
                ("通用项目审查", "default", 1,
                 "优先依据系统确定性数字和证据链，避免宽泛建议。",
                 "检查经营真实性、数据完整性、利润、现金流、税务与可执行整改。"),
                ("合同专项审查", "contract", 1,
                 "重点关注合同主体、业务性质、金额、履约、发票与付款的一致性。",
                 "检查合同商业实质、内部/外部主体边界、金额异常、履约和税务条款。"),
                ("税务专项审查", "tax", 1,
                 "不得把管理测算当作正式申报结论；未复核税务规则必须列为数据缺口。",
                 "检查26家系统内单位按业务角色分组的独立税负、进销项、规则复核状态及异常税负。"),
                ("设备专项审查", "equipment", 1,
                 "重点区分设备裸租、配操作人员和设备施工作业，并核对台班证据。",
                 "检查设备合同、台班、人员、发票税率、真实成本与付款。"),
                ("劳务专项审查", "labor", 1,
                 "重点核对真实人员、工资社保、工程量、结算和发票付款证据。",
                 "检查四川本盛劳务有限公司与外部实名交易方的业务真实性、成本穿透、履约证据和四流一致性。"),
                ("材料专项审查", "material", 1,
                 "重点核对四川乾润和贸易有限公司真实外采、材料验收、数量价格与付款。",
                 "检查四川乾润和贸易有限公司与外部实名交易方材料业务的真实采购、内部交易抵消、验收和价格合理性。"),
                ("整项目体检", "whole_project", 1,
                 "按严重程度排序，优先输出影响项目利润、现金流和合规的关键问题。",
                 "全面检查合同、履约、发票、资金、成本、税务、EAC和风险。"),
            ]
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for name, scope, ver, addendum, focus in prompts:
                db.add(AIPromptTemplate(
                    name=name, scope=scope, version=ver,
                    system_addendum=addendum, review_focus=focus,
                    enabled=True, created_at=ts,
                ))

        # 9. 真实风险事件
        db.add(RiskEvent(
            project_id=1,
            code="RAG_EVIDENCE_LOGISTICS_GAP",
            severity="warning",
            message="【乾润和贸易】大宗钢材物资过磅单与入库验收凭证滞后：本期进项发票30000万元，大宗钢材电子地磅单覆盖率82%，暂缺18%凭证链。",
            resolved=False,
        ))
        db.add(RiskEvent(
            project_id=2,
            code="CROSS_REGION_FILING_AUDIT",
            severity="danger",
            message="【屹明汇重庆分公司】跨省跨区施工预缴税款与企业所得税分摊核查：成渝特大桥项目重庆段需在月末前完成就地预缴2%增值税核销及川渝两地企业所得税三因素法分摊测算。",
            resolved=False,
        ))

        db.commit()
        print("✓ app/seed.py 26 家系统内单位与 5 大标杆示范项目库构建完成！")
    except Exception as e:
        db.rollback()
        print(f"✗ Seed 异常: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
