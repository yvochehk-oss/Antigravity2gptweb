"""V0.2: 演示数据 seed。自适应当前月份 + 风险阈值。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .db import Base, SessionLocal, engine
from .models import (
    AIModelEndpoint, AIPromptTemplate, Budget, Contract, CostAccount,
    CashFlow, Entity, Fulfillment, Invoice, Progress, Project, RealCost,
    RiskThreshold, TaxRule,
)


def _current_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def run() -> None:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if db.query(Entity).count():
            return

        # 主体
        entities = [
            ("A", "施工企业A", "construction", 1),
            ("B", "劳务公司B", "labor", 1),
            ("C", "商贸公司C", "trade", 1),
            ("D", "设备租赁公司D", "equipment", 1),
            ("甲", "第三方劳务公司甲", "labor", 0),
            ("乙", "第三方设备租赁公司乙", "equipment", 0),
            ("丙", "外部材料供应商丙", "material", 0),
            ("丁", "外部专业分包/其他供应商丁", "subcontract", 0),
        ]
        for c, n, k, i in entities:
            db.add(Entity(code=c, name=n, kind=k, internal=bool(i)))

        period = _current_period()

        # 项目 + 预算 + 进度
        p = Project(
            code="YB-DEMO-001", name="宜宾示范工业项目",
            city="宜宾", contract_total=Decimal("100000000"),
            tax_method="general",
        )
        db.add(p); db.flush()

        for cat, amt in [
            ("材料", 35_000_000), ("劳务", 18_000_000),
            ("设备", 8_000_000), ("专业分包", 12_000_000),
            ("项目管理", 7_000_000),
        ]:
            db.add(Budget(
                project_id=p.id, category=cat, amount=Decimal(str(amt)),
            ))
        db.add(Progress(
            project_id=p.id, period=period,
            output_value=Decimal("25000000"),
            settlement=Decimal("22000000"),
            recognized_revenue=Decimal("23000000"),
            collection=Decimal("18000000"),
        ))

        # 合同（内部 + 外部）
        for no, buyer, seller, cat, amt in [
            ("YB-B-001", "A", "B", "劳务", 8_000_000),
            ("YB-C-001", "A", "C", "材料", 12_000_000),
            ("YB-D-001", "A", "D", "设备", 3_000_000),
            ("YB-J-001", "A", "甲", "劳务", 2_000_000),
            ("YB-Y-001", "A", "乙", "设备", 1_500_000),
            ("YB-BING-001", "A", "丙", "材料", 5_000_000),
            ("YB-DING-001", "A", "丁", "专业分包", 3_000_000),
        ]:
            db.add(Contract(
                project_id=p.id, contract_no=no, buyer_code=buyer,
                seller_code=seller, category=cat,
                amount=Decimal(str(amt)),
                internal_trade=(
                    buyer in {"A", "B", "C", "D"}
                    and seller in {"A", "B", "C", "D"}
                ),
            ))

        # 履约
        for cp, kind, cat, amt, ok in [
            ("B", "labor_settlement", "劳务", 5_800_000, 1),
            ("C", "material_acceptance", "材料", 9_300_000, 1),
            ("D", "equipment_shift", "设备", 1_850_000, 1),
            ("甲", "labor_settlement", "劳务", 1_100_000, 1),
            ("乙", "equipment_shift", "设备", 650_000, 0),  # 履约证据缺失
            ("丙", "material_acceptance", "材料", 4_300_000, 1),
            ("丁", "other", "专业分包", 1_600_000, 1),
        ]:
            db.add(Fulfillment(
                project_id=p.id, counterparty_code=cp, kind=kind,
                category=cat, amount=Decimal(str(amt)),
                evidence_complete=bool(ok),
            ))

        # 发票：A 销项 + A 进项 + B/C/D 内部销项镜像
        db.add(Invoice(
            project_id=p.id, invoice_no="OUT-YB-001", period=period,
            entity_code="A", direction="out",
            counterparty_code="业主", category="construction",
            net=Decimal("23000000"), vat=Decimal("2070000"),
            rate=Decimal("0.09"), deductible=False,
        ))
        for no, cp, cat, net, vat, rate in [
            ("IN-B-001", "B", "劳务", 5_500_000, 495_000, 0.09),
            ("IN-C-001", "C", "材料", 9_000_000, 1_170_000, 0.13),
            ("IN-D-001", "D", "设备", 1_800_000, 234_000, 0.13),
            ("IN-J-001", "甲", "劳务", 1_000_000, 90_000, 0.09),
            ("IN-Y-001", "乙", "设备", 600_000, 78_000, 0.13),
            ("IN-BING-001", "丙", "材料", 4_000_000, 520_000, 0.13),
            ("IN-DING-001", "丁", "专业分包", 1_500_000, 135_000, 0.09),
        ]:
            db.add(Invoice(
                project_id=p.id, invoice_no=no, period=period,
                entity_code="A", direction="in",
                counterparty_code=cp, category=cat,
                net=Decimal(str(net)), vat=Decimal(str(vat)),
                rate=Decimal(str(rate)), deductible=True,
            ))
        for code, cp, cat, net, vat, rate, no in [
            ("B", "A", "劳务", 5_500_000, 495_000, 0.09, "B-OUT-001"),
            ("C", "A", "材料", 9_000_000, 1_170_000, 0.13, "C-OUT-001"),
            ("D", "A", "设备", 1_800_000, 234_000, 0.13, "D-OUT-001"),
        ]:
            db.add(Invoice(
                project_id=p.id, invoice_no=no, period=period,
                entity_code=code, direction="out",
                counterparty_code=cp, category=cat,
                net=Decimal(str(net)), vat=Decimal(str(vat)),
                rate=Decimal(str(rate)), deductible=False,
            ))

        # 真实底层成本
        real = [
            ("A", "", "项目管理", "site_salary",
             1_200_000, 1, "A项目部工资管理"),
            ("B", "", "劳务", "salary_social",
             4_350_000, 1, "B工资社保真实成本"),
            ("C", "", "材料", "external_purchase",
             7_550_000, 1, "C材料对外采购真实成本"),
            ("D", "", "设备", "depr_fuel_maintenance",
             1_250_000, 1, "D折旧维修燃料真实成本"),
            ("A", "甲", "劳务", "external_labor",
             1_100_000, 1, "第三方劳务真实外部成本"),
            ("A", "乙", "设备", "external_equipment",
             650_000, 1, "第三方设备外部成本"),
            ("A", "丙", "材料", "external_material",
             4_300_000, 1, "外部材料成本"),
            ("A", "丁", "专业分包", "external_subcontract",
             1_600_000, 1, "外部专业分包成本"),
        ]
        for owner, source, cat, sub, amt, ext, note in real:
            db.add(RealCost(
                project_id=p.id, entity_code=owner,
                counterparty_code=source, category=cat,
                subcategory=sub, period=period,
                amount=Decimal(str(amt)),
                external_cash=bool(ext), note=note,
            ))

        # 现金流
        for cp, cat, amt in [
            ("B", "劳务", 5_000_000), ("C", "材料", 8_000_000),
            ("D", "设备", 1_500_000), ("甲", "劳务", 900_000),
            ("乙", "设备", 700_000), ("丙", "材料", 3_800_000),
            ("丁", "专业分包", 1_200_000),
        ]:
            db.add(CashFlow(
                project_id=p.id, entity_code="A",
                counterparty_code=cp, direction="out",
                amount=Decimal(str(amt)), period=period,
                note=f"{cat}付款",
            ))
        db.add(CashFlow(
            project_id=p.id, entity_code="A",
            counterparty_code="业主", direction="in",
            amount=Decimal("18000000"), period=period,
            note="工程回款",
        ))

        # 税务规则（演示基准，未复核）
        for code, rate, note in [
            ("VAT_CONSTRUCTION_GENERAL", 0.09, "演示基准，正式使用前复核"),
            ("VAT_MOVABLE_RENTAL", 0.13, "演示基准，正式使用前复核"),
            ("VAT_SIMPLIFIED", 0.03, "演示基准，正式使用前复核"),
            ("CIT_GENERAL", 0.25, "演示基准，正式使用前复核"),
        ]:
            db.add(TaxRule(
                code=code, rate=Decimal(str(rate)),
                effective_from="2026-01-01", reviewed=False, note=note,
            ))

        # 风险阈值（V0.2 新增）
        for t in RiskThreshold.defaults():
            db.add(t)

        # 成本科目树
        accounts = [
            ("1000", "", "材料", "材料"),
            ("1101", "1000", "钢材/主材", "材料"),
            ("2000", "", "劳务", "劳务"),
            ("2101", "2000", "工资社保", "劳务"),
            ("3000", "", "设备", "设备"),
            ("3101", "3000", "折旧维修燃料", "设备"),
            ("4000", "", "专业分包", "专业分包"),
            ("5000", "", "项目管理", "项目管理"),
            ("6000", "", "税费", "税金"),
        ]
        for code, parent, name, cat in accounts:
            db.add(CostAccount(
                code=code, parent_code=parent, name=name,
                category=cat, active=True,
            ))

        # AI 端点（两个 Mock）
        db.add(AIModelEndpoint(
            name="本地Mock经营审查器", adapter="mock",
            base_url="", chat_path="", model="mock-operations-v1",
            api_key_env="", enabled=True, timeout_seconds=10,
            note="不访问外网，用于验证经营审查流程",
        ))
        db.add(AIModelEndpoint(
            name="本地Mock合规复核器", adapter="mock",
            base_url="", chat_path="", model="mock-compliance-v1",
            api_key_env="", enabled=True, timeout_seconds=10,
            note="不访问外网，用于验证多模型交叉复核",
        ))

        # Prompt 模板
        prompts = [
            ("通用项目审查", "default", 1,
             "优先依据系统确定性数字和证据链，避免宽泛建议。",
             "检查经营真实性、数据完整性、利润、现金流、税务与可执行整改。"),
            ("合同专项审查", "contract", 1,
             "重点关注合同主体、业务性质、金额、履约、发票与付款的一致性。",
             "检查合同商业实质、内部/外部主体边界、金额异常、履约和税务条款。"),
            ("税务专项审查", "tax", 1,
             "不得把管理测算当作正式申报结论；未复核税务规则必须列为数据缺口。",
             "检查ABCD法人独立税负、进销项、规则复核状态及异常税负。"),
            ("设备专项审查", "equipment", 1,
             "重点区分设备裸租、配操作人员和设备施工作业，并核对台班证据。",
             "检查设备合同、台班、人员、发票税率、真实成本与付款。"),
            ("劳务专项审查", "labor", 1,
             "重点核对真实人员、工资社保、工程量、结算和发票付款证据。",
             "检查B与甲的业务真实性、成本穿透、履约证据和四流一致性。"),
            ("材料专项审查", "material", 1,
             "重点核对C真实外采、材料验收、数量价格与付款。",
             "检查C与丙材料业务的真实采购、内部交易抵消、验收和价格合理性。"),
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

        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    run()