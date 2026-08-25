"""V0.3: AI 检查编排（含 Schema 校验）。"""
from __future__ import annotations

import hashlib
import json
from contextlib import suppress
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import INPUT_PREVIEW_MAX, RAW_RESPONSE_MAX
from ..models import (
    AIModelEndpoint,
    AIReviewJob,
    AIReviewResult,
)
from ..sanitize import sanitize_context
from ..schemas import AIReviewPayload
from .adapter import ensure_endpoint_allowed, is_mock_endpoint, mock_endpoint_allowed
from .context import build_context
from .failover import call_with_failover
from .prompt import build_messages, get_prompt_template
from .timeutil import now_iso


def _clamp_score(value: float | None) -> float:
    """截断分数到 0-100 范围。"""
    if value is None:
        return 0.0
    if value < 0:
        return 0.0
    if value > 100:
        return 100.0
    return float(value)


def _failure_message(exc: Exception) -> str:
    """Bound model failure text before persisting it in the audit record."""
    text = str(exc).replace("\x00", " ").replace("\n", " ").strip()
    return text[:2000] or exc.__class__.__name__


def _record_unavailable_result(
    db: Session,
    job: AIReviewJob,
    endpoint: AIModelEndpoint | None,
    exc: Exception,
) -> AIReviewResult:
    """Persist an explicit non-AI result for an unavailable endpoint.

    A failed job remains failed for orchestration/audit purposes, while the
    result row gives API consumers a stable ``UNKNOWN``/manual-review payload.
    It is intentionally not a mock conclusion and contains no raw response or
    credentials.
    """
    reason = _failure_message(exc)
    status = getattr(exc, "status", "DEGRADED")
    data_gaps = [
        f"AI状态={status}",
        f"真实 AI 端点不可用：{reason}",
    ]
    findings = [{
        # ``Finding`` does not accept UNKNOWN, so HIGH is used only as the
        # operational severity of an unavailable AI result; risk_level stays
        # UNKNOWN and the finding explicitly requires manual review.
        "severity": "HIGH",
        "area": "AI端点可用性",
        "issue": "未生成真实模型审查结论",
        "evidence": reason,
        "impact": "不得依据此结果作出财税判断",
        "requires_manual_review": True,
    }]
    db.rollback()
    persisted_job = db.get(AIReviewJob, job.id) if job.id is not None else job
    if persisted_job is None:  # pragma: no cover - protects an invalid caller
        raise exc
    persisted_job.status = "failed"
    persisted_job.error_message = reason
    persisted_job.parse_failed = True
    persisted_job.finished_at = now_iso()
    result = db.scalar(
        select(AIReviewResult).where(AIReviewResult.job_id == persisted_job.id),
    )
    values = {
        "provider_name": endpoint.name if endpoint is not None else "",
        "model_name": (
            (endpoint.model or endpoint.adapter)
            if endpoint is not None else ""
        ),
        "risk_level": "UNKNOWN",
        "score": Decimal("0"),
        "summary": "真实 AI 端点不可用，未生成模拟结论；请人工复核。",
        "findings_json": json.dumps(findings, ensure_ascii=False),
        "recommendations_json": "[]",
        "data_gaps_json": json.dumps(data_gaps, ensure_ascii=False),
        "raw_response": "",
    }
    if result is None:
        result = AIReviewResult(job_id=persisted_job.id, **values)
        db.add(result)
    else:
        for key, value in values.items():
            setattr(result, key, value)
    db.commit()
    return result


def run_review(
    db: Session,
    job: AIReviewJob,
    *,
    deadline: float | None = None,
) -> AIReviewResult:
    endpoint = db.get(AIModelEndpoint, job.endpoint_id)
    try:
        ensure_endpoint_allowed(endpoint)
        job.status = "running"
        job.started_at = now_iso()
        db.commit()

        ctx = build_context(db, job.project_id, job.scope)
        safe_ctx = sanitize_context(ctx)
        serialized = json.dumps(
            safe_ctx, ensure_ascii=False, sort_keys=True, default=str,
        )
        job.input_digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        job.input_preview = serialized[:INPUT_PREVIEW_MAX]

        template = get_prompt_template(db, job.scope, job.prompt_template_id)
        if template and not job.prompt_template_id:
            job.prompt_template_id = template.id

        messages = build_messages(safe_ctx, job.user_instruction, template)
        failover = call_with_failover(
            db,
            messages,
            safe_ctx,
            endpoint_id=job.endpoint_id,
            deadline=deadline,
            # Legacy mock fixtures remain available only in an explicitly
            # opted-in test process.  They are never a production candidate.
            allow_test_mock=(
                is_mock_endpoint(endpoint) and mock_endpoint_allowed()
            ),
        )
        parsed, raw, parse_failed, route_meta = failover

        validated_payload: AIReviewPayload | None = None
        try:
            validated_payload = AIReviewPayload.model_validate(parsed)
        except ValidationError as e:
            parse_failed = True
            gaps = parsed.get("data_gaps", [])
            if not isinstance(gaps, list):
                gaps = []
            gaps.append(f"AI 输出 Schema 校验失败: {e}")
            parsed["data_gaps"] = gaps
            parsed["risk_level"] = "UNKNOWN"
            parsed["notes"] = f"AI output validation failed: {e}"
            with suppress(ValidationError):
                validated_payload = AIReviewPayload.model_validate(parsed)

        risk_level = validated_payload.risk_level if validated_payload else parsed.get("risk_level", "UNKNOWN")
        score_value = validated_payload.score if validated_payload else _clamp_score(parsed.get("score"))
        summary = validated_payload.summary if validated_payload else str(parsed.get("summary", ""))
        findings = [f.model_dump() if hasattr(f, "model_dump") else f for f in (validated_payload.findings if validated_payload else parsed.get("findings", []))]
        recommendations = [r.model_dump() if hasattr(r, "model_dump") else r for r in (validated_payload.recommendations if validated_payload else parsed.get("recommendations", []))]
        data_gaps = list(
            validated_payload.data_gaps
            if validated_payload else parsed.get("data_gaps", [])
        )
        if route_meta.get("fallback_used"):
            data_gaps.append(
                "首选 AI 端点失败，已切换同路由组后备端点；本结果状态为 DEGRADED",
            )

        result = AIReviewResult(
            job_id=job.id,
            provider_name=endpoint.name,
            model_name=endpoint.model or endpoint.adapter,
            risk_level=str(risk_level),
            score=Decimal(str(score_value)),
            summary=str(summary),
            findings_json=json.dumps(findings, ensure_ascii=False, default=str),
            recommendations_json=json.dumps(recommendations, ensure_ascii=False, default=str),
            data_gaps_json=json.dumps(data_gaps, ensure_ascii=False, default=str),
            raw_response=raw[:RAW_RESPONSE_MAX],
        )
        db.add(result)
        job.status = "completed"
        # A fallback response is usable but not equivalent to a healthy
        # primary route.  Reuse the existing persisted flag so old API
        # consumers surface this as DEGRADED without a schema change.
        job.parse_failed = bool(parse_failed or route_meta.get("fallback_used"))
        job.finished_at = now_iso()
        db.commit()
        return result
    except Exception as e:
        _record_unavailable_result(db, job, endpoint, e)
        raise


__all__ = ["run_review", "now_iso"]
