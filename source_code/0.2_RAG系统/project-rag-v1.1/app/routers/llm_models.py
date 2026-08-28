"""Admin-only RAG LLM endpoint management.

The endpoint table is deliberately managed at the RAG boundary.  This
router never serializes ``api_key``; the shared model pool remains the only
caller that can use it for an outbound request.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import auth as auth_module
from ..auth import (
    ServicePrincipal,
    TaxPrincipal,
    require_web_or_service_read,
    require_web_or_service_role,
    require_web_role,
)
from ..models import LLMModelEndpoint
from ..schemas import (
    LLMModelEndpointCreate,
    LLMModelEndpointMove,
    LLMModelEndpointPatch,
)
from ..security import validate_llm_outbound_url
from ..services import llm_pool

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
router = APIRouter(tags=["RAG LLM models"])


def get_db():
    # A local generator keeps this router independently testable and matches
    # the existing router dependency contract.
    from ..db import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _admin_read(principal: TaxPrincipal | ServicePrincipal = Depends(require_web_or_service_read)):
    """Allow only an admin cookie or the trusted internal service key."""
    # Resolve the marker classes through the module at call time.  The auth
    # test harness reloads ``app.auth`` to isolate JWT configuration; keeping
    # a class object captured at import time would then reject a valid
    # TaxPrincipal as a misleading 403.  This does not trust a caller-supplied
    # value: ``principal`` is still produced by the dependency above.
    if isinstance(principal, auth_module.ServicePrincipal):
        return principal
    if not isinstance(principal, auth_module.TaxPrincipal) or principal.role.strip().lower() != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return principal


def _validate_endpoint_url(base_url: str, chat_path: str) -> None:
    """Validate the resolved OpenAI-compatible endpoint before persistence."""
    try:
        resolved = llm_pool.build_chat_url(base_url, chat_path)
        validate_llm_outbound_url(resolved)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "LLM_ENDPOINT_URL_INVALID", "message": str(exc)},
        ) from exc


def _view(endpoint: LLMModelEndpoint) -> dict[str, Any]:
    """Return a credential-free endpoint view with operational status."""
    result = llm_pool.safe_endpoint_view(endpoint)
    # safe_endpoint_view is the allow-list boundary.  These fields are
    # explicitly non-secret model state that the UI needs to render.
    result.update(
        {
            "note": str(getattr(endpoint, "note", "") or ""),
            "has_api_key": bool(getattr(endpoint, "api_key", "")),
            "last_status": str(getattr(endpoint, "last_status", "UNKNOWN") or "UNKNOWN"),
            "last_error_class": str(getattr(endpoint, "last_error_class", "") or ""),
            "last_latency_ms": int(getattr(endpoint, "last_latency_ms", 0) or 0),
            "last_checked_at": getattr(endpoint, "last_checked_at", None).isoformat()
            if getattr(endpoint, "last_checked_at", None)
            else None,
            "managed_fallback": result.get("source") == "local_fallback",
        }
    )
    return result


def _ordered_rows(db: Session, routing_group: str | None = None, *, lock: bool = False):
    statement = select(LLMModelEndpoint)
    if routing_group is not None:
        statement = statement.where(LLMModelEndpoint.routing_group == routing_group)
    statement = statement.order_by(LLMModelEndpoint.routing_group, LLMModelEndpoint.priority, LLMModelEndpoint.id)
    if lock:
        statement = statement.with_for_update()
    return list(db.execute(statement).scalars().all())


def _commit_or_500(db: Session, message: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="模型名称已存在或配置不满足数据库约束") from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=message) from exc


@router.get("/llm-models", response_class=HTMLResponse)
def llm_models_page(request: Request, principal=Depends(require_web_role("admin"))):
    return templates.TemplateResponse(request, "llm_models.html", {"active_page": "llm-models"})


@router.get("/api/v1/llm-models")
def list_llm_models(
    principal=Depends(_admin_read),
    db: Session = Depends(get_db),
):
    # Keep every persisted routing group visible, then add the launcher-owned
    # local endpoint when it is healthy.  The latter is intentionally not
    # persisted and is read-only; the pool still appends it after all eligible
    # user rows.
    rows = _ordered_rows(db)
    items = [_view(item) for item in rows]
    catalog = llm_pool.get_endpoint_catalog(session=db)
    items.extend(
        _view(item)
        for item in catalog.endpoints
        if item.source == "local_fallback"
    )
    return {"items": items}


@router.post("/api/v1/llm-models", status_code=status.HTTP_201_CREATED)
def create_llm_model(
    payload: LLMModelEndpointCreate,
    principal=Depends(require_web_or_service_role("admin")),
    db: Session = Depends(get_db),
):
    _validate_endpoint_url(payload.base_url, payload.chat_path)
    endpoint = LLMModelEndpoint(**payload.model_dump())
    db.add(endpoint)
    _commit_or_500(db, "模型配置保存失败，事务已回滚")
    db.refresh(endpoint)
    return _view(endpoint)


@router.patch("/api/v1/llm-models/{model_id}")
def update_llm_model(
    model_id: int,
    payload: LLMModelEndpointPatch,
    principal=Depends(require_web_or_service_role("admin")),
    db: Session = Depends(get_db),
):
    endpoint = db.get(LLMModelEndpoint, model_id)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")

    values = payload.model_dump(exclude_unset=True)
    next_base = values.get("base_url", endpoint.base_url)
    next_path = values.get("chat_path", endpoint.chat_path)
    _validate_endpoint_url(next_base, next_path)
    for field, value in values.items():
        # A blank API key from the settings form means "leave unchanged".
        # Credentials can therefore never be accidentally erased by a
        # browser form submission.
        if field == "api_key" and not str(value or "").strip():
            continue
        setattr(endpoint, field, value)
    _commit_or_500(db, "模型配置更新失败，事务已回滚")
    db.refresh(endpoint)
    return _view(endpoint)


@router.post("/api/v1/llm-models/{model_id}/move")
def move_llm_model(
    model_id: int,
    payload: LLMModelEndpointMove,
    principal=Depends(require_web_or_service_role("admin")),
    db: Session = Depends(get_db),
):
    endpoint = db.get(LLMModelEndpoint, model_id)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")

    # Lock and re-number the group in one transaction.  Re-numbering first
    # handles tied priorities deterministically (priority, id), then the
    # requested adjacent swap changes the effective pool order.
    rows = _ordered_rows(db, endpoint.routing_group, lock=True)
    index = next((i for i, item in enumerate(rows) if item.id == model_id), None)
    if index is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    target_index = index - 1 if payload.direction == "up" else index + 1
    if 0 <= target_index < len(rows):
        for position, item in enumerate(rows):
            item.priority = position * 10
        rows[index].priority, rows[target_index].priority = (
            rows[target_index].priority,
            rows[index].priority,
        )
        _commit_or_500(db, "模型优先级调整失败，事务已回滚")
    else:
        # Keep the endpoint order response useful while treating a boundary
        # move as an idempotent operation.
        db.rollback()
    return {"items": [_view(item) for item in _ordered_rows(db)]}


@router.post("/api/v1/llm-models/{model_id}/test")
def test_llm_model(
    model_id: int,
    principal=Depends(require_web_or_service_role("admin")),
    db: Session = Depends(get_db),
):
    endpoint = db.get(LLMModelEndpoint, model_id)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")

    # check_connection uses the shared pool's URL policy, HTTP client and
    # key-free attempt trail.  Restrict its catalog to the selected row so a
    # successful higher-priority provider cannot mask the button's target.
    class _OneRowResult:
        def scalars(self):
            return self

        def all(self):
            return [endpoint]

    class _OneRowSession:
        def execute(self, _statement):
            return _OneRowResult()

    try:
        result = llm_pool.check_connection(
            session=_OneRowSession(),
            routing_group=endpoint.routing_group,
            # The button probes exactly the persisted row selected by the
            # administrator.  Do not append the launcher fallback here.
            local_base_url="",
            local_model="",
        )
    except Exception as exc:
        result = {"ok": False, "error": "模型探测失败", "attempts": []}
        error_class = exc.__class__.__name__
    else:
        attempts = result.get("attempts") or []
        error_class = str(attempts[-1].get("error_class", "")) if attempts else ""

    attempt = (result.get("attempts") or [{}])[-1]
    endpoint.last_status = "OK" if result.get("ok") else "ERROR"
    endpoint.last_error_class = "" if result.get("ok") else error_class
    endpoint.last_latency_ms = int(attempt.get("latency_ms") or 0)
    endpoint.last_checked_at = datetime.now(timezone.utc)
    _commit_or_500(db, "模型探测状态保存失败，事务已回滚")
    # Do not pass the provider's arbitrary /models JSON through this admin
    # API.  The shared pool's attempts are already key-free and sufficient
    # for the UI status message.
    response = {
        "ok": bool(result.get("ok")),
        "attempts": result.get("attempts") or [],
        "endpoint": _view(endpoint),
    }
    if not response["ok"]:
        response["error"] = "模型连接失败"
    return response


@router.delete("/api/v1/llm-models/{model_id}")
def delete_llm_model(
    model_id: int,
    principal=Depends(require_web_or_service_role("admin")),
    db: Session = Depends(get_db),
):
    endpoint = db.get(LLMModelEndpoint, model_id)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    db.delete(endpoint)
    _commit_or_500(db, "模型配置删除失败，事务已回滚")
    return {"ok": True, "deleted_id": model_id}


__all__ = ["router"]
