import csv, io, json
from fastapi import FastAPI,Request,Form,UploadFile,File
from fastapi.responses import HTMLResponse,RedirectResponse,JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from .db import SessionLocal, Base, engine
from .models import *
from .engine import consolidated,project_summary,matching_rows,rebuild_tax_ledger,scan_risks,cost_tree_summary
from .ai_review import SCOPES, run_review, now_iso
from .ai_orchestrator import HEALTH_PROFILES, run_health_check, recheck_task

app=FastAPI(title="建筑项目经营与税务统筹系统",version="2.4")
t=Jinja2Templates(directory="app/templates")

def audit(db,action,obj,obj_id,msg):
    db.add(AuditLog(action=action,object_type=obj,object_id=str(obj_id),message=msg))

@app.get("/",response_class=HTMLResponse)
def home(r:Request):
    db=SessionLocal(); d=consolidated(db); risks=db.execute(select(RiskEvent).where(RiskEvent.resolved==False)).scalars().all()
    db.close(); return t.TemplateResponse(r, "home.html", {"d":d, "risk_count":len(risks)})

@app.get("/project/{pid}",response_class=HTMLResponse)
def project(r:Request,pid:int):
    db=SessionLocal(); s=project_summary(db,pid)
    costs=db.execute(select(RealCost).where(RealCost.project_id==pid)).scalars().all()
    tree=cost_tree_summary(db,pid); db.close()
    return t.TemplateResponse(r, "project.html", {"s":s, "costs":costs, "tree":tree})

@app.get("/manage",response_class=HTMLResponse)
def manage(r:Request):
    db=SessionLocal()
    ps=db.execute(select(Project)).scalars().all(); es=db.execute(select(Entity)).scalars().all()
    db.close(); return t.TemplateResponse(r, "manage.html", {"projects":ps, "entities":es})

@app.post("/manage/cost")
def add_cost(project_id:int=Form(...),entity_code:str=Form(...),counterparty_code:str=Form(""),category:str=Form(...),subcategory:str=Form(""),
             period:str=Form("2026-08"),amount:float=Form(...),external_cash:bool=Form(False),note:str=Form("")):
    db=SessionLocal(); x=RealCost(project_id=project_id,entity_code=entity_code,counterparty_code=counterparty_code,
        category=category,subcategory=subcategory,period=period,amount=amount,external_cash=external_cash,note=note)
    db.add(x); db.flush(); audit(db,"CREATE","RealCost",x.id,f"{entity_code}/{category}/{amount}"); db.commit(); db.close()
    return RedirectResponse(f"/project/{project_id}",303)

@app.post("/manage/contract")
def add_contract(project_id:int=Form(...),contract_no:str=Form(""),buyer_code:str=Form(...),seller_code:str=Form(...),
                 category:str=Form(...),amount:float=Form(...),note:str=Form("")):
    db=SessionLocal(); x=Contract(project_id=project_id,contract_no=contract_no,buyer_code=buyer_code,seller_code=seller_code,
        category=category,amount=amount,internal_trade=buyer_code in {"A","B","C","D"} and seller_code in {"A","B","C","D"},note=note)
    db.add(x); db.flush(); audit(db,"CREATE","Contract",x.id,f"{contract_no}/{seller_code}/{amount}"); db.commit(); db.close()
    return RedirectResponse("/manage",303)

@app.post("/manage/invoice")
def add_invoice(project_id:int=Form(...),invoice_no:str=Form(""),period:str=Form(...),entity_code:str=Form(...),
                direction:str=Form(...),counterparty_code:str=Form(...),category:str=Form(...),net:float=Form(...),
                vat:float=Form(0),rate:float=Form(0),deductible:bool=Form(False),note:str=Form("")):
    db=SessionLocal(); x=Invoice(project_id=project_id,invoice_no=invoice_no,period=period,entity_code=entity_code,
        direction=direction,counterparty_code=counterparty_code,category=category,net=net,vat=vat,rate=rate,
        deductible=deductible,note=note)
    db.add(x); db.flush(); audit(db,"CREATE","Invoice",x.id,f"{invoice_no}/{direction}/{net+vat}"); db.commit(); db.close()
    return RedirectResponse("/manage",303)

