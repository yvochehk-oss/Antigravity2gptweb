"""V0.1手工回归脚本。建议在独立SQLite数据库运行：
DATABASE_URL=sqlite:///./data/v01_manual_test.db python -m app.seed
DATABASE_URL=sqlite:///./data/v01_manual_test.db python tests/v01_flow.py
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import SessionLocal
from app.models import AIModelEndpoint, AIReviewBatch, AIConsensusReport, RemediationTask

c=TestClient(app)
for path in ["/","/ai-review","/health-check","/tasks","/ai-prompts","/ai-models"]:
    assert c.get(path).status_code==200, path

db=SessionLocal(); endpoints=db.execute(select(AIModelEndpoint).where(AIModelEndpoint.enabled==True)).scalars().all(); db.close()
assert endpoints
ids=[str(x.id) for x in endpoints[:2]]
r=c.post("/health-check/run",data={"project_id":"1","profile":"standard","endpoint_ids":ids,"user_instruction":"V0.1回归测试"},follow_redirects=False)
assert r.status_code==303
bid=int(r.headers["location"].split("/")[-1])
assert c.get(f"/health-check/{bid}").status_code==200

db=SessionLocal(); batch=db.get(AIReviewBatch,bid); report=db.scalar(select(AIConsensusReport).where(AIConsensusReport.batch_id==bid))
assert batch and report
print("V0.1 FLOW PASS",batch.status,report.overall_risk,report.score)
db.close()
