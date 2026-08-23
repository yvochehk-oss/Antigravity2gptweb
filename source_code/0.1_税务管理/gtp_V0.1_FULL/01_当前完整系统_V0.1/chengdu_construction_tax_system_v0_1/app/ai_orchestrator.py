import json, re
from collections import defaultdict
from sqlalchemy import select
from .models import AIReviewBatch, AIReviewJob, AIReviewResult, AIConsensusReport, AIModelEndpoint, RemediationTask
from .ai_review import run_review, now_iso

HEALTH_PROFILES = {
    "quick": ["overview", "risk", "eac"],
    "standard": ["contract", "fulfillment", "invoice", "cashflow", "cost", "tax", "eac", "risk"],
    "deep": ["contract", "fulfillment", "invoice", "cashflow", "cost", "tax", "eac", "risk", "material", "labor", "equipment", "subcontract"],
}

RISK_ORDER={"UNKNOWN":0,"LOW":1,"MEDIUM":2,"HIGH":3,"CRITICAL":4}

def _loads(text, default):
    try: return json.loads(text or "")
    except Exception: return default

def _norm(s):
    return re.sub(r"\s+", "", (s or "").lower())[:180]

def build_consensus(db, batch_id:int):
    batch=db.get(AIReviewBatch,batch_id)
    jobs=db.execute(select(AIReviewJob).where(AIReviewJob.batch_id==batch_id).order_by(AIReviewJob.id)).scalars().all()
    completed=[]
    endpoint_names={x.id:x.name for x in db.execute(select(AIModelEndpoint)).scalars().all()}
    for j in jobs:
        r=db.scalar(select(AIReviewResult).where(AIReviewResult.job_id==j.id))
        if r: completed.append((j,r))

    finding_groups=defaultdict(list); recs={}; gaps=[]; scores=[]; risk="UNKNOWN"
    for j,r in completed:
        scores.append(r.score)
        if RISK_ORDER.get(r.risk_level,0)>RISK_ORDER.get(risk,0): risk=r.risk_level
        for f in _loads(r.findings_json,[]):
            key=(j.scope,_norm(f.get("area")),_norm(f.get("issue")))
            item=dict(f); item["scope"]=j.scope; item["job_id"]=j.id; item["model"]=endpoint_names.get(j.endpoint_id,str(j.endpoint_id))
            finding_groups[key].append(item)
        for rec in _loads(r.recommendations_json,[]):
            key=_norm(rec.get("action"))
            if key and key not in recs:
                item=dict(rec); item["scope"]=j.scope; item["source_job_id"]=j.id
                recs[key]=item
        for g in _loads(r.data_gaps_json,[]):
            if g not in gaps: gaps.append(g)

    common=[]; differences=[]
    selected_endpoint_count=len(set(_loads(batch.endpoint_ids_json,[]))) if batch else 1
    for key,items in finding_groups.items():
        models=set(x["model"] for x in items)
        if selected_endpoint_count>1 and len(models)>=2:
            base=dict(items[0]); base["confirmed_by"]=sorted(models); base["count"]=len(items); common.append(base)
        elif selected_endpoint_count==1 and len(items)>=1:
            # 单模型体检也把确定发现作为共识项展示
            base=dict(items[0]); base["confirmed_by"]=sorted(models); base["count"]=len(items); common.append(base)
        else:
            differences.append({"scope":key[0],"issue":items[0].get("issue",""),"opinions":items})

    avg=sum(scores)/len(scores) if scores else 0
    failed=sum(1 for j in jobs if j.status=="failed")
    summary=f"完成 {len(completed)}/{len(jobs)} 次AI子检查；形成 {len(common)} 项共识/确定发现，{len(differences)} 项模型差异。"
    if failed: summary += f" 另有 {failed} 次模型调用失败。"

    old=db.scalar(select(AIConsensusReport).where(AIConsensusReport.batch_id==batch_id))
    if old: db.delete(old); db.flush()
    report=AIConsensusReport(batch_id=batch_id,overall_risk=risk,score=avg,summary=summary,
        common_findings_json=json.dumps(common,ensure_ascii=False),
        differences_json=json.dumps(differences,ensure_ascii=False),
        recommendations_json=json.dumps(list(recs.values()),ensure_ascii=False),
        data_gaps_json=json.dumps(gaps,ensure_ascii=False))
    db.add(report); db.commit(); return report

def run_health_check(db,batch:AIReviewBatch):
    batch.status="running"; db.commit()
    scopes=_loads(batch.scopes_json,[]) or HEALTH_PROFILES.get(batch.profile,HEALTH_PROFILES["standard"])
    endpoint_ids=_loads(batch.endpoint_ids_json,[])
    if not endpoint_ids:
        raise ValueError("至少选择一个模型端点")
    failures=[]
    for scope in scopes:
        for eid in endpoint_ids:
            endpoint=db.get(AIModelEndpoint,eid)
            if not endpoint or not endpoint.enabled:
                failures.append(f"scope={scope}, endpoint={eid}: 端点不可用"); continue
            job=AIReviewJob(project_id=batch.project_id,scope=scope,endpoint_id=eid,batch_id=batch.id,
                user_instruction=batch.user_instruction,status="pending",created_at=now_iso())
            db.add(job); db.commit()
            try: run_review(db,job)
            except Exception as e: failures.append(f"scope={scope}, endpoint={endpoint.name}: {e}")
    build_consensus(db,batch.id)
    batch.status="completed_with_errors" if failures else "completed"
    batch.error_message="\n".join(failures)[:10000]
    batch.finished_at=now_iso(); db.commit(); return batch

def recheck_task(db, task:RemediationTask, endpoint_id:int):
    instruction=f"这是整改后的复检。整改任务：{task.title}。整改说明：{task.description}。请重点判断原问题是否得到改善，并指出仍需补充的证据。"
    job=AIReviewJob(project_id=task.project_id,scope=task.scope or "whole_project",endpoint_id=endpoint_id,
                    parent_job_id=task.source_job_id,user_instruction=instruction,status="pending",created_at=now_iso())
    db.add(job); db.commit(); run_review(db,job)
    task.recheck_job_id=job.id; task.status="rechecked"; task.updated_at=now_iso(); db.commit(); return job
