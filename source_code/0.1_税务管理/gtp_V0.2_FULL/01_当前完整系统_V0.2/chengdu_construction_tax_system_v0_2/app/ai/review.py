"""V0.2: 单次 AI 检查编排。"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from sqlalchemy.orm import Session

from ..constants import INPUT_PREVIEW_MAX, RAW_RESPONSE_MAX
from ..models import (
    AIPromptTemplate, AIReviewJob, AIReviewResult, AIModelEndpoint,
)
from ..sanitize import sanitize_context
from .adapter import call_endpoint
from .context import build_context
from .prompt import build_messages, get_prompt_template
from .timeutil import now_iso


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
        result = AIReviewResult(
            job_id=job.id,
            provider_name=endpoint.name,
            model_name=endpoint.model or endpoint.adapter,
            risk_level=str(parsed.get("risk_level", "UNKNOWN")),
            score=Decimal(str(parsed.get("score", 0) or 0)),
            summary=str(parsed.get("summary", "")),
            findings_json=json.dumps(parsed.get("findings", []), ensure_ascii=False),
            recommendations_json=json.dumps(
                parsed.get("recommendations", []), ensure_ascii=False,
            ),
            data_gaps_json=json.dumps(
                parsed.get("data_gaps", []), ensure_ascii=False,
            ),
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
    try:
        parsed, raw, parse_failed = call_endpoint(endpoint, messages, safe_ctx)
        result = AIReviewResult(
            job_id=job.id,
            provider_name=endpoint.name,
            model_name=endpoint.model or endpoint.adapter,
            risk_level=str(parsed.get("risk_level", "UNKNOWN")),
            score=Decimal(str(parsed.get("score", 0) or 0)),
            summary=str(parsed.get("summary", "")),
            findings_json=json.dumps(parsed.get("findings", []), ensure_ascii=False),
            recommendations_json=json.dumps(
                parsed.get("recommendations", []), ensure_ascii=False,
            ),
            data_gaps_json=json.dumps(
                parsed.get("data_gaps", []), ensure_ascii=False,
            ),
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