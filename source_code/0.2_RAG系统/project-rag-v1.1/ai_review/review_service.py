"""
AI Review service.

AI Review is deliberately downstream of the canonical Facts contract:

* deterministic metrics are read from Facts and are never re-calculated or
  replaced by an LLM value;
* RAG supplies documentary evidence and citations, not structured truth;
* missing Facts, missing evidence, an unavailable model, or an ungrounded
  model response produce ``NEEDS_REVIEW``/``UNKNOWN`` rather than a demo
  score or a guessed risk level.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.entities import CANONICAL_ENTITY_RANGE_TEXT, is_canonical_entity_code
from app.security import validate_llm_outbound_url
from facts_provider.facts_provider import FactsProvider, entity_mapping_failure

from .evidence_pack_service import RAGEvidencePackService
from .rag_client import ProjectRAGClient
from .run_service import AIReviewRunService
from .snapshot_service import FactsSnapshotService

logger = logging.getLogger(__name__)

ALLOWED_RISK_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})
UNKNOWN_RISK = "UNKNOWN"
REVIEW_COMPLETED = "COMPLETED"
REVIEW_NEEDS_REVIEW = "NEEDS_REVIEW"

class AIReviewError(RuntimeError):
    """Base class for expected AI Review failures."""


class AIReviewUnavailableError(AIReviewError):
    """The configured model or documentary evidence is unavailable."""


# Preserve the historical import path used by callers and integrations.
AIReviewUnavailable = AIReviewUnavailableError


class AIReviewOutputError(AIReviewError):
    """The model did not return the required grounded JSON contract."""


def _is_canonical_entity_code(value: Any) -> bool:
    return is_canonical_entity_code(str(value or "").strip().upper())


def _json_object(value: Any) -> dict:
    """Return a JSON object, including compatibility with legacy rows."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}


