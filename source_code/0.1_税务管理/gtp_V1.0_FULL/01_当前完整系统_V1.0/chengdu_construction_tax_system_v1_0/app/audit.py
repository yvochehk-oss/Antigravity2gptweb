"""V0.2: 统一审计 + actor/ip 注入。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from .models import AuditLog
from .observability import current_actor as _scope_actor
from .observability import get_request_id


def current_actor(request: Request | None = None) -> str:
    """Return the authenticated actor for an audit record.

    Priority:
    1. ``request.state.current_user`` (populated by the AuthMiddleware)
    2. The actor pin in the active observability scope (background tasks)
    3. ``anonymous`` as a final fallback
    """
    if request is not None:
        user = getattr(request.state, "current_user", None)
        if user is not None:
            name = getattr(user, "username", None) or getattr(user, "display_name", None)
            if name:
                return str(name)
    return _scope_actor() or "anonymous"


def current_ip(request: Request | None = None) -> str:
    if request is None or request.client is None:
        return ""
    return request.client.host or ""


def audit(
    db: Session,
    action: str,
    obj_type: str,
    obj_id: str | int,
    message: str,
    actor: str = "anonymous",
    ip: str = "",
    request_id: str = "",
) -> AuditLog:
    """写入审计日志。保证与业务同事务（不主动 commit，由调用方决定）。

    The ``request_id`` defaults to the active observability scope so a
    background job shares the same identifier as the request that
    initiated it.
    """
    resolved_request_id = (request_id or get_request_id() or "").strip()
    log = AuditLog(
        action=action,
        object_type=obj_type,
        object_id=str(obj_id),
        message=message[:5000],
        actor=actor or "anonymous",
        ip=ip or "",
        request_id=resolved_request_id,
    )
    db.add(log)
    return log


def audit_from_request(
    db: Session,
    request: Request,
    action: str,
    obj_type: str,
    obj_id: str | int,
    message: str,
) -> AuditLog:
    return audit(
        db, action, obj_type, obj_id, message,
        actor=current_actor(request),
        ip=current_ip(request),
        request_id=get_request_id(),
    )
