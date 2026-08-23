import os, tempfile, json
from pathlib import Path

tmp=tempfile.mkdtemp(prefix="projectrag-v02-test-")
os.environ["PROJECT_RAG_DATA_DIR"]=tmp
os.environ["PROJECT_RAG_DB_URL"]=f"sqlite:///{Path(tmp)/'test.db'}"
os.environ["PROJECT_RAG_EMBEDDING_BACKEND"]="hash_v1"
os.environ["PROJECT_RAG_RERANKER_BACKEND"]="off"
os.environ["PROJECT_RAG_AUTO_START_WORKER"]="0"
os.environ["PROJECT_RAG_PROCESS_JOBS_INLINE"]="1"

from fastapi.testclient import TestClient
from app.db import Base,engine,SessionLocal,init_db
from app.main import app
from app.models import Project,Document,Chunk,IngestJob

Base.metadata.drop_all(engine); init_db()
client=TestClient(app)

def make_project(code="TEST001"):
    r=client.post("/api/v1/projects/sync",json={"project_code":code,"name":"测试项目","external_system":"construction-tax","external_project_id":code,"status":"ACTIVE"})
    assert r.status_code==200
    return r.json()["id"]

def test_end_to_end_markdown_and_dedupe():
    pid=make_project()
    content="# D设备租赁合同\n\n## 第七条 台班\n\nD设备公司应按月提供完整设备台班和操作记录。\n\n## 第八条 结算\n\n结算前应核对台班、验收和付款资料。"
    files={"file":("D设备租赁合同_2026-08.md",content.encode("utf-8"),"text/markdown")}
    data={"project_id":str(pid),"entity_code":"D","business_category":"equipment","period":"2026-08","auto_parse":"true"}
    r=client.post("/api/v1/documents/upload",files=files,data=data)
    assert r.status_code==200, r.text
    assert r.json()["parse_status"]=="INDEXED"
    assert r.json()["job_id"]
    did=r.json()["id"]
    r2=client.post("/api/v1/documents/upload",files=files,data=data)
    assert r2.status_code==200 and r2.json()["parse_status"]=="DUPLICATE"
    r=client.post("/api/v1/retrieve",json={"project_id":pid,"query":"设备结算需要什么台班资料","filters":{"entity_code":["D"],"business_category":["equipment"]},"top_k":5})
    assert r.status_code==200
    results=r.json()["results"]
    assert results and results[0]["document_id"]==did
    assert "台班" in results[0]["text"] or "台班" in results[0]["heading_path"]

def test_folder_import_and_audit(tmp_path):
    pid=make_project("FOLDER001")
    folder=tmp_path/"资料"; folder.mkdir()
    (folder/"主合同.md").write_text("# 主合同\n施工总承包合同基本条款",encoding="utf-8")
    (folder/"C_材料采购_2026-08.md").write_text("# 材料采购合同\nC商贸材料采购及验收",encoding="utf-8")
    r=client.post("/api/v1/documents/import-folder",json={"project_id":pid,"path":str(folder),"recursive":True,"auto_parse":True})
    assert r.status_code==200 and r.json()["count"]==2
    audit=client.get(f"/api/v1/projects/{pid}/audit").json()
    assert audit["counts"]["indexed"]==2
    assert audit["coverage"]["material"] is True

def test_content_list_preserves_page_and_heading(tmp_path):
    from app.services.chunker import chunks_from_content_list
    path=tmp_path/"demo_content_list.json"
    path.write_text(json.dumps([
        {"type":"text","text":"第七条 设备台班","text_level":1,"page_idx":4,"bbox":[0,0,1,1]},
        {"type":"text","text":"设备公司应提供完整台班记录。","page_idx":4,"bbox":[0,0,1,1]},
        {"type":"text","text":"台班应与结算一致。","page_idx":5,"bbox":[0,0,1,1]}
    ],ensure_ascii=False),encoding="utf-8")
    chunks=chunks_from_content_list(str(path))
    assert chunks[0]["heading_path"]=="第七条 设备台班" and chunks[0]["page_start"]==5 and chunks[0]["page_end"]==6

def test_fake_mineru_adapter(tmp_path, monkeypatch):
    from app.services import mineru_adapter
    fake=tmp_path/"mineru-fake"
    fake.write_text('''#!/bin/bash\nset -e\nOUT=""\nwhile [[ $# -gt 0 ]]; do\n  case "$1" in\n    -p) shift 2;;\n    -o) OUT="$2"; shift 2;;\n    *) shift;;\n  esac\ndone\nmkdir -p "$OUT/result"\necho '# Parsed by fake MinerU' > "$OUT/result/result.md"\necho '[{"type":"text","text":"设备台班证据","text_level":1,"page_idx":0,"bbox":[0,0,1,1]}]' > "$OUT/result/result_content_list.json"\n''',encoding="utf-8")
    fake.chmod(0o755); input_file=tmp_path/"a.pdf"; input_file.write_bytes(b"fake")
    monkeypatch.setattr(mineru_adapter,"MINERU_BIN",str(fake))
    result=mineru_adapter.parse_with_mineru("DOC-FAKE",str(input_file))
    assert result["markdown_path"].endswith("result.md") and result["content_list_path"].endswith("result_content_list.json")

def test_health_and_pages():
    assert client.get("/").status_code==200
    assert client.get("/search").status_code==200
    h=client.get("/api/v1/health")
    assert h.status_code==200
    # 优化版本号
    version = h.json()["version"]
    assert version.startswith("0.2")