def _number(value: Any) -> int | float | None:
    """Convert a JSON-safe metric value without inventing a zero."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_payload(metric: Any) -> dict:
    """Serialize one MetricValue without relying on its broken ``to_dict``."""
    if isinstance(metric, Mapping):
        value = metric.get("value")
        version = metric.get("metric_version")
        unit = metric.get("unit", "")
    else:
        if not hasattr(metric, "value"):
            raise AIReviewError("Canonical Facts contains a metric without a value")
        value = getattr(metric, "value")
        version = getattr(metric, "metric_version", None)
        unit = getattr(metric, "unit", "")
    return {
        "value": _number(value),
        "metric_version": str(version) if version is not None else "",
        "unit": str(unit or ""),
    }


def _serialize_facts(facts: Any) -> dict:
    """Adapt the Facts Provider response to the canonical JSON contract.

    ``FactsResponse.to_dict`` in old deployments attempted to access
    ``.value`` after ``dataclasses.asdict`` had already converted metrics to
    dictionaries.  AI Review therefore serializes the response explicitly so
    that it cannot accidentally fall back to demo values.

    Preserves availability metadata so downstream code can detect degraded
    or unavailable Facts and avoid returning COMPLETED without real data.
    """
    if isinstance(facts, Mapping):
        project_code = facts.get("project_code")
        as_of = facts.get("as_of")
        facts_version = facts.get("facts_version")
        raw_metrics = facts.get("metrics")
        # A few pre-canonical in-process callers supplied the core Facts
        # fields but did not yet expose the availability flags.  Presence of
        # metrics is the only compatibility inference allowed here; an
        # explicitly supplied false value always wins and remains degraded.
        facts_available = facts.get("facts_available", bool(raw_metrics))
        status = facts.get(
            "status",
            "AVAILABLE" if facts_available else "UNKNOWN",
        )
        reason = facts.get("reason")
        source = facts.get("source", "")
        entity_fields = {
            key: facts.get(key)
            for key in (
                "entity_code",
                "business_role",
                "entity_mapping_status",
                "entity_mapping_reason",
                "entity_mapping_valid",
            )
            if facts.get(key) is not None
        }
    else:
        project_code = getattr(facts, "project_code", None)
        as_of = getattr(facts, "as_of", None)
        facts_version = getattr(facts, "facts_version", None)
        raw_metrics = getattr(facts, "metrics", None)
        facts_available = getattr(facts, "facts_available", None)
        if facts_available is None:
            facts_available = bool(raw_metrics)
        status = getattr(
            facts,
            "status",
            "AVAILABLE" if facts_available else "UNKNOWN",
        )
        reason = getattr(facts, "reason", None)
        source = getattr(facts, "source", "")
        entity_fields = {
            key: getattr(facts, key)
            for key in (
                "entity_code",
                "business_role",
                "entity_mapping_status",
                "entity_mapping_reason",
                "entity_mapping_valid",
            )
            if getattr(facts, key, None) is not None
        }

    # Detect unavailable Facts before building the payload
    if not facts_available or status in ("DEGRADED", "UNAVAILABLE"):
        return {
            "project_code": str(project_code) if project_code else "",
            "as_of": str(as_of) if as_of else "",
            "facts_version": str(facts_version) if facts_version else "",
            "status": status,
            "facts_available": facts_available,
            "reason": reason or "facts_unavailable",
            "source": source,
            **entity_fields,
            "metrics": {},
        }

    if not str(project_code or "").strip():
        raise AIReviewError("Canonical Facts is missing project_code")
    if not str(as_of or "").strip():
        raise AIReviewError("Canonical Facts is missing as_of")
    if not str(facts_version or "").strip():
        raise AIReviewError("Canonical Facts is missing facts_version")
    if not isinstance(raw_metrics, Mapping):
        raise AIReviewError("Canonical Facts is missing metrics")

    metrics = {str(key): _metric_payload(value) for key, value in raw_metrics.items()}
    payload = {
        "project_code": str(project_code),
        "as_of": str(as_of),
        "facts_version": str(facts_version),
        "status": status,
        "facts_available": facts_available,
        "reason": reason,
        "source": source,
        "metrics": metrics,
    }

    # Entity identity and business role are independent dimensions.  Copy the
    # producer's explicit mapping metadata; never infer it from names, tax IDs,
    # or business roles.
    payload.update(entity_fields)

    entity_code = payload.get("entity_code")
    if entity_code:
        if not _is_canonical_entity_code(entity_code):
            # Leave the raw value available in the diagnostic payload, but
            # make the result explicitly unavailable below.  AI Review must
            # not turn an illegal entity label into a guessed canonical code.
            pass
        else:
            payload["entity_code"] = str(entity_code).strip().upper()
    mapping_failure = entity_mapping_failure(payload)
    if mapping_failure:
        # Keep the serializer fail-closed for custom/in-process Facts
        # producers as well as the SQL-backed provider.  The caller will
        # return NEEDS_REVIEW without snapshotting or invoking the LLM.
        payload.update(
            {
                "status": "DEGRADED",
                "facts_available": False,
                "reason": mapping_failure,
                "metrics": {},
            }
        )
    return payload


def _metric_value(facts_payload: Mapping, key: str) -> Any:
    metric = facts_payload.get("metrics", {}).get(key)
    return metric.get("value") if isinstance(metric, Mapping) else None


def _format_value(value: Any, unit: str = "") -> str:
    if value is None:
        return "UNKNOWN"
    if unit == "percent":
        try:
            return f"{float(value):.2%}"
        except (TypeError, ValueError):
            return "UNKNOWN"
    if unit == "CNY":
        try:
            return f"{float(value):,.0f} 元"
        except (TypeError, ValueError):
            return "UNKNOWN"
    return str(value)


def _evidence_manifest(evidence: list[dict]) -> list[dict]:
    """Return citation metadata without allowing documentary text to become Facts."""
    manifest: list[dict] = []
    for ev in evidence:
        source = ev.get("source") or ev.get("filename") or "UNKNOWN"
        page = ev.get("page") or "UNKNOWN"
        evidence_id = ev.get("evidence_id") or f"source:{source}:page:{page}"
        manifest.append({
            "evidence_id": str(evidence_id),
            "filename": ev.get("filename"),
            "page": page,
            "source": source,
            "version": ev.get("version"),
            "effective_from": ev.get("effective_from"),
            "effective_to": ev.get("effective_to"),
            "entity_code": ev.get("entity_code"),
            "business_role": ev.get("business_role"),
            "score": ev.get("score"),
        })
    return manifest


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _current_evidence(evidence: list[dict], as_of: str) -> list[dict]:
    """Drop documentary sources outside their declared effective window."""
    as_of_date = _as_date(as_of)
    if as_of_date is None:
        return evidence
    current: list[dict] = []
    for item in evidence:
        starts = _as_date(item.get("effective_from"))
        ends = _as_date(item.get("effective_to"))
        if starts and starts > as_of_date:
            continue
        if ends and ends < as_of_date:
            continue
        current.append(item)
    return current


class AIReviewService:
    """Run an evidence-grounded review over one canonical project Facts set."""

    def __init__(self, db: Session, facts_provider: FactsProvider):
        self._db = db
        self._facts_provider = facts_provider
        self._snapshot_service = FactsSnapshotService(db)
        self._evidence_pack_service = RAGEvidencePackService(db)
        self._run_service = AIReviewRunService(db)
        self._last_rag_errors: list[str] = []

    def _resolve_project_id(self, project_code: str) -> int:
        """Resolve the real Project row; never use a sentinel project_id."""
        from app.models import Project

        project = self._db.execute(
            select(Project).where(Project.project_code == project_code)
        ).scalar_one_or_none()
        if project is None:
            raise ValueError(f"Project not found for project_code={project_code!r}")
        return int(project.id)

    def run_review(
        self,
        project_code: str,
        model: str = "gpt-4o",
        prompt_version: str = "1.0",
        require_fresh: bool = True,
    ) -> dict:
        """Execute a review and return an explicit completion/needs-review result."""
        project_id = self._resolve_project_id(project_code)

        facts = self._facts_provider.get_facts(
            project_code=project_code,
            require_fresh=require_fresh,
            max_age=60,
        )

        # Check Facts availability BEFORE attempting any review
        facts_available = getattr(facts, "facts_available", None)
        if facts_available is None:
            facts_available = bool(getattr(facts, "metrics", None))
        facts_status = getattr(
            facts,
            "status",
            "AVAILABLE" if facts_available else "UNKNOWN",
        )
        facts_reason = getattr(facts, "reason", None)
        if not facts_available or facts_status in ("DEGRADED", "UNAVAILABLE"):
            return {
                "run_id": None,
                "status": REVIEW_NEEDS_REVIEW,
                "result": {
                    "review_status": REVIEW_NEEDS_REVIEW,
                    "needs_review": True,
                    "summary": f"Facts 不可用：{facts_reason or 'unknown'}",
                    "risk_level": UNKNOWN_RISK,
                    "confidence": "UNKNOWN",
                    "health_score": None,
                    "health_score_source": "unavailable",
                    "findings": [],
                    "metric_explanations": {},
                    "evidence_refs": [],
                    "facts_used": False,
                    "facts_status": facts_status,
                    "facts_available": False,
                    "facts_reason": facts_reason,
                },
                "facts_snapshot_id": None,
                "as_of": getattr(facts, "as_of", ""),
            }

        facts_payload = _serialize_facts(facts)
        if not facts_payload.get("facts_available") or facts_payload.get("status") in (
            "DEGRADED",
            "UNAVAILABLE",
        ):
            return {
                "run_id": None,
                "status": REVIEW_NEEDS_REVIEW,
                "result": {
                    "review_status": REVIEW_NEEDS_REVIEW,
                    "needs_review": True,
                    "summary": f"Facts 不可用：{facts_payload.get('reason') or 'unknown'}",
                    "risk_level": UNKNOWN_RISK,
                    "confidence": "UNKNOWN",
                    "health_score": None,
                    "health_score_source": "unavailable",
                    "findings": [],
                    "metric_explanations": {},
                    "evidence_refs": [],
                    "facts_used": False,
                    "facts_status": facts_payload.get("status", "DEGRADED"),
                    "facts_available": False,
                    "facts_reason": facts_payload.get("reason"),
                },
                "facts_snapshot_id": None,
                "as_of": facts_payload.get("as_of", ""),
            }
        if facts_payload["project_code"] != project_code:
            raise AIReviewError(
                "Canonical Facts project_code does not match the requested project"
            )

        snapshot = self._snapshot_service.create_snapshot(
            project_id=project_id,
            project_code=project_code,
            facts_data=facts_payload,
            as_of=facts_payload["as_of"],
            facts_version=facts_payload["facts_version"],
            analytics_contract_version="1.0",
            created_by="ai_review",
        )
        run = None
        try:
            rag_evidence = _current_evidence(
                self._get_rag_evidence(project_code),
                facts_payload["as_of"],
            )
            evidence_manifest = _evidence_manifest(rag_evidence)

            pack_status = "DEGRADED" if self._last_rag_errors else None
            evidence_pack = self._evidence_pack_service.create_pack(
                project_id=project_id,
                project_code=project_code,
                query=f"AI Review documentary evidence: {project_code}",
                evidence=rag_evidence,
                status=pack_status,
                extra_metadata={
                    "facts_as_of": facts_payload["as_of"],
                    "evidence_manifest": evidence_manifest,
                    "retrieval_queries": self._rag_evidence_queries(project_code),
                    "retrieval_errors": list(self._last_rag_errors),
                },
                created_by="ai_review",
            )
            run = self._run_service.create_run(
                project_id=project_id,
                project_code=project_code,
                facts_snapshot_id=snapshot.id,
                model=model,
                prompt_version=prompt_version,
                rag_evidence_pack_id=evidence_pack.id,
                created_by="ai_review",
            )

            if not rag_evidence:
                result = self._needs_review_result(
                    facts_payload,
                    evidence_manifest,
                    "没有可核验的文档证据；不得生成确定性风险结论",
                )
            else:
                prompt = self._build_review_prompt(facts_payload, rag_evidence)
                try:
                    llm_output = self._call_llm(prompt, model)
                    parsed = self._parse_llm_result(
                        llm_output,
                        valid_evidence_ids={ev["evidence_id"] for ev in evidence_manifest},
                    )
                    canonical_score = _metric_value(facts_payload, "health_score")
                    result = {
                        **parsed,
                        "facts": facts_payload,
                        "evidence": evidence_manifest,
                        # The score is documentary/structured truth only.  An
                        # LLM-provided score is intentionally ignored.
                        "health_score": canonical_score,
                        "health_score_source": (
                            "canonical_facts" if canonical_score is not None else "unknown"
                        ),
                    }
                except AIReviewError as exc:
                    logger.warning("AI Review requires manual review: %s", exc)
                    result = self._needs_review_result(
                        facts_payload,
                        evidence_manifest,
                        str(exc),
                    )

            run_status = (
                REVIEW_NEEDS_REVIEW
                if result.get("review_status") == REVIEW_NEEDS_REVIEW
                else REVIEW_COMPLETED
            )
            self._run_service.complete_run(
                run_id=run.id,
                result=result,
                result_summary=result.get("summary", ""),
                risk_level=result.get("risk_level", UNKNOWN_RISK),
                run_status=run_status,
                metadata={
                    "facts_version": facts_payload["facts_version"],
                    "rag_evidence_count": len(rag_evidence),
                    "evidence_ids": [ev.get("evidence_id") for ev in evidence_manifest],
                },
            )
            return {
                "run_id": run.id,
                "status": run_status,
                "result": result,
                "facts_snapshot_id": snapshot.id,
                "as_of": facts_payload["as_of"],
            }
        except Exception as exc:
            # Unexpected persistence/integration failures remain failures; do
            # not disguise them as a successful review.
            if run is not None:
                self._run_service.fail_run(run.id, str(exc))
            raise

    @staticmethod
    def _rag_evidence_queries(project_code: str) -> list[str]:
        return [
            f"{project_code} 项目合同台账",
            f"{project_code} 项目结算与回款",
            f"{project_code} 项目成本与利润",
            f"{project_code} 项目税务与发票",
            f"{project_code} 项目进度与风险",
        ]

    def _get_rag_evidence(self, project_code: str) -> list[dict]:
        """Retrieve documentary evidence with stable citation IDs."""
        queries = self._rag_evidence_queries(project_code)
        client = ProjectRAGClient(project_code=project_code)
        merged: list[dict] = []
        seen_keys: set[str] = set()
        self._last_rag_errors = []
        for query in queries:
            try:
                evidence = client.retrieve_evidence(query, top_k=3)
            except Exception as exc:  # explicit needs-review path below
                self._last_rag_errors.append(str(exc))
                logger.warning("RAG evidence retrieval failed for AI Review: %s", exc)
                continue
            for ev in client.format_evidence(evidence):
                key = str(ev.get("evidence_id"))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                merged.append(ev)
            if len(merged) >= 10:
                break
        return merged[:10]

    def _build_review_prompt(
        self,
        facts: Mapping | Any,
        rag_evidence: list[dict],
    ) -> str:
        """Build a prompt that separates canonical Facts from RAG evidence."""
        facts_payload = facts if isinstance(facts, Mapping) else _serialize_facts(facts)
        metrics = facts_payload.get("metrics", {})

        def metric_line(name: str, label: str) -> str:
            metric = metrics.get(name, {})
            if not isinstance(metric, Mapping):
                return f"- {label}: UNKNOWN"
            value = _format_value(metric.get("value"), metric.get("unit", ""))
            version = metric.get("metric_version") or "UNKNOWN"
            return f"- {label}: {value}（指标版本: {version}）"

        lines = [
            "你是一名建筑项目经营分析专家，只做有证据的解释、风险提示和复核建议。",
            "",
            "## 不可违反的数据边界",
            "- Canonical Facts 是确定性计算结果，只能读取，不能重新计算、修改或用文档/模型值覆盖。",
            "- RAG 文档是 Documentary Truth，只能作为带来源的证据；不能改变 Facts。",
            "- 缺失字段必须写 UNKNOWN；证据不足必须要求人工复核，不得给出确定性结论。",
            f"- entity_code 只能是主数据中的真实代码（{CANONICAL_ENTITY_RANGE_TEXT}）；A/B/C/D 仅是 business_role。",
            "",
            f"## Facts 截止时间\n{facts_payload.get('as_of', 'UNKNOWN')}",
            f"Facts 版本: {facts_payload.get('facts_version', 'UNKNOWN')}",
            "",
            "## Canonical Facts（只读，不要重新计算）",
            metric_line("recognized_revenue", "确认收入"),
            metric_line("real_profit", "项目真实利润"),
            metric_line("eac_profit", "EAC预计利润"),
            metric_line("eac_margin", "EAC预计利润率"),
            metric_line("collection_rate", "回款率"),
            metric_line("cash_gap_30d", "30天现金缺口"),
            "",
            "## 可核验的文档证据",
        ]
        known_metrics = {
            "recognized_revenue",
            "real_profit",
            "eac_profit",
            "eac_margin",
            "collection_rate",
            "cash_gap_30d",
        }
        for name in sorted(set(metrics) - known_metrics):
            metric = metrics.get(name)
            if isinstance(metric, Mapping):
                lines.insert(
                    lines.index("## 可核验的文档证据"),
                    f"- {name}: {_format_value(metric.get('value'), metric.get('unit', ''))} "
                    f"（指标版本: {metric.get('metric_version') or 'UNKNOWN'}）",
                )
        for evidence in rag_evidence:
            lines.extend([
                f"[{evidence.get('evidence_id')}] 文件: {evidence.get('filename', 'UNKNOWN')}",
                f"来源: {evidence.get('source', 'UNKNOWN')}；页码: {evidence.get('page', 'UNKNOWN')}；",
                f"版本/生效期: {evidence.get('version') or 'UNKNOWN'} / "
                f"{evidence.get('effective_from') or 'UNKNOWN'}—{evidence.get('effective_to') or 'UNKNOWN'}",
                f"内容: {evidence.get('content', '')}",
                "",
            ])
        lines.extend([
            "## 输出 JSON（不要输出 Markdown 代码围栏）",
            "仅返回以下字段：",
            '{"summary":"...","risk_level":"LOW|MEDIUM|HIGH|CRITICAL",'
            '"evidence_refs":["evidence_id"],"findings":[{"category":"...",'
            '"description":"...","evidence_refs":["evidence_id"],"suggestion":"..."}],'
            '"metric_explanations":{"metric_name":"..."}}',
            "不要返回 health_score；系统只从 Canonical Facts 读取它。",
            "summary 和每个 finding 必须有 evidence_refs；引用不存在的 evidence_id 即视为需要人工复核。",
        ])
        return "\n".join(lines)

    def _call_llm(self, prompt: str, model: str) -> str:
        """Call the configured OpenAI-compatible model; raise on unavailability."""
        from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

        base_url = (LLM_BASE_URL or "").rstrip("/")
        if not base_url:
            raise AIReviewUnavailable("AI Review LLM 未配置；无法形成模型结论")
        selected_model = model or LLM_MODEL
        if not selected_model:
            raise AIReviewUnavailable("AI Review LLM model 未配置")

        if base_url.endswith("/chat/completions"):
            url = base_url
        elif base_url.endswith("/v1"):
            url = f"{base_url}/chat/completions"
        else:
            url = f"{base_url}/v1/chat/completions"
        try:
            # AI Review uses the same dedicated LLM policy as extraction,
            # answer, Rewrite and HyDE.  This allows local Ollama/LM
            # Studio/vLLM while keeping generic outbound URLs strict.
            url = validate_llm_outbound_url(url)
        except ValueError as exc:
            raise AIReviewUnavailable("AI Review LLM URL 不允许出站") from exc
        headers = {"Content-Type": "application/json"}
        if LLM_API_KEY:
            headers["Authorization"] = f"Bearer {LLM_API_KEY}"
        try:
            with httpx.Client(timeout=90) as client:
                response = client.post(
                    url,
                    headers=headers,
                    json={
                        "model": selected_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.0,
                        "response_format": {"type": "json_object"},
                    },
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            raise AIReviewUnavailable("AI Review LLM 请求超时") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise AIReviewUnavailable("AI Review LLM 请求失败") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIReviewUnavailable("AI Review LLM 返回格式无效") from exc
        if not isinstance(content, str) or not content.strip():
            raise AIReviewUnavailable("AI Review LLM 未返回内容")
        return content.strip()

    def _parse_llm_result(
        self,
        llm_output: str,
        valid_evidence_ids: set[str] | None = None,
    ) -> dict:
        """Parse and ground model JSON; never return a fabricated score."""
        if not isinstance(llm_output, str) or not llm_output.strip():
            raise AIReviewOutputError("LLM 输出为空")
        text = llm_output.strip()
        if text.startswith("```"):
            text = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                text,
                flags=re.IGNORECASE | re.DOTALL,
            ).strip()
        try:
            raw = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise AIReviewOutputError("LLM 输出不是有效 JSON") from exc
        if not isinstance(raw, Mapping):
            raise AIReviewOutputError("LLM 输出必须是 JSON 对象")

        evidence_refs = raw.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise AIReviewOutputError("LLM 输出缺少 evidence_refs，无法核验结论")
        valid_ids = valid_evidence_ids or set()
        evidence_refs = [str(ref) for ref in evidence_refs if str(ref) in valid_ids]
        if not evidence_refs:
            raise AIReviewOutputError("LLM 输出引用了不存在的证据")

        risk_level = str(raw.get("risk_level") or "").upper()
        if risk_level not in ALLOWED_RISK_LEVELS:
            raise AIReviewOutputError("LLM 输出 risk_level 无效")
        summary = raw.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise AIReviewOutputError("LLM 输出缺少 summary")

        findings: list[dict] = []
        raw_findings = raw.get("findings", [])
        if not isinstance(raw_findings, list):
            raise AIReviewOutputError("LLM 输出 findings 必须是数组")
        for finding in raw_findings:
            if not isinstance(finding, Mapping):
                raise AIReviewOutputError("LLM 输出包含无效 finding")
            refs = finding.get("evidence_refs")
            refs = [str(ref) for ref in refs] if isinstance(refs, list) else []
            refs = [ref for ref in refs if ref in valid_ids]
            if not refs:
                raise AIReviewOutputError("finding 缺少有效 evidence_refs")
            findings.append({
                "category": str(finding.get("category") or "未分类"),
                "description": str(finding.get("description") or ""),
                "evidence_refs": refs,
                "suggestion": str(finding.get("suggestion") or ""),
            })

        explanations = raw.get("metric_explanations", {})
        if not isinstance(explanations, Mapping):
            raise AIReviewOutputError("metric_explanations 必须是对象")
        return {
            "review_status": REVIEW_COMPLETED,
            "summary": summary.strip(),
            "risk_level": risk_level,
            "confidence": "GROUNDED",
            "evidence_refs": evidence_refs,
            "findings": findings,
            "metric_explanations": {
                str(key): str(value) for key, value in explanations.items()
            },
        }

    def _needs_review_result(
        self,
        facts_payload: dict,
        evidence_manifest: list[dict],
        reason: str,
    ) -> dict:
        """Build an explicit non-conclusion for missing/invalid evidence."""
        score = _metric_value(facts_payload, "health_score")
        return {
            "review_status": REVIEW_NEEDS_REVIEW,
            "needs_review": True,
            "summary": "证据不足，未生成确定性 AI Review 结论；请人工复核。",
            "risk_level": UNKNOWN_RISK,
            "confidence": "UNKNOWN",
            "health_score": score,
            "health_score_source": "canonical_facts" if score is not None else "unknown",
            "findings": [],
            "metric_explanations": {},
            "evidence_refs": [],
            "facts": facts_payload,
            "evidence": evidence_manifest,
            "reasons": [reason],
        }

    def get_review_history(self, project_code: str, limit: int = 10) -> list[dict]:
        """Return history without assuming JSON columns are strings or dicts."""
        runs = self._run_service.get_runs_for_project(
            project_code=project_code,
            limit=limit,
        )
        return [
            {
                "run_id": run.id,
                "started_at": run.started_at,
                "status": run.status,
                "risk_level": run.risk_level,
                "result_summary": run.result_summary,
                "health_score": run.health_score,
                "as_of": (
                    _json_object(run.result).get("facts", {}).get("as_of")
                    or _json_object(run.result).get("as_of")
                ),
            }
            for run in runs
        ]


__all__ = [
    "AIReviewError",
    "AIReviewUnavailable",
    "AIReviewOutputError",
    "AIReviewService",
]
