with open('../chengdu_construction_tax_system_v1_0/app/planning/service.py', 'r') as f:
    content = f.read()

# The ext_cost_rows query and loop — replace to also group by entity_code and use note
old_ext_query = """    ext_cost_rows = db.execute(
        select(RealCost).where(RealCost.project_id == project_id, RealCost.external_cash.is_(True))
    ).scalars().all()"""

new_ext_query = """    ext_cost_rows = db.execute(
        select(RealCost).where(RealCost.project_id == project_id, RealCost.external_cash.is_(True))
    ).scalars().all()
    # Build entity_code → name map for real cost attribution
    entity_name_map = {e.code: e.name for e in db.execute(select(Entity)).scalars().all()}"""

content = content.replace(old_ext_query, new_ext_query)

# Fix the loop: use (entity_code, counterparty_code, category) as key
old_ext_loop = """    for r in ext_cost_rows:
        amt = _dec(r.amount)
        external_cost += amt
        ext_details[(r.counterparty_code, r.category)] += amt"""

new_ext_loop = """    for r in ext_cost_rows:
        amt = _dec(r.amount)
        external_cost += amt
        ext_details[(r.entity_code, r.counterparty_code, r.category)] += amt"""

content = content.replace(old_ext_loop, new_ext_loop)

# Fix the rendering loop to use entity_code for better display
old_render = """    externalDetails = []
    for (ccode, cat), amt in ext_details.items():
        if ccode:
            cname = external_map.get(ccode, {}).get("name", ccode)
            supplier_name = f"{cname} ({ccode})"
        else:
            supplier_name = "散户 / 未登记零星供应商 (分散支付)"
            
        externalDetails.append({
            "category": cat or '外部支出',
            "supplier": supplier_name,
            "nominal": float(amt * D("1.09")), # Fake nominal for VAT
            "real": float(amt)
        })"""

new_render = """    externalDetails = []
    for (ecode, ccode, cat), amt in ext_details.items():
        if ccode:
            # 有明确外部对手方
            cname = external_map.get(ccode, {}).get("name", ccode)
            supplier_name = f"{cname} ({ccode})"
        elif ecode and ecode in entity_name_map:
            # 归属到系统内单位自身发生的实际外部支出（如工资、设备折旧）
            supplier_name = f"{entity_name_map[ecode]} ({ecode}) · 系统内单位自营支出"
        else:
            supplier_name = "散户 / 未登记零星供应商 (分散支付)"
            
        externalDetails.append({
            "category": cat or '外部支出',
            "entity": ecode,
            "supplier": supplier_name,
            "nominal": float(amt * D("1.09")),
            "real": float(amt)
        })"""

content = content.replace(old_render, new_render)

# Make sure Entity is imported in service.py
if 'from ..models import' in content and 'Entity' not in content:
    content = content.replace('from ..models import', 'from ..models import Entity,')

with open('../chengdu_construction_tax_system_v1_0/app/planning/service.py', 'w') as f:
    f.write(content)

print("service.py updated - externalDetails now uses entity attribution")
