import sys
import os
sys.path.append(os.path.join(os.getcwd(), "source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0"))
from app.db import SessionLocal
from app.models import Entity, Project
db = SessionLocal()
db.query(Project).delete()
db.query(Entity).delete()
db.commit()
print("Business data cleared.")