@app.post("/manage/cashflow")
def add_cashflow(project_id:int=Form(...),entity_code:str=Form(...),counterparty_code:str=Form(...),direction:str=Form(...),
                 amount:float=Form(...),period:str=Form(...),note:str=Form("")):
    db=SessionLocal(); x=CashFlow(project_id=project_id,entity_code=entity_code,counterparty_code=counterparty_code,
        direction=direction,amount=amount,period=period,note=note)
    db.add(x); db.flush(); audit(db,"CREATE","CashFlow",x.id,f"{counterparty_code}/{direction}/{amount}"); db.commit(); db.close()
    return RedirectResponse("/manage",303)

@app.post("/manage/fulfillment")
def add_fulfillment(project_id:int=Form(...),counterparty_code:str=Form(...),kind:str=Form(...),category:str=Form(...),
                    quantity:float=Form(0),amount:float=Form(0),evidence_complete:bool=Form(False),note:str=Form("")):
    db=SessionLocal(); x=Fulfillment(project_id=project_id,counterparty_code=counterparty_code,kind=kind,category=category,
        quantity=quantity,amount=amount,evidence_complete=evidence_complete,note=note)
    db.add(x); db.flush(); audit(db,"CREATE","Fulfillment",x.id,f"{counterparty_code}/{kind}/{amount}"); db.commit(); db.close()
    return RedirectResponse("/manage",303)

@app.get("/matching",response_class=HTMLResponse)
def matching(r:Request,project_id:int|None=None):
    db=SessionLocal(); ps=db.execute(select(Project)).scalars().all()
    pid=project_id or (ps[0].id if ps else None); rows=matching_rows(db,pid) if pid else []
    db.close(); return t.TemplateResponse(r, "matching.html", {"projects":ps, "pid":pid, "rows":rows})

@app.get("/tax-ledger",response_class=HTMLResponse)
def tax_ledger(r:Request,period:str="2026-08"):
    db=SessionLocal(); rows=rebuild_tax_ledger(db,period)
    unreviewed=db.execute(select(TaxRule).where(TaxRule.reviewed==False)).scalars().all()
    db.close(); return t.TemplateResponse(r, "tax_ledger.html", {"rows":rows, "period":period, "unreviewed":len(unreviewed)})

@app.get("/risks",response_class=HTMLResponse)
def risks(r:Request,project_id:int|None=None):
    db=SessionLocal(); ps=db.execute(select(Project)).scalars().all()
    pid=project_id or (ps[0].id if ps else None); rows=scan_risks(db,pid) if pid else []
    db.close(); return t.TemplateResponse(r, "risks.html", {"projects":ps, "pid":pid, "rows":rows})

@app.get("/audit",response_class=HTMLResponse)
def audits(r:Request):
    db=SessionLocal(); rows=db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(200)).scalars().all()
    db.close(); return t.TemplateResponse(r, "audit.html", {"rows":rows})

@app.get("/imports",response_class=HTMLResponse)
def imports(r:Request):
    db=SessionLocal(); ps=db.execute(select(Project)).scalars().all(); db.close()
    return t.TemplateResponse(r, "imports.html", {"projects":ps, "message":""})

@app.post("/imports/invoices",response_class=HTMLResponse)
async def import_invoices(r:Request,project_id:int=Form(...),file:UploadFile=File(...)):
    raw=(await file.read()).decode("utf-8-sig"); reader=csv.DictReader(io.StringIO(raw)); db=SessionLocal(); ok=0; errors=[]
    for n,row in enumerate(reader,2):
        try:
            x=Invoice(project_id=project_id,invoice_no=row.get("invoice_no",""),period=row["period"],entity_code=row["entity_code"],
                direction=row["direction"],counterparty_code=row["counterparty_code"],category=row["category"],
                net=float(row["net"]),vat=float(row.get("vat") or 0),rate=float(row.get("rate") or 0),
                deductible=str(row.get("deductible","1")).lower() in ("1","true","yes","y"),note=row.get("note",""))
            db.add(x); ok+=1
        except Exception as e: errors.append(f"第{n}行: {e}")
    audit(db,"IMPORT","Invoice",project_id,f"{file.filename}: 成功{ok}行，失败{len(errors)}行"); db.commit()
    ps=db.execute(select(Project)).scalars().all(); db.close()
    msg=f"发票导入完成：成功 {ok} 行，失败 {len(errors)} 行。" + (" | "+"; ".join(errors[:5]) if errors else "")
    return t.TemplateResponse(r, "imports.html", {"projects":ps, "message":msg})

