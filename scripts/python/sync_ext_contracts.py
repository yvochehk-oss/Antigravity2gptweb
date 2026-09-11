import sys
import os
sys.path.append(os.path.abspath('source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0'))

from scripts.generate_project_archives import DEMO_PROJECTS
from app.db import SessionLocal
from app.models import Project, Contract, ExternalParty, Invoice, CashFlow
from decimal import Decimal
import random

db = SessionLocal()

ext_parties = {p.code: p for p in db.query(ExternalParty).all()}

for proj_data in DEMO_PROJECTS:
    proj_code = proj_data['code']
    project = db.query(Project).filter(Project.code == proj_code).first()
    if not project:
        print(f"Project not found: {proj_code}")
        continue
        
    for contract_data in proj_data['contracts']:
        seller = contract_data['seller']
        buyer = contract_data['buyer']
        
        # We want to sync all contracts that have an EXT- buyer or seller
        if seller.startswith("EXT-") or buyer.startswith("EXT-"):
            # Check if it exists
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
                    internal_trade=False,
                    recognized_cost=Decimal(contract_data['amount']) * Decimal('0.8'),
                    recognized_revenue=Decimal('0'),
                    collection=Decimal('0'),
                    payment=Decimal(contract_data['amount']) * Decimal('0.6'),
                )
                db.add(c)
                db.flush()
                
                # Also generate some invoices and cashflows for it so they have data
                inv = Invoice(
                    project_id=project.id,
                    contract_id=c.id,
                    invoice_no=f"INV-{c_no}-01",
                    issuer_code=seller,
                    receiver_code=buyer,
                    amount=Decimal(contract_data['amount']) * Decimal('0.5'),
                    tax_amount=Decimal(contract_data['amount']) * Decimal('0.5') * Decimal('0.09'),
                    issue_date="2026-03-10",
                    direction="inbound" if buyer.startswith("A") else "outbound",
                    category=contract_data['cat'],
                    status="verified"
                )
                db.add(inv)
                
                cf = CashFlow(
                    project_id=project.id,
                    contract_id=c.id,
                    flow_no=f"CF-{c_no}-01",
                    payer_code=buyer,
                    payee_code=seller,
                    amount=Decimal(contract_data['amount']) * Decimal('0.6'),
                    flow_date="2026-03-22",
                    direction="outflow" if buyer.startswith("A") else "inflow",
                    category="engineering"
                )
                db.add(cf)

db.commit()
print("Done syncing EXT contracts!")
