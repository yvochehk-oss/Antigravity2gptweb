"""V0.2: 体检编排（线程池并行）+ 共识引擎 + 整改复检。"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..audit import audit
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

_TERMINAL_BATCH_STATUSES = {"completed", "completed_with_errors", "failed"}
_HEALTH_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, int(os.getenv("AI_HEALTH_WORKERS", "2"))),
    thread_name_prefix="tax-ai-health",
)
_HEALTH_FUTURES: dict[int, object] = {}
_HEALTH_FUTURES_LOCK = threading.Lock()
DEFAULT_HEALTH_ENDPOINT_DEADLINE_SECONDS = 120.0
DEFAULT_HEALTH_LEASE_SECONDS = 300.0


def _safe_rollback(db: Session) -> None:
    """Rollback without masking the original failure."""
    with suppress(Exception):
        db.rollback()


def _pg_advisory_xact_lock(db: Session, key: str) -> None:
    """Take a transaction-scoped PostgreSQL lock when available.

    V2 is PostgreSQL-only, but the dialect guard makes the orchestration
    helpers straightforward to exercise with lightweight SQLAlchemy fakes.
    Locks are deliberately transaction-scoped: the caller must hold them only
    for the short claim/upsert transaction, never while waiting on an LLM.
    """
    bind = db.get_bind()
    if getattr(getattr(bind, "dialect", None), "name", "") != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": key},
    )


def _loads(text: str | None, default: Any) -> Any:
    try:
        return json.loads(text or "")
    except Exception:
        return default


def _setting_seconds(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.1, float(raw))
    except (TypeError, ValueError):
        return default


def health_endpoint_deadline_seconds(endpoint: AIModelEndpoint | None) -> float:
    """Return the total health-call budget, including retries/backoff.

    The endpoint's configured timeout is never mutated.  Without an explicit
    health budget, retain at least one configured request timeout while
    bounding the retry loop to a single total budget.
    """
    endpoint_timeout = float(getattr(endpoint, "timeout_seconds", 0) or 0)
    default = max(DEFAULT_HEALTH_ENDPOINT_DEADLINE_SECONDS, endpoint_timeout)
    return _setting_seconds("AI_HEALTH_ENDPOINT_DEADLINE_SECONDS", default)


def _health_lease_seconds() -> float:
    return _setting_seconds(
        "AI_HEALTH_LEASE_SECONDS", DEFAULT_HEALTH_LEASE_SECONDS,
    )


def _parse_iso_timestamp(value: str | None) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _batch_request_key(
    project_id: int,
    profile: str,
    scopes: list[str],
    endpoint_ids: list[int],
    user_instruction: str,
) -> str:
    return json.dumps({
        "project_id": project_id,
        "profile": profile,
        "scopes": list(dict.fromkeys(str(x) for x in scopes)),
        "endpoint_ids": list(dict.fromkeys(int(x) for x in endpoint_ids)),
        "user_instruction": user_instruction or "",
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def claim_health_batch(
    db: Session,
    *,
    project_id: int,
    profile: str,
    scopes: list[str],
    endpoint_ids: list[int],
    user_instruction: str,
    actor: str,
) -> tuple[AIReviewBatch, bool]:
    """Create one pending batch or reuse an identical active submission."""
    normalized_scopes = list(dict.fromkeys(str(x) for x in scopes if str(x).strip()))
    normalized_endpoints = list(dict.fromkeys(int(x) for x in endpoint_ids))
    scopes_json = json.dumps(normalized_scopes, ensure_ascii=False)
    endpoints_json = json.dumps(normalized_endpoints, ensure_ascii=False)
    key = _batch_request_key(
        project_id, profile, normalized_scopes, normalized_endpoints,
        user_instruction or "",
    )
    _pg_advisory_xact_lock(db, f"ai-health-submit:{key}")
    existing = db.scalar(
        select(AIReviewBatch)
        .where(
            AIReviewBatch.project_id == project_id,
            AIReviewBatch.profile == profile,
            AIReviewBatch.scopes_json == scopes_json,
            AIReviewBatch.endpoint_ids_json == endpoints_json,
            AIReviewBatch.user_instruction == (user_instruction or ""),
            AIReviewBatch.status.in_(("pending", "running")),
        )
        .order_by(AIReviewBatch.id.desc())
    )
    if existing is not None:
        return existing, False
    batch = AIReviewBatch(
        project_id=project_id,
        profile=profile,
        scopes_json=scopes_json,
        endpoint_ids_json=endpoints_json,
        user_instruction=user_instruction or "",
        status="pending",
        created_at=now_iso(),
        actor=actor or "anonymous",
    )
    db.add(batch)
    db.flush()
    db.commit()
    return batch, True


def _mark_batch_worker_failure(batch_id: int, exc: BaseException) -> None:
    """Persist a worker crash/cancellation without leaving running state."""
    from ..db import SessionLocal

    error_text = str(exc).replace("\x00", " ").replace("\n", " ").strip()
    error_text = (error_text or exc.__class__.__name__)[:10000]
    sess = SessionLocal()
    try:
        _safe_rollback(sess)
        batch = sess.scalar(
            select(AIReviewBatch)
            .where(AIReviewBatch.id == batch_id)
            .with_for_update()
        )
        if batch is None or batch.status in _TERMINAL_BATCH_STATUSES:
            return
        jobs = sess.execute(
            select(AIReviewJob).where(AIReviewJob.batch_id == batch_id)
        ).scalars().all()
        for job in jobs:
            if job.status in {"pending", "running"}:
                job.status = "failed"
                job.error_message = f"AI体检 worker 异常：{error_text}"
                job.finished_at = now_iso()
        has_completed = any(job.status == "completed" for job in jobs)
        batch.status = "completed_with_errors" if has_completed else "failed"
        batch.error_message = f"AI体检 worker 异常：{error_text}"
        batch.finished_at = now_iso()
        audit(
            sess, "AI_HEALTH_WORKER_FAILED", "AIReviewBatch", batch.id,
            batch.error_message, actor=batch.actor,
        )
        sess.commit()
    except BaseException:
        _safe_rollback(sess)
    finally:
        sess.close()


def _run_health_check_background(batch_id: int) -> None:
    from ..db import SessionLocal

    sess = SessionLocal()
    try:
        batch = sess.get(AIReviewBatch, batch_id)
        if batch is None or batch.status in _TERMINAL_BATCH_STATUSES:
            return
        run_health_check(sess, batch)
    except BaseException as exc:  # cancellation must not strand a lease
        _mark_batch_worker_failure(batch_id, exc)
    finally:
        sess.close()


def _forget_health_future(batch_id: int, future: object) -> None:
    with _HEALTH_FUTURES_LOCK:
        if _HEALTH_FUTURES.get(batch_id) is future:
            _HEALTH_FUTURES.pop(batch_id, None)


def enqueue_health_check(batch_id: int) -> bool:
    """Submit one batch to the bounded process-local health worker pool."""
    with _HEALTH_FUTURES_LOCK:
        current = _HEALTH_FUTURES.get(batch_id)
        if current is not None and not current.done():
            return False
        future = _HEALTH_EXECUTOR.submit(_run_health_check_background, batch_id)
        _HEALTH_FUTURES[batch_id] = future
        future.add_done_callback(
            lambda done: _forget_health_future(batch_id, done),
        )
    return True


def fail_health_batch(batch_id: int, reason: str) -> None:
    """Public bounded-queue failure hook for the HTTP submission path."""
    _mark_batch_worker_failure(batch_id, RuntimeError(reason))


def recover_stale_health_batches(
    db: Session,
    *,
    stale_after_seconds: float | None = None,
) -> int:
    """Reap pending/running batches whose worker lease has expired.

    This is intentionally safe to call from each request: row locks make
    recovery idempotent, and a late worker cannot overwrite a terminal batch
    during finalization.
    """
    lease_seconds = (
        max(0.1, float(stale_after_seconds))
        if stale_after_seconds is not None else _health_lease_seconds()
    )
    now = time.time()
    candidates = db.execute(
        select(AIReviewBatch).where(
            AIReviewBatch.status.in_(("pending", "running")),
        )
    ).scalars().all()
    recovered = 0
    try:
        for candidate in candidates:
            created_at = _parse_iso_timestamp(candidate.created_at)
            if created_at is not None and now - created_at <= lease_seconds:
                continue
            batch = db.scalar(
                select(AIReviewBatch)
                .where(AIReviewBatch.id == candidate.id)
                .with_for_update()
            )
            if batch is None or batch.status not in {"pending", "running"}:
                continue
            jobs = db.execute(
                select(AIReviewJob).where(AIReviewJob.batch_id == batch.id)
            ).scalars().all()
            for job in jobs:
                if job.status in {"pending", "running"}:
                    job.status = "failed"
                    job.error_message = "AI体检 worker lease 已过期，未完成的子任务需人工复核"
                    job.finished_at = now_iso()
            completed = any(job.status == "completed" for job in jobs)
            batch.status = "completed_with_errors" if completed else "failed"
            batch.error_message = "AI体检 worker lease 已过期，批次已回收"
            batch.finished_at = now_iso()
            audit(
                db, "AI_HEALTH_STALE_RECOVERED", "AIReviewBatch", batch.id,
                batch.error_message, actor=batch.actor,
            )
            recovered += 1
        if recovered:
            db.commit()
    except BaseException:
        _safe_rollback(db)
        raise
    return recovered


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())[:180]


def _claim_review_job(
    sess: Session,
    batch_id: int,
    scope: str,
    endpoint_id: int,
    user_instruction: str,
) -> tuple[AIReviewJob | None, str | None]:
    """Claim one ``(batch, scope, endpoint)`` review exactly once.

    There is intentionally no new model constraint in this worker-owned
    change.  The advisory lock closes the race around the existing-job check
    and insert.  A second trigger observes ``pending``/``running`` and exits
    without starting a duplicate model call; a completed job is reused.
    """
    _pg_advisory_xact_lock(
        sess, f"ai-review-job:{batch_id}:{scope}:{endpoint_id}"
    )
    batch = sess.get(AIReviewBatch, batch_id)
    if not batch:
        return None, "failed:batch not found"

    existing = sess.scalar(
        select(AIReviewJob)
        .where(
            AIReviewJob.batch_id == batch_id,
            AIReviewJob.scope == scope,
            AIReviewJob.endpoint_id == endpoint_id,
        )
        .order_by(AIReviewJob.id)
    )
    if existing is not None:
        if existing.status == "completed":
            result = sess.scalar(
                select(AIReviewResult).where(AIReviewResult.job_id == existing.id)
            )
            if result is not None:
                return existing, "ok"
            existing.error_message = "已完成但缺少 result"
            existing.parse_failed = True
            sess.commit()
            return existing, "failed:completed job has no result"
        if existing.status in {"pending", "running"}:
            return existing, "in_progress"
        # A failed child is retained as an audit record.  Do not silently
        # create a second job for the same trigger; an explicit new batch is
        # the retry boundary.
        return existing, f"failed:{existing.error_message or 'review job failed'}"

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
    sess.add(job)
    sess.commit()
    return job, None


def _single_run(batch_id: int, scope: str, endpoint_id: int,
                user_instruction: str) -> tuple[str, int, str]:
    """线程池 worker：在独立 session 中执行 run_review。"""
    from ..db import SessionLocal  # 避免循环

    sess = SessionLocal()
    job: AIReviewJob | None = None
    try:
        try:
            job, claim_status = _claim_review_job(
                sess, batch_id, scope, endpoint_id, user_instruction,
            )
        except Exception as exc:  # noqa: BLE001
            _safe_rollback(sess)
            return scope, endpoint_id, f"failed:{exc}"
        if claim_status is not None:
            return scope, endpoint_id, claim_status

        try:
            if job is None:
                raise RuntimeError("Review job instance cannot be None in claim worker")
            endpoint = sess.get(AIModelEndpoint, endpoint_id)
            deadline = time.monotonic() + health_endpoint_deadline_seconds(endpoint)
            run_review(sess, job, deadline=deadline)
            return scope, endpoint_id, "ok"
        except Exception as e:  # noqa: BLE001
            # ``run_review`` normally records its own failure after rollback.
            # This outer guard covers failures before that handler can do so
            # and guarantees the child is not left in ``running`` forever.
            _safe_rollback(sess)
            if job is not None and job.id is not None:
                failed_job = sess.get(AIReviewJob, job.id)
                if failed_job is not None and failed_job.status != "completed":
                    failed_job.status = "failed"
                    failed_job.error_message = str(e)
                    failed_job.finished_at = now_iso()
                    try:
                        sess.commit()
                    except Exception:  # noqa: BLE001
                        _safe_rollback(sess)
            return scope, endpoint_id, f"failed:{e}"
    finally:
        sess.close()


def run_health_check(db: Session, batch: AIReviewBatch) -> AIReviewBatch:
    """多 scope × 多模型 并行执行（V0.2）。"""
    # Lock and claim the batch first.  This makes a repeated request or a
    # duplicate browser submission a no-op instead of spawning another full
    # scope × model matrix.
    try:
        locked_batch = db.scalar(
            select(AIReviewBatch)
            .where(AIReviewBatch.id == batch.id)
            .with_for_update()
        )
        if locked_batch is None:
            raise ValueError("AI体检批次不存在")
        if locked_batch.status in _TERMINAL_BATCH_STATUSES:
            return locked_batch
        if locked_batch.status == "running":
            return locked_batch

        scopes = _loads(locked_batch.scopes_json, []) or HEALTH_PROFILES.get(
            locked_batch.profile, HEALTH_PROFILES["standard"],
        )
        endpoint_ids = _loads(locked_batch.endpoint_ids_json, [])
        if not endpoint_ids:
            locked_batch.status = "failed"
            locked_batch.error_message = "至少选择一个模型端点"
            locked_batch.finished_at = now_iso()
            db.commit()
            raise ValueError("至少选择一个模型端点")
        try:
            scopes = list(
                dict.fromkeys(str(scope) for scope in scopes if str(scope).strip())
            )
            endpoint_ids = list(dict.fromkeys(int(eid) for eid in endpoint_ids))
        except (TypeError, ValueError) as exc:
            locked_batch.status = "failed"
            locked_batch.error_message = f"模型端点参数非法：{exc}"
            locked_batch.finished_at = now_iso()
            db.commit()
            raise ValueError("模型端点参数非法") from exc
        if not scopes:
            locked_batch.status = "failed"
            locked_batch.error_message = "至少选择一个体检范围"
            locked_batch.finished_at = now_iso()
            db.commit()
            raise ValueError("至少选择一个体检范围")
        locked_batch.status = "running"
        db.commit()
    except Exception:
        _safe_rollback(db)
        raise

    user_instruction = locked_batch.user_instruction
    tasks: list[tuple[str, int]] = [
        (scope, eid) for scope in scopes for eid in endpoint_ids
    ]

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(tasks)))) as pool:
        futures = {
            pool.submit(_single_run, locked_batch.id, scope, eid, user_instruction): (scope, eid)
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

    try:
        build_consensus(db, locked_batch.id)
        final_batch = db.scalar(
            select(AIReviewBatch)
            .where(AIReviewBatch.id == locked_batch.id)
            .with_for_update()
        )
        if final_batch is None:
            raise ValueError("AI体检批次在执行期间丢失")
        if final_batch.status != "running":
            # A stale-lease reaper or an explicit cancellation won the race;
            # a late worker must never resurrect a terminal batch.
            db.commit()
            return final_batch
        final_batch.status = "completed_with_errors" if failures else "completed"
        final_batch.error_message = "\n".join(failures)[:10000]
        final_batch.finished_at = now_iso()
        db.commit()
        return final_batch
    except Exception:
        _safe_rollback(db)
        # Preserve the failure state in a clean transaction where possible;
        # the route also has a fresh-session fallback for a broken connection.
        try:
            failed_batch = db.scalar(
                select(AIReviewBatch)
                .where(AIReviewBatch.id == locked_batch.id)
                .with_for_update()
            )
            if failed_batch is not None and failed_batch.status not in _TERMINAL_BATCH_STATUSES:
                failed_batch.status = "failed"
                failed_batch.error_message = "AI体检编排失败"
                failed_batch.finished_at = now_iso()
                db.commit()
        except Exception:  # noqa: BLE001
            _safe_rollback(db)
        raise


def _build_consensus(db: Session, batch_id: int) -> AIConsensusReport:
    """本地确定性共识汇总，不依赖再次调用模型。"""
    try:
        _pg_advisory_xact_lock(db, f"ai-consensus:{batch_id}")
        batch = db.scalar(
            select(AIReviewBatch)
            .where(AIReviewBatch.id == batch_id)
            .with_for_update()
        )
        jobs = db.execute(
            select(AIReviewJob)
            .where(AIReviewJob.batch_id == batch_id)
            .order_by(AIReviewJob.id)
        ).scalars().all()
    except Exception:
        _safe_rollback(db)
        raise


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
        if (
            (selected_endpoint_count > 1 and len(models) >= 2)
            or (selected_endpoint_count == 1 and len(items) >= 1)
        ):
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

    try:
        old = db.scalar(
            select(AIConsensusReport)
            .where(AIConsensusReport.batch_id == batch_id)
            .with_for_update()
        )
        values = {
            "overall_risk": risk,
            "score": avg,
            "summary": summary,
            "common_findings_json": json.dumps(common, ensure_ascii=False),
            "differences_json": json.dumps(differences, ensure_ascii=False),
            "recommendations_json": json.dumps(list(recs.values()), ensure_ascii=False),
            "data_gaps_json": json.dumps(gaps, ensure_ascii=False),
        }
        if old is None:
            report = AIConsensusReport(batch_id=batch_id, **values)
            db.add(report)
        else:
            # Update in place so repeated calls are idempotent and never race
            # the unique ``batch_id`` constraint with delete+insert.
            report = old
            for key, value in values.items():
                setattr(report, key, value)
        db.commit()
        return report
    except Exception:
        _safe_rollback(db)
        raise


def build_consensus(db: Session, batch_id: int) -> AIConsensusReport:
    """Build/update the deterministic report with session cleanup on error."""
    try:
        return _build_consensus(db, batch_id)
    except Exception:
        _safe_rollback(db)
        raise


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
    db.add(job)
    db.commit()
    run_review(db, job)
    task.recheck_job_id = job.id
    task.status = "rechecked"
    task.updated_at = now_iso()
    db.commit()
    return job


__all__ = [
    "run_health_check",
    "build_consensus",
    "recheck_task",
    "claim_health_batch",
    "enqueue_health_check",
    "fail_health_batch",
    "recover_stale_health_batches",
    "health_endpoint_deadline_seconds",
]