@app.post("/imports/cashflows",response_class=HTMLResponse)
async def import_cashflows(r:Request,project_id:int=Form(...),file:UploadFile=File(...)):
    raw=(await file.read()).decode("utf-8-sig"); reader=csv.DictReader(io.StringIO(raw)); db=SessionLocal(); ok=0; errors=[]
    for n,row in enumerate(reader,2):
        try:
            db.add(CashFlow(project_id=project_id,entity_code=row["entity_code"],counterparty_code=row["counterparty_code"],
                direction=row["direction"],amount=float(row["amount"]),period=row["period"],note=row.get("note",""))); ok+=1
        except Exception as e: errors.append(f"第{n}行: {e}")
    audit(db,"IMPORT","CashFlow",project_id,f"{file.filename}: 成功{ok}行，失败{len(errors)}行"); db.commit()
    ps=db.execute(select(Project)).scalars().all(); db.close()
    msg=f"流水导入完成：成功 {ok} 行，失败 {len(errors)} 行。" + (" | "+"; ".join(errors[:5]) if errors else "")
    return t.TemplateResponse(r, "imports.html", {"projects":ps, "message":msg})

@app.get("/api/projects/{pid}")
def api_project(pid:int):
    db=SessionLocal(); s=project_summary(db,pid)
    out={k:v for k,v in s.items() if k!="project"}; out["project"]={"id":s["project"].id,"code":s["project"].code,"name":s["project"].name}
    db.close(); return out

@app.get("/api/projects/{pid}/matching")
def api_matching(pid:int):
    db=SessionLocal(); rows=matching_rows(db,pid); db.close(); return rows


@app.get("/ai-review",response_class=HTMLResponse)
def ai_review_home(r:Request,project_id:int|None=None,scope:str|None=None):
    db=SessionLocal()
    projects=db.execute(select(Project)).scalars().all()
    endpoints=db.execute(select(AIModelEndpoint).where(AIModelEndpoint.enabled==True).order_by(AIModelEndpoint.id)).scalars().all()
    jobs=db.execute(select(AIReviewJob).order_by(AIReviewJob.id.desc()).limit(50)).scalars().all()
    project_map={p.id:p for p in projects}; endpoint_map={e.id:e for e in db.execute(select(AIModelEndpoint)).scalars().all()}
    db.close()
    return t.TemplateResponse(r, "ai_review.html", {"projects":projects, "endpoints":endpoints, "jobs":jobs,
        "project_map":project_map, "endpoint_map":endpoint_map, "scopes":SCOPES, "selected_project_id":project_id, "selected_scope":scope})

