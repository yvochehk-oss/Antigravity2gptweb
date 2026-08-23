"""V0.2: 体检编排（线程池并行）+ 共识引擎 + 整改复检。"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import HEALTH_PROFILES, RISK_ORDER
from ..models import (
    AIConsensusReport,
    AIModelEndpoint,
    AIReviewBatch,
    AIReviewJob,
    AIReviewResult,
    RemediationTask,
)
from .review import run_review
from .timeutil import now_iso


def _loads(text: str | None, default: Any) -> Any:
    try:
        return json.loads(text or "")
    except Exception:
        return default


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())[:180]


def _single_run(batch_id: int, scope: str, endpoint_id: int,
                user_instruction: str) -> tuple[str, int, str]:
    """线程池 worker：在独立 session 中执行 run_review。"""
    from ..db import SessionLocal  # 避免循环
    from ..models import AIReviewBatch

    sess = SessionLocal()
    try:
        batch = sess.get(AIReviewBatch, batch_id)
        if not batch:
            return scope, endpoint_id, "failed:batch not found"
        job = AIReviewJob(
            project_id=batch.project_id,
            scope=scope,
            endpoint_id=endpoint_id,
            batch_id=batch_id,
            user_instruction=user_instruction,
            status="pending",
            created_at=now_iso(),
            actor=batch.actor,
        )
        sess.add(job); sess.commit()
        try:
            run_review(sess, job)
            return scope, endpoint_id, "ok"
        except Exception as e:  # noqa: BLE001
            return scope, endpoint_id, f"failed:{e}"
    finally:
        sess.close()


def run_health_check(db: Session, batch: AIReviewBatch) -> AIReviewBatch:
    """多 scope × 多模型 并行执行（V0.2）。"""
    batch.status = "running"
    db.commit()

    scopes = _loads(batch.scopes_json, []) or HEALTH_PROFILES.get(
        batch.profile, HEALTH_PROFILES["standard"],
    )
    endpoint_ids = _loads(batch.endpoint_ids_json, [])
    if not endpoint_ids:
        raise ValueError("至少选择一个模型端点")

    user_instruction = batch.user_instruction
    tasks: list[tuple[str, int]] = [
        (scope, eid) for scope in scopes for eid in endpoint_ids
    ]

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(tasks)))) as pool:
        futures = {
            pool.submit(_single_run, batch.id, scope, eid, user_instruction): (scope, eid)
            for scope, eid in tasks
        }
        for fut in as_completed(futures):
            scope, eid = futures[fut]
            try:
                _, _, status = fut.result()
                if status != "ok":
                    failures.append(f"scope={scope}, endpoint={eid}: {status}")
            except Exception as e:  # noqa: BLE001
                failures.append(f"scope={scope}, endpoint={eid}: {e}")

    build_consensus(db, batch.id)
    batch.status = "completed_with_errors" if failures else "completed"
    batch.error_message = "\n".join(failures)[:10000]
    batch.finished_at = now_iso()
    db.commit()
    return batch


def build_consensus(db: Session, batch_id: int) -> AIConsensusReport:
    """本地确定性共识汇总，不依赖再次调用模型。"""
    batch = db.get(AIReviewBatch, batch_id)
    jobs = db.execute(
        select(AIReviewJob)
        .where(AIReviewJob.batch_id == batch_id)
        .order_by(AIReviewJob.id)
    ).scalars().all()

    completed: list[tuple[AIReviewJob, AIReviewResult]] = []
    endpoint_names = {
        x.id: x.name for x in
        db.execute(select(AIModelEndpoint)).scalars().all()
    }
    for j in jobs:
        r = db.scalar(select(AIReviewResult).where(AIReviewResult.job_id == j.id))
        if r:
            completed.append((j, r))

    finding_groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    recs: dict[str, dict] = {}
    gaps: list[str] = []
    scores: list[float] = []
    risk = "UNKNOWN"

    for j, r in completed:
        scores.append(float(r.score))
        if RISK_ORDER.get(r.risk_level, 0) > RISK_ORDER.get(risk, 0):
            risk = r.risk_level
        for f in _loads(r.findings_json, []):
            key = (j.scope, _norm(f.get("area")), _norm(f.get("issue")))
            item = dict(f)
            item["scope"] = j.scope
            item["job_id"] = j.id
            item["model"] = endpoint_names.get(j.endpoint_id, str(j.endpoint_id))
            finding_groups[key].append(item)
        for rec in _loads(r.recommendations_json, []):
            key = _norm(rec.get("action"))
            if key and key not in recs:
                item = dict(rec)
                item["scope"] = j.scope
                item["source_job_id"] = j.id
                recs[key] = item
        for g in _loads(r.data_gaps_json, []):
            if g not in gaps:
                gaps.append(g)

    common: list[dict] = []
    differences: list[dict] = []
    selected_endpoint_count = (
        len(set(_loads(batch.endpoint_ids_json, []))) if batch else 1
    )
    for _key, items in finding_groups.items():
        models = sorted({x["model"] for x in items})
        if selected_endpoint_count > 1 and len(models) >= 2 or selected_endpoint_count == 1 and len(items) >= 1:
            base = dict(items[0])
            base["confirmed_by"] = models
            base["count"] = len(items)
            common.append(base)
        else:
            differences.append({
                "scope": _key[0], "issue": items[0].get("issue", ""),
                "opinions": items,
            })

    avg = (sum(scores) / len(scores)) if scores else 0
    failed = sum(1 for j in jobs if j.status == "failed")
    summary = (
        f"完成 {len(completed)}/{len(jobs)} 次AI子检查；形成 {len(common)} 项共识/确定发现，"
        f"{len(differences)} 项模型差异。"
    )
    if failed:
        summary += f" 另有 {failed} 次模型调用失败。"

    old = db.scalar(
        select(AIConsensusReport).where(AIConsensusReport.batch_id == batch_id)
    )
    if old:
        db.delete(old); db.flush()

    report = AIConsensusReport(
        batch_id=batch_id,
        overall_risk=risk,
        score=avg,
        summary=summary,
        common_findings_json=json.dumps(common, ensure_ascii=False),
        differences_json=json.dumps(differences, ensure_ascii=False),
        recommendations_json=json.dumps(list(recs.values()), ensure_ascii=False),
        data_gaps_json=json.dumps(gaps, ensure_ascii=False),
    )
    db.add(report); db.commit()
    return report


def recheck_task(
    db: Session, task: RemediationTask, endpoint_id: int,
) -> AIReviewJob:
    """整改后复检：新建 AIReviewJob，保留 parent_job_id。"""
    instruction = (
        f"这是整改后的复检。整改任务：{task.title}。"
        f"整改说明：{task.description}。"
        f"请重点判断原问题是否得到改善，并指出仍需补充的证据。"
    )
    job = AIReviewJob(
        project_id=task.project_id,
        scope=task.scope or "whole_project",
        endpoint_id=endpoint_id,
        parent_job_id=task.source_job_id,
        user_instruction=instruction,
        status="pending",
        created_at=now_iso(),
        actor=task.actor,
    )
    db.add(job); db.commit()
    run_review(db, job)
    task.recheck_job_id = job.id
    task.status = "rechecked"
    task.updated_at = now_iso()
    db.commit()
    return job


__all__ = ["run_health_check", "build_consensus", "recheck_task"]