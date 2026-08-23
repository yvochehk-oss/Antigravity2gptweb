with open('../chengdu_construction_tax_system_v1_0/app/planning/service.py', 'r') as f:
    content = f.read()

old_ext = """    for (ccode, cat), amt in ext_details.items():
        cname = external_map.get(ccode, {}).get("name", ccode)
        externalDetails.append({
            "category": cat or '外部支出',
            "supplier": f"{cname} ({ccode})",
            "nominal": float(amt * D("1.09")), # Fake nominal for VAT
            "real": float(amt)
        })"""

new_ext = """    for (ccode, cat), amt in ext_details.items():
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

content = content.replace(old_ext, new_ext)

with open('../chengdu_construction_tax_system_v1_0/app/planning/service.py', 'w') as f:
    f.write(content)
