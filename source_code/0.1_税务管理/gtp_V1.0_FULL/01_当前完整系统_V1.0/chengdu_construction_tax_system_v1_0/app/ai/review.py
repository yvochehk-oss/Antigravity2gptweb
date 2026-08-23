"""V0.3: AI 检查编排（含 Schema 校验）。"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..constants import INPUT_PREVIEW_MAX, RAW_RESPONSE_MAX
from ..models import (
    AIModelEndpoint,
    AIReviewJob,
    AIReviewResult,
)
from ..sanitize import sanitize_context
from ..schemas import AIReviewPayload
from .adapter import call_endpoint
from .context import build_context
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


def run_review(db: Session, job: AIReviewJob) -> AIReviewResult:
    endpoint = db.get(AIModelEndpoint, job.endpoint_id)
    if not endpoint or not endpoint.enabled:
        raise RuntimeError("模型端点不存在或已禁用")

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
    try:
        parsed, raw, parse_failed = call_endpoint(endpoint, messages, safe_ctx)

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
            try:
                validated_payload = AIReviewPayload.model_validate(parsed)
            except ValidationError:
                pass

        risk_level = validated_payload.risk_level if validated_payload else parsed.get("risk_level", "UNKNOWN")
        score_value = validated_payload.score if validated_payload else _clamp_score(parsed.get("score"))
        summary = validated_payload.summary if validated_payload else str(parsed.get("summary", ""))
        findings = [f.model_dump() if hasattr(f, "model_dump") else f for f in (validated_payload.findings if validated_payload else parsed.get("findings", []))]
        recommendations = [r.model_dump() if hasattr(r, "model_dump") else r for r in (validated_payload.recommendations if validated_payload else parsed.get("recommendations", []))]
        data_gaps = validated_payload.data_gaps if validated_payload else parsed.get("data_gaps", [])

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
        job.parse_failed = parse_failed
        job.finished_at = now_iso()
        db.commit()
        return result
    except Exception as e:
        db.rollback()
        job.status = "failed"
        job.error_message = str(e)
        job.finished_at = now_iso()
        db.commit()
        raise


__all__ = ["run_review", "now_iso"]