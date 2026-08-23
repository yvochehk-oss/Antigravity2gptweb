with open('../chengdu_construction_tax_system_v1_0/app/planning/service.py', 'r') as f:
    content = f.read()

content = content.replace('"unit": f"{ccode} {cname}",', '"unit": f"{ecode} {ename}",')

with open('../chengdu_construction_tax_system_v1_0/app/planning/service.py', 'w') as f:
    f.write(content)