@app.post("/ai-review/run")
def ai_review_run(project_id:int=Form(...),scope:str=Form(...),endpoint_id:int=Form(...),user_instruction:str=Form("")):
    db=SessionLocal()
    if scope not in SCOPES:
        db.close(); return RedirectResponse("/ai-review",303)
    job=AIReviewJob(project_id=project_id,scope=scope,endpoint_id=endpoint_id,user_instruction=user_instruction,
                    status="pending",created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds"))
    db.add(job); db.flush(); audit(db,"AI_REVIEW_START","AIReviewJob",job.id,f"project={project_id}, scope={scope}, endpoint={endpoint_id}")
    db.commit()
    try:
        run_review(db,job)
        audit(db,"AI_REVIEW_COMPLETE","AIReviewJob",job.id,f"status={job.status}")
        db.commit()
    except Exception as e:
        audit(db,"AI_REVIEW_FAILED","AIReviewJob",job.id,str(e))
        db.commit()
    jid=job.id; db.close()
    return RedirectResponse(f"/ai-review/{jid}",303)

@app.get("/ai-review/{job_id}",response_class=HTMLResponse)
def ai_review_detail(r:Request,job_id:int):
    db=SessionLocal()
    job=db.get(AIReviewJob,job_id)
    if not job:
        db.close(); return HTMLResponse("AI review job not found",404)
    project=db.get(Project,job.project_id); endpoint=db.get(AIModelEndpoint,job.endpoint_id)
    prompt_template=db.get(AIPromptTemplate,job.prompt_template_id) if job.prompt_template_id else None
    result=db.scalar(select(AIReviewResult).where(AIReviewResult.job_id==job.id))
    findings=[]; recommendations=[]; gaps=[]
    if result:
        try: findings=json.loads(result.findings_json or "[]")
        except: findings=[]
        try: recommendations=json.loads(result.recommendations_json or "[]")
        except: recommendations=[]
        try: gaps=json.loads(result.data_gaps_json or "[]")
        except: gaps=[]
    db.close()
    return t.TemplateResponse(r, "ai_review_detail.html", {"job":job, "project":project, "endpoint":endpoint,
        "result":result, "findings":findings, "recommendations":recommendations, "gaps":gaps, "scope_name":SCOPES.get(job.scope,job.scope), "prompt_template":prompt_template})

@app.get("/ai-models",response_class=HTMLResponse)
def ai_models(r:Request):
    db=SessionLocal(); rows=db.execute(select(AIModelEndpoint).order_by(AIModelEndpoint.id)).scalars().all(); db.close()
    return t.TemplateResponse(r, "ai_models.html", {"rows":rows})

@app.post("/ai-models")
def ai_model_add(name:str=Form(...),adapter:str=Form("openai_compatible"),base_url:str=Form(""),
                 chat_path:str=Form("/v1/chat/completions"),model:str=Form(""),api_key_env:str=Form(""),
                 timeout_seconds:int=Form(90),note:str=Form("")):
    db=SessionLocal()
    x=AIModelEndpoint(name=name,adapter=adapter,base_url=base_url,chat_path=chat_path,model=model,
                      api_key_env=api_key_env,enabled=True,timeout_seconds=timeout_seconds,note=note)
    db.add(x); db.flush(); audit(db,"CREATE","AIModelEndpoint",x.id,f"{name}/{adapter}/{model}"); db.commit(); db.close()
    return RedirectResponse("/ai-models",303)

@app.post("/ai-models/{endpoint_id}/toggle")
def ai_model_toggle(endpoint_id:int):
    db=SessionLocal(); x=db.get(AIModelEndpoint,endpoint_id)
    if x:
        x.enabled=not x.enabled; audit(db,"UPDATE","AIModelEndpoint",x.id,f"enabled={x.enabled}"); db.commit()
    db.close(); return RedirectResponse("/ai-models",303)

@app.get("/api/ai-review/{job_id}")
def api_ai_review(job_id:int):
    db=SessionLocal(); job=db.get(AIReviewJob,job_id)
    if not job:
        db.close(); return JSONResponse({"error":"not found"},404)
    result=db.scalar(select(AIReviewResult).where(AIReviewResult.job_id==job.id))
    out={"job":{"id":job.id,"project_id":job.project_id,"scope":job.scope,"status":job.status,
                "created_at":job.created_at,"started_at":job.started_at,"finished_at":job.finished_at,
                "input_digest":job.input_digest,"error_message":job.error_message}}
    if result:
        out["result"]={"risk_level":result.risk_level,"score":result.score,"summary":result.summary,
                       "findings":json.loads(result.findings_json or "[]"),
                       "recommendations":json.loads(result.recommendations_json or "[]"),
                       "data_gaps":json.loads(result.data_gaps_json or "[]"),
                       "provider_name":result.provider_name,"model_name":result.model_name}
    db.close(); return out


@app.get("/health-check",response_class=HTMLResponse)
def health_check_home(r:Request,project_id:int|None=None):
    db=SessionLocal()
    projects=db.execute(select(Project).order_by(Project.id)).scalars().all()
    endpoints=db.execute(select(AIModelEndpoint).where(AIModelEndpoint.enabled==True).order_by(AIModelEndpoint.id)).scalars().all()
    batches=db.execute(select(AIReviewBatch).order_by(AIReviewBatch.id.desc()).limit(30)).scalars().all()
    project_map={p.id:p for p in projects}
    db.close()
    return t.TemplateResponse(r, "health_check.html", {"projects":projects, "endpoints":endpoints,
        "batches":batches, "project_map":project_map, "profiles":HEALTH_PROFILES, "selected_project_id":project_id})

@app.post("/health-check/run")
def health_check_run(project_id:int=Form(...),profile:str=Form("standard"),endpoint_ids:list[int]=Form(...),user_instruction:str=Form("")):
    db=SessionLocal()
    scopes=HEALTH_PROFILES.get(profile,HEALTH_PROFILES["standard"])
    batch=AIReviewBatch(project_id=project_id,profile=profile,scopes_json=json.dumps(scopes,ensure_ascii=False),
        endpoint_ids_json=json.dumps(endpoint_ids),user_instruction=user_instruction,status="pending",created_at=now_iso())
    db.add(batch); db.flush(); audit(db,"AI_HEALTH_START","AIReviewBatch",batch.id,f"project={project_id}, profile={profile}, endpoints={endpoint_ids}"); db.commit()
    try:
        run_health_check(db,batch)
        audit(db,"AI_HEALTH_COMPLETE","AIReviewBatch",batch.id,f"status={batch.status}"); db.commit()
    except Exception as e:
        batch.status="failed"; batch.error_message=str(e); batch.finished_at=now_iso();
        audit(db,"AI_HEALTH_FAILED","AIReviewBatch",batch.id,str(e)); db.commit()
    bid=batch.id; db.close(); return RedirectResponse(f"/health-check/{bid}",303)

@app.get("/health-check/{batch_id}",response_class=HTMLResponse)
def health_check_detail(r:Request,batch_id:int):
    db=SessionLocal(); batch=db.get(AIReviewBatch,batch_id)
    if not batch:
        db.close(); return HTMLResponse("health check batch not found",404)
    project=db.get(Project,batch.project_id)
    jobs=db.execute(select(AIReviewJob).where(AIReviewJob.batch_id==batch.id).order_by(AIReviewJob.scope,AIReviewJob.endpoint_id)).scalars().all()
    result_map={}
    endpoint_map={e.id:e for e in db.execute(select(AIModelEndpoint)).scalars().all()}
    for j in jobs:
        rr=db.scalar(select(AIReviewResult).where(AIReviewResult.job_id==j.id)); result_map[j.id]=rr
    report=db.scalar(select(AIConsensusReport).where(AIConsensusReport.batch_id==batch.id))
    common=[]; differences=[]; recommendations=[]; gaps=[]
    if report:
        try: common=json.loads(report.common_findings_json or "[]")
        except: pass
        try: differences=json.loads(report.differences_json or "[]")
        except: pass
        try: recommendations=json.loads(report.recommendations_json or "[]")
        except: pass
        try: gaps=json.loads(report.data_gaps_json or "[]")
        except: pass
    db.close()
    return t.TemplateResponse(r, "health_check_detail.html", {"batch":batch, "project":project, "jobs":jobs,
        "result_map":result_map, "endpoint_map":endpoint_map, "report":report, "common":common, "differences":differences,
        "recommendations":recommendations, "gaps":gaps, "scopes":SCOPES})

@app.get("/ai-prompts",response_class=HTMLResponse)
def ai_prompts(r:Request):
    db=SessionLocal(); rows=db.execute(select(AIPromptTemplate).order_by(AIPromptTemplate.scope,AIPromptTemplate.version.desc())).scalars().all(); db.close()
    return t.TemplateResponse(r, "ai_prompts.html", {"rows":rows, "scopes":SCOPES})

@app.post("/ai-prompts")
def ai_prompt_add(name:str=Form(...),scope:str=Form(...),system_addendum:str=Form(""),review_focus:str=Form("")):
    db=SessionLocal(); existing=db.execute(select(AIPromptTemplate).where(AIPromptTemplate.scope==scope)).scalars().all()
    ver=max([x.version for x in existing],default=0)+1
    x=AIPromptTemplate(name=name,scope=scope,version=ver,system_addendum=system_addendum,review_focus=review_focus,enabled=True,created_at=now_iso())
    db.add(x); db.flush(); audit(db,"CREATE","AIPromptTemplate",x.id,f"{scope}/v{ver}/{name}"); db.commit(); db.close(); return RedirectResponse("/ai-prompts",303)

@app.post("/ai-prompts/{template_id}/toggle")
def ai_prompt_toggle(template_id:int):
    db=SessionLocal(); x=db.get(AIPromptTemplate,template_id)
    if x:
        x.enabled=not x.enabled; audit(db,"UPDATE","AIPromptTemplate",x.id,f"enabled={x.enabled}"); db.commit()
    db.close(); return RedirectResponse("/ai-prompts",303)

@app.get("/tasks",response_class=HTMLResponse)
def task_center(r:Request):
    db=SessionLocal(); rows=db.execute(select(RemediationTask).order_by(RemediationTask.id.desc())).scalars().all()
    projects={p.id:p for p in db.execute(select(Project)).scalars().all()}
    endpoints=db.execute(select(AIModelEndpoint).where(AIModelEndpoint.enabled==True).order_by(AIModelEndpoint.id)).scalars().all()
    db.close(); return t.TemplateResponse(r, "tasks.html", {"rows":rows, "projects":projects, "endpoints":endpoints, "scopes":SCOPES})

@app.post("/tasks/create")
def task_create(project_id:int=Form(...),scope:str=Form("whole_project"),title:str=Form(...),description:str=Form(""),
                priority:str=Form("P2"),owner_role:str=Form("项目财务/商务"),source_job_id:int|None=Form(None),source_batch_id:int|None=Form(None)):
    db=SessionLocal(); x=RemediationTask(project_id=project_id,scope=scope,source_job_id=source_job_id,source_batch_id=source_batch_id,
        title=title[:200],description=description,priority=priority,owner_role=owner_role,status="open",created_at=now_iso(),updated_at=now_iso())
    db.add(x); db.flush(); audit(db,"CREATE","RemediationTask",x.id,f"{priority}/{title[:100]}"); db.commit(); db.close(); return RedirectResponse("/tasks",303)

@app.post("/tasks/{task_id}/status")
def task_status(task_id:int,status:str=Form(...)):
    allowed={"open","in_progress","done","rechecked","verified","closed"}
    db=SessionLocal(); x=db.get(RemediationTask,task_id)
    if x and status in allowed:
        x.status=status; x.updated_at=now_iso();
        if status in {"verified","closed"}: x.closed_at=now_iso()
        audit(db,"UPDATE","RemediationTask",x.id,f"status={status}"); db.commit()
    db.close(); return RedirectResponse("/tasks",303)

@app.post("/tasks/{task_id}/recheck")
def task_recheck(task_id:int,endpoint_id:int=Form(...)):
    db=SessionLocal(); x=db.get(RemediationTask,task_id)
    if not x:
        db.close(); return RedirectResponse("/tasks",303)
    try:
        job=recheck_task(db,x,endpoint_id); audit(db,"AI_RECHECK","RemediationTask",x.id,f"job={job.id}"); db.commit(); jid=job.id
    except Exception as e:
        audit(db,"AI_RECHECK_FAILED","RemediationTask",x.id,str(e)); db.commit(); db.close(); return RedirectResponse("/tasks",303)
    db.close(); return RedirectResponse(f"/ai-review/{jid}",303)

@app.get("/api/health-check/{batch_id}")
def api_health_check(batch_id:int):
    db=SessionLocal(); batch=db.get(AIReviewBatch,batch_id)
    if not batch:
        db.close(); return JSONResponse({"error":"not found"},404)
    report=db.scalar(select(AIConsensusReport).where(AIConsensusReport.batch_id==batch.id))
    out={"batch":{"id":batch.id,"project_id":batch.project_id,"profile":batch.profile,"status":batch.status,
                  "scopes":json.loads(batch.scopes_json or "[]"),"endpoint_ids":json.loads(batch.endpoint_ids_json or "[]"),
                  "created_at":batch.created_at,"finished_at":batch.finished_at,"error_message":batch.error_message}}
    if report:
        out["consensus"]={"overall_risk":report.overall_risk,"score":report.score,"summary":report.summary,
            "common_findings":json.loads(report.common_findings_json or "[]"),"differences":json.loads(report.differences_json or "[]"),
            "recommendations":json.loads(report.recommendations_json or "[]"),"data_gaps":json.loads(report.data_gaps_json or "[]")}
    db.close(); return out
