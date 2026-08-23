import re

with open('../chengdu_construction_tax_system_v1_0/app/routers/planning.py', 'r') as f:
    content = f.read()

content = content.replace("    # admin_only(request) # Disabled for demo\n    db=SessionLocal()", "    admin_only(request)\n    db=SessionLocal()")
content = content.replace("    # user=admin_only(request)\n    user = type('User', (), {'username': 'demo_user'})()", "    user=admin_only(request)")

with open('../chengdu_construction_tax_system_v1_0/app/routers/planning.py', 'w') as f:
    f.write(content)
