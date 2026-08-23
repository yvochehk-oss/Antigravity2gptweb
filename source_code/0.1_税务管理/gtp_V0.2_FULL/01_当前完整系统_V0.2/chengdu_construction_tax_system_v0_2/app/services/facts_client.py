"""V0.2: RAG V1.0 Facts Provider 客户端。

税务系统侧调用 RAG V1.0 Facts Provider 的统一入口：
- 调用 `/api/v1/facts/projects/{project_code}` 读取指标
- 调用 `/api/v1/facts/invalidate` 在业务变更时主动失效缓存
- 每次调用记录到 `facts_request_logs` 表；响应保留到 `facts_snapshots` 表

Facts Provider 是 RAG V1.0 统一事实通道，承载 L2（确定性计算）和 L3（Analytics Contract）。
本客户端仅作调用 + 快照记录，不复制指标口径。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import httpx

import logging

from .. import config
from ..audit import audit
from ..db import SessionLocal
from ..models import FactsRequestLog, FactsSnapshot, Project


logger = logging.getLogger(__name__)


FACTS_URL: str = config.RAG_V1_FACTS_URL
FACTS_API_KEY: str = config.RAG_V1_FACTS_API_KEY
FACTS_DEFAULT_MAX_AGE: int = config.FACTS_DEFAULT_MAX_AGE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if FACTS_API_KEY:
        h["Authorization"] = f"Bearer {FACTS_API_KEY}"
    return h


def _project_code_from_id(db, project_id: int) -> Optional[str]:
    """根据税务系统 project_id 查找对应的 project_code。"""
    if not project_id:
        return None
    row = db.query(Project).filter(Project.id == project_id).first()
    return row.code if row else None


def get_project_facts(
    project_id: int,
    *,
    require_fresh: bool = False,
    max_age: int = FACTS_DEFAULT_MAX_AGE,
    as_of: str | None = None,
    actor: str = "system",
    ip: str = "",
) -> dict[str, Any]:
    """调用 RAG V1.0 Facts Provider 读取项目指标。

    行为：
    1. 由 project_id 查出 project_code；
    2. 调用 `/api/v1/facts/projects/{project_code}`；
    3. 将响应写入 facts_snapshots（每次响应都落库，便于追溯）；
    4. 写一条 facts_request_logs（含 status / latency_ms / actor / ip）。

    返回 FactsResponse 字典；若调用失败，返回 dict 含 "error" 键。
    """
    started = time.time()
    db = SessionLocal()
    project_code = _project_code_from_id(db, project_id)
    if not project_code:
        db.close()
        return {"error": f"project_id={project_id} 在税务系统内不存在"}

    endpoint = f"/api/v1/facts/projects/{project_code}"
    url = f"{FACTS_URL}{endpoint}"

    params: dict[str, Any] = {}
    if require_fresh:
        params["require_fresh"] = "true"
    if max_age and max_age != FACTS_DEFAULT_MAX_AGE:
        params["max_age"] = max_age
    if as_of:
        params["as_of"] = as_of

    log = FactsRequestLog(
        project_code=project_code,
        endpoint=endpoint,
        require_fresh=require_fresh,
        max_age=max_age,
        as_of_param=as_of or "",
        response_status=0,
        latency_ms=0,
        actor=actor,
        ip=ip,
        created_at=_now(),
    )
    db.add(log)
    db.flush()

    response_payload: dict[str, Any] = {}
    response_status = 0
    error_message = ""

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(url, headers=_headers(), params=params)
            response_status = resp.status_code
            resp.raise_for_status()
            response_payload = resp.json()
    except httpx.HTTPStatusError as e:
        error_message = f"{e.response.status_code}: {e.response.text[:200]}"
    except httpx.TimeoutException:
        error_message = "Facts Provider timeout (30s)"
    except Exception as e:
        error_message = f"Facts call failed: {e}"

    latency_ms = int((time.time() - started) * 1000)
    log.response_status = response_status or 500
    log.latency_ms = latency_ms
    log.error_message = error_message

    if not error_message and response_payload:
        metrics = response_payload.get("metrics") or {}
        as_of_ts = response_payload.get("as_of") or _now()
        facts_version = response_payload.get("facts_version") or ""

        snapshot = FactsSnapshot(
            project_id=project_id,
            project_code=project_code,
            as_of=as_of_ts,
            facts_version=facts_version,
            metrics_json=json.dumps(metrics, ensure_ascii=False),
            raw_response_json=json.dumps(response_payload, ensure_ascii=False),
            source="rag_v1",
            require_fresh=require_fresh,
            max_age=max_age,
            requested_at=_now(),
            requested_by=actor,
        )
        db.add(snapshot)
        db.flush()
        log.facts_snapshot_id = snapshot.id

        audit(
            db,
            action="QUERY",
            obj_type="Facts",
            obj_id=snapshot.id,
            message=(
                f"{project_code} as_of={as_of_ts} version={facts_version} "
                f"latency={latency_ms}ms"
            ),
            actor=actor,
            ip=ip,
        )
    else:
        audit(
            db,
            action="QUERY",
            obj_type="Facts",
            obj_id="error",
            message=f"{project_code} {error_message}",
            actor=actor,
            ip=ip,
        )

    db.commit()
    db.close()

    if error_message:
        return {"error": error_message, "project_code": project_code}
    return response_payload


def invalidate_facts(
    project_id: int,
    *,
    actor: str = "system",
    ip: str = "",
) -> dict[str, Any]:
    """业务数据变更后调用：通知 Facts Provider 失效该项目缓存。"""
    db = SessionLocal()
    project_code = _project_code_from_id(db, project_id)
    if not project_code:
        db.close()
        return {"error": f"project_id={project_id} 不存在"}

    url = f"{FACTS_URL}/api/v1/facts/invalidate"
    body = {"project_code": project_code}
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, headers=_headers(), json=body)
            resp.raise_for_status()
            audit(
                db,
                action="INVALIDATE",
                obj_type="Facts",
                obj_id=project_code,
                message=f"已失效 Facts 缓存: {project_code}",
                actor=actor,
                ip=ip,
            )
            db.commit()
            db.close()
            return resp.json()
    except Exception as e:
        db.close()
        return {"error": f"invalidate 失败: {e}", "project_code": project_code}


def get_latest_snapshot(project_id: int) -> Optional[dict[str, Any]]:
    """读取最近一次的 Facts 快照（无需调用 RAG）。"""
    db = SessionLocal()
    row = (
        db.query(FactsSnapshot)
        .filter(FactsSnapshot.project_id == project_id)
        .order_by(FactsSnapshot.id.desc())
        .first()
    )
    db.close()
    if not row:
        return None
    return {
        "id": row.id,
        "project_id": row.project_id,
        "project_code": row.project_code,
        "as_of": row.as_of,
        "facts_version": row.facts_version,
        "metrics": json.loads(row.metrics_json or "{}"),
        "requested_at": row.requested_at,
        "requested_by": row.requested_by,
    }


def list_snapshots(project_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """读取项目最近的 N 个 Facts 快照列表。"""
    db = SessionLocal()
    rows = (
        db.query(FactsSnapshot)
        .filter(FactsSnapshot.project_id == project_id)
        .order_by(FactsSnapshot.id.desc())
        .limit(limit)
        .all()
    )
    db.close()
    return [
        {
            "id": r.id,
            "project_code": r.project_code,
            "as_of": r.as_of,
            "facts_version": r.facts_version,
            "requested_at": r.requested_at,
            "requested_by": r.requested_by,
            "require_fresh": r.require_fresh,
        }
        for r in rows
    ]


def to_decimal_safe(value: Any) -> Decimal:
    """把 Facts 指标值安全转换为 Decimal（用于系统内计算）。"""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


__all__ = [
    "get_project_facts",
    "invalidate_facts",
    "get_latest_snapshot",
    "list_snapshots",
    "to_decimal_safe",
]