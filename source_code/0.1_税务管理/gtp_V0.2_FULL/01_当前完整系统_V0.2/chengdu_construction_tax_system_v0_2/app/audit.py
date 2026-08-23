"""V0.2: 统一审计 + actor/ip 注入。"""
from __future__ import annotations

from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from .models import AuditLog


def current_actor(request: Optional[Request] = None) -> str:
    """V0.2 Demo 阶段 actor 留 "demo-user"。生产版对接用户系统后替换。"""
    return "demo-user"


def current_ip(request: Optional[Request] = None) -> str:
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
) -> AuditLog:
    """写入审计日志。保证与业务同事务（不主动 commit，由调用方决定）。"""
    log = AuditLog(
        action=action,
        object_type=obj_type,
        object_id=str(obj_id),
        message=message[:5000],
        actor=actor or "anonymous",
        ip=ip or "",
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
    )