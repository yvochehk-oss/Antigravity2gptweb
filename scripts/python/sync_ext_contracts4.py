import sys
import os
sys.path.append(os.path.abspath('source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0'))
from app.db import SessionLocal
from app.models import Project, Contract, ExternalParty, Invoice, CashFlow
from app.domain.entities import CANONICAL_ENTITY_CODES
from decimal import Decimal

DEMO_PROJECTS = [
    {
        'dir_name': '01_天府国际金融中心二期_CD-TF-001',
        'code': 'CD-TF-001',
        'contracts': [
            {'title': '天府国际金融中心二期总承包合同', 'seller': 'A08', 'buyer': 'EXT-TF', 'cat': '建筑工程施工总承包', 'amount': 1_450_000_000, 'no': 'CDTF-MAIN-2026-01'},
            {'title': '外部超高层深基坑地质监测与技术咨询服务合同', 'seller': 'EXT-EXP', 'buyer': 'A08', 'cat': '工程服务', 'amount': 15_000_000, 'no': 'TF-A08-EXT-EXP'},
            {'title': '外部特种高强合金钢直采供货合同', 'seller': 'EXT-PG', 'buyer': 'A08', 'cat': '材料采购', 'amount': 180_000_000, 'no': 'TF-A08-EXT-PG'},
            {'title': '外部500吨级超重型履带吊租赁与吊装合同', 'seller': 'EXT-CRANE', 'buyer': 'A08', 'cat': '机械租赁', 'amount': 45_000_000, 'no': 'TF-A08-EXT-CRANE'},
        ]
    },
    {
        'dir_name': '02_成渝中线高速互通_CY-CQ-002',
        'code': 'CY-CQ-002',
        'contracts': [
            {'title': '成渝中线高速公路跨线互通工程总承包合同', 'seller': 'A03', 'buyer': 'EXT-CY', 'cat': '公路工程施工总承包', 'amount': 850_000_000, 'no': 'CYGS-MAIN-2026-02'},
            {'title': '外部深水作业工程潜水与水上航道保障租赁合同', 'seller': 'EXT-SHIP', 'buyer': 'A03', 'cat': '机械租赁', 'amount': 22_000_000, 'no': 'CY-A03-EXT-SHIP'},
            {'title': '外部高强度水下抗冲刷特种混凝土直供合同', 'seller': 'EXT-CONC', 'buyer': 'A03', 'cat': '材料采购', 'amount': 95_000_000, 'no': 'CY-A03-EXT-CONC'},
        ]
    },
    {
        'dir_name': '03_广元白龙湖生态涵养_GY-LZ-003',
        'code': 'GY-LZ-003',
        'contracts': [
            {'title': '白龙湖库区水环境治理及生态涵养工程总承包合同', 'seller': 'A10', 'buyer': 'EXT-GY', 'cat': '市政公用工程施工总承包', 'amount': 420_000_000, 'no': 'GYBLH-MAIN-2026-03'},
            {'title': '外部生态景观植被与水土保持苗木直采合同', 'seller': 'EXT-TREE', 'buyer': 'A10', 'cat': '材料采购', 'amount': 55_000_000, 'no': 'GY-A10-EXT-TREE'},
        ]
    },
    {
        'dir_name': '04_成都高新西区微电网变电站_CD-GX-004',
        'code': 'CD-GX-004',
        'contracts': [
            {'title': '110kV变电站及配电微网工程总承包合同', 'seller': 'A07', 'buyer': 'EXT-GX', 'cat': '建筑工程施工总承包', 'amount': 180_000_000, 'no': 'CDGX-MAIN-2026-04'},
            {'title': '外部高压微网继电保护成套智能装置供销合同', 'seller': 'EXT-ABB-ELECTRIC', 'buyer': 'A07', 'cat': '材料采购', 'amount': 28_000_000, 'no': 'GX-A07-EXT-NARI'},
        ]
    },
    {
        'dir_name': '05_格尔木特种仓储综合配套_QY-GEM-005',
        'code': 'QY-GEM-005',
        'contracts': [
            {'title': '盐湖工业园区特种耐腐仓储设施总承包合同', 'seller': 'A01', 'buyer': 'EXT-GEM', 'cat': '建筑工程施工总承包', 'amount': 250_000_000, 'no': 'QYGEM-MAIN-2026-05'},
            {'title': '外部重型铁路专用卸货线路与轨道接轨分包合同', 'seller': 'EXT-QH-RAILWAY', 'buyer': 'A01', 'cat': '专业分包', 'amount': 30_000_000, 'no': 'GEM-A01-EXT-RAIL'},
        ]
    }
]

db = SessionLocal()

for proj_data in DEMO_PROJECTS:
    proj_code = proj_data['code']
    project = db.query(Project).filter(Project.code == proj_code).first()
    if not project:
        print(f"Project not found: {proj_code}")
        continue
        
    for contract_data in proj_data['contracts']:
        seller = contract_data['seller']
        buyer = contract_data['buyer']
        
        if seller.startswith("EXT-") or buyer.startswith("EXT-"):
            c_no = contract_data['no']
            existing = db.query(Contract).filter(Contract.contract_no == c_no).first()
            if not existing:
                print(f"Adding missing EXT contract: {c_no} ({buyer} -> {seller})")
                c = Contract(
                    project_id=project.id,
                    contract_no=c_no,
                    buyer_code=buyer,
                    seller_code=seller,
                    category=contract_data['cat'],
                    amount=Decimal(contract_data['amount']),
                    internal_trade=False
                )
                db.add(c)
                db.flush()
                
                internal_code = buyer if buyer in CANONICAL_ENTITY_CODES else seller
                cp_code = seller if buyer in CANONICAL_ENTITY_CODES else buyer
                
                inv = Invoice(
                    project_id=project.id,
                    invoice_no=f"INV-{c_no}-01",
                    period="2026-03",
                    entity_code=internal_code,
                    direction="out" if seller.startswith("A") else "in",
                    counterparty_code=cp_code,
                    category=contract_data['cat'],
                    net=Decimal(contract_data['amount']) * Decimal('0.5'),
                    vat=Decimal(contract_data['amount']) * Decimal('0.5') * Decimal('0.09')
                )
                db.add(inv)
                
                cf = CashFlow(
                    project_id=project.id,
                    entity_code=internal_code,
                    counterparty_code=cp_code,
                    direction="in" if seller.startswith("A") else "out",
                    amount=Decimal(contract_data['amount']) * Decimal('0.6'),
                    period="2026-03",
                    transaction_date="2026-03-22",
                    bank_reference=f"BANK-{c_no}-01"
                )
                db.add(cf)

db.commit()
print("Done syncing EXT contracts!")
