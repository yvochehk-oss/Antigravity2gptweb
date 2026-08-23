from app.seed import run
from app.db import SessionLocal
from app.engine import consolidated
run(); db=SessionLocal(); d=consolidated(db); assert d["rows"] and d["cost"]>0; print("SMOKE PASS",d["profit"]); db.close()
