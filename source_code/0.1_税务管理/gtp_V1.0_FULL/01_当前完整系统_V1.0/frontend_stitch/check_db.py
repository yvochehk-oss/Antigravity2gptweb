import sys
sys.path.append('../chengdu_construction_tax_system_v1_0')
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Project, Invoice, RealCost

engine = create_engine("postgresql://yvoche@localhost:5432/projectrag")
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

projects = db.query(Project).all()
print(f"Total projects: {len(projects)}")
for p in projects:
    inv_count = db.query(Invoice).filter(Invoice.project_id == p.id).count()
    rc_count = db.query(RealCost).filter(RealCost.project_id == p.id).count()
    print(f"Project {p.id}: {p.name} - Invoices: {inv_count}, RealCosts: {rc_count}")
