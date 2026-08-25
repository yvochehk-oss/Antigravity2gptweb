"""管理员可用的 AI 模型端点与凭证管理。

Browser-submitted API keys are moved immediately into ``secret_store``.  No
ORM row, audit row, template context, exception, or response contains the
credential value.  Legacy environment-variable references remain supported.

The production UI deliberately does not create or expose the old ``mock``
adapter.  A local ``llama.cpp`` OpenAI-compatible server is represented by a
normal endpoint (usually ``http://127.0.0.1:<port>``) and requires the
explicit ``AI_ALLOW_PRIVATE_LLM=1`` policy.
"""
from __future__ import annotations

import os
from contextlib import suppress
from typing import Any

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..ai.adapter import endpoint_is_allowed
from ..audit import audit_from_request
from ..db import SessionLocal
from ..dependencies import admin_only
from ..models import AIModelEndpoint, AIReviewJob, AuditLog
from ..security import resolve_secret_env_name, validate_ai_endpoint_url
from ..services.secret_store import SecretStoreError, default_secret_store
from ..templates import templates

router = APIRouter()

_ADAPTER_ALIASES = {
    "openai兼容接口": "openai_compatible",
    "openai_compatible": "openai_compatible",
    "openai-compatible": "openai_compatible",
    "openrouter": "openrouter",
    # Kept solely to produce a controlled validation error for old clients;
    # the admin UI and production API never create mock endpoints.
    "模拟": "mock",
    "mock": "mock",
}
_CONTROL_CHARS = frozenset(chr(i) for i in range(32)) | {chr(127)}
_MAX_SECRET_BYTES = 64 * 1024


def _column_names() -> set[str]:
    """Return live endpoint columns while the schema migration rolls out."""
    try:
        return set(AIModelEndpoint.__table__.columns.keys())
    except AttributeError:  # pragma: no cover - defensive for unit doubles
        return set()


def _has_column(name: str) -> bool:
    return name in _column_names()


def _endpoint_value(endpoint: AIModelEndpoint, name: str, default: Any = "") -> Any:
    return getattr(endpoint, name, default)


def _set_endpoint_value(endpoint: AIModelEndpoint, name: str, value: Any) -> None:
    if _has_column(name) or hasattr(endpoint, name):
        setattr(endpoint, name, value)


def _clean_text(value: str | None, *, field: str, maximum: int, required: bool = False) -> str:
    raw = str(value or "")
    if len(raw) > maximum or any(char in _CONTROL_CHARS for char in raw):
        raise ValueError(f"{field} 超出允许大小或包含控制字符")
    cleaned = raw.strip()
    if required and not cleaned:
        raise ValueError(f"{field} 不能为空")
    return cleaned


def _normalize_adapter(value: str | None) -> str:
    candidate = _clean_text(value, field="adapter", maximum=30, required=True).lower()
    try:
        return _ADAPTER_ALIASES[candidate]
    except KeyError as exc:
        raise ValueError("不支持的 AI 适配器") from exc


def _validate_api_key_input(api_key: str | None) -> str:
    """Validate without ever echoing the submitted value."""
    value = str(api_key or "")
    if not value:
        return ""
    if len(value.encode("utf-8")) > _MAX_SECRET_BYTES:
        raise ValueError("API Key 超出允许大小")
    if any(char in _CONTROL_CHARS for char in value):
        raise ValueError("API Key 不得包含控制字符")
    if not value.strip():
        raise ValueError("API Key 不能为空")
    return value


def _validate_form(
    *, name: str, adapter: str, base_url: str, chat_path: str, model: str,
    api_key_env: str, timeout_seconds: int, note: str, priority: int,
    routing_group: str, api_key: str,
) -> dict[str, Any]:
    normalized_adapter = _normalize_adapter(adapter)
    if normalized_adapter == "mock":
        raise ValueError("正式环境不允许创建或编辑 mock AI 端点；请使用本地 llama.cpp 端点")
    values = {
        "name": _clean_text(name, field="名称", maximum=100, required=True),
        "adapter": normalized_adapter,
        "base_url": _clean_text(base_url, field="base_url", maximum=300),
        "chat_path": _clean_text(chat_path, field="chat_path", maximum=200),
        "model": _clean_text(model, field="模型名", maximum=120),
        "api_key_env": _clean_text(api_key_env, field="环境变量名", maximum=120),
        "note": _clean_text(note, field="备注", maximum=300),
        "api_key": _validate_api_key_input(api_key),
        "timeout_seconds": timeout_seconds,
        "priority": priority,
        "routing_group": _clean_text(routing_group, field="路由组", maximum=80),
    }
    if not 1 <= timeout_seconds <= 600:
        raise ValueError("超时时间必须在 1 到 600 秒之间")
    if not 0 <= priority <= 10000:
        raise ValueError("优先级必须在 0 到 10000 之间")
    if values["api_key"] and values["api_key_env"]:
        raise ValueError("API Key 与环境变量名不能同时填写")
    validate_ai_endpoint_url(values["base_url"], values["chat_path"])
    resolve_secret_env_name(values["api_key_env"])
    return values


def _credential_status(endpoint: AIModelEndpoint) -> str:
    """Return UI-safe state; never return a ref or any key characters."""
    ref = str(_endpoint_value(endpoint, "credential_ref", "") or "").strip()
    if ref:
        try:
            metadata = default_secret_store().metadata(ref)
        except SecretStoreError:
            return "凭证存储不可用"
        if metadata is None:
            return "凭证缺失"
        return "已撤销" if metadata["revoked"] else "已配置"
    if str(getattr(endpoint, "api_key_env", "") or "").strip():
        return "环境变量引用"
    return "未配置（本地服务）"


def _new_endpoint_kwargs(values: dict[str, Any]) -> dict[str, Any]:
    columns = _column_names()
    kwargs = {
        key: values[key]
        for key in (
            "name", "adapter", "base_url", "chat_path", "model", "api_key_env",
            "timeout_seconds", "note",
        )
        if key in columns
    }
    if "enabled" in columns:
        kwargs["enabled"] = True
    for name in ("priority", "routing_group"):
        if name in columns:
            kwargs[name] = values[name]
    if "credential_ref" in columns:
        kwargs["credential_ref"] = ""
    return kwargs


def _cleanup_new_secret(store, ref: str | None) -> None:
    if not ref:
        return
    with suppress(Exception):
        store.delete(ref)


def _get_endpoint(db, endpoint_id: int) -> AIModelEndpoint:
    endpoint = db.get(AIModelEndpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="AI 端点不存在")
    return endpoint


def _commit_or_fail(db) -> None:
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="AI 端点保存失败") from exc


@router.get("/ai-models", response_class=HTMLResponse)
def ai_models(request: Request):
    admin_only(request)
    db = SessionLocal()
    try:
        rows = db.execute(select(AIModelEndpoint).order_by(AIModelEndpoint.id)).scalars().all()
        # Existing mock rows are intentionally not deleted by the UI.  They
        # are hidden from production model management and cannot be recreated.
        rows = [
            row for row in rows
            if str(row.adapter or "").strip().lower() != "mock"
            and endpoint_is_allowed(row)
        ]
        credential_status = {row.id: _credential_status(row) for row in rows}
        return templates.TemplateResponse(
            request, "ai_models.html",
            {"request": request, "rows": rows, "credential_status": credential_status},
        )
    finally:
        db.close()


def _form_values(
    *, name: str, adapter: str, base_url: str, chat_path: str, model: str,
    api_key_env: str, timeout_seconds: int, note: str, priority: int,
    routing_group: str, api_key: str,
) -> dict[str, Any]:
    try:
        return _validate_form(
            name=name, adapter=adapter, base_url=base_url, chat_path=chat_path, model=model,
            api_key_env=api_key_env, timeout_seconds=timeout_seconds, note=note,
            priority=priority, routing_group=routing_group, api_key=api_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai-models")
def ai_model_add(
    request: Request, name: str = Form(...), adapter: str = Form("openai_compatible"),
    base_url: str = Form(""), chat_path: str = Form("/v1/chat/completions"),
    model: str = Form(""), api_key: str = Form(""), api_key_env: str = Form(""),
    timeout_seconds: int = Form(90), note: str = Form(""), priority: int = Form(100),
    routing_group: str = Form("default"),
):
    admin_only(request)
    values = _form_values(
        name=name, adapter=adapter, base_url=base_url, chat_path=chat_path, model=model,
        api_key_env=api_key_env, timeout_seconds=timeout_seconds, note=note,
        priority=priority, routing_group=routing_group, api_key=api_key,
    )
    store = default_secret_store()
    new_ref: str | None = None
    db = SessionLocal()
    try:
        if values["api_key"]:
            if not _has_column("credential_ref"):
                raise HTTPException(status_code=503, detail="当前数据库尚未启用安全凭证存储")
            try:
                new_ref = store.put(values["api_key"])
            except SecretStoreError as exc:
                raise HTTPException(status_code=503, detail="安全凭证存储不可用") from exc
        endpoint = AIModelEndpoint(**_new_endpoint_kwargs(values))
        _set_endpoint_value(endpoint, "credential_ref", new_ref or "")
        db.add(endpoint)
        db.flush()
        audit_from_request(db, request, "CREATE", "AIModelEndpoint", endpoint.id, "创建 AI 端点")
        _commit_or_fail(db)
    except HTTPException:
        db.rollback()
        _cleanup_new_secret(store, new_ref)
        raise
    except Exception as exc:
        db.rollback()
        _cleanup_new_secret(store, new_ref)
        raise HTTPException(status_code=500, detail="AI 端点保存失败") from exc
    finally:
        db.close()
    return RedirectResponse("/ai-models", status_code=303)


@router.post("/ai-models/{endpoint_id}/update")
def ai_model_update(
    request: Request, endpoint_id: int, name: str = Form(...),
    adapter: str = Form("openai_compatible"), base_url: str = Form(""),
    chat_path: str = Form("/v1/chat/completions"), model: str = Form(""),
    api_key: str = Form(""), api_key_env: str = Form(""),
    timeout_seconds: int = Form(90), note: str = Form(""), priority: int = Form(100),
    routing_group: str = Form("default"),
):
    admin_only(request)
    values = _form_values(
        name=name, adapter=adapter, base_url=base_url, chat_path=chat_path, model=model,
        api_key_env=api_key_env, timeout_seconds=timeout_seconds, note=note,
        priority=priority, routing_group=routing_group, api_key=api_key,
    )
    store = default_secret_store()
    new_ref: str | None = None
    old_ref = ""
    retire_old_ref = False
    db = SessionLocal()
    try:
        endpoint = _get_endpoint(db, endpoint_id)
        if str(endpoint.adapter or "").strip().lower() == "mock":
            raise HTTPException(status_code=400, detail="旧 mock 端点不能在此页面编辑")
        old_ref = str(_endpoint_value(endpoint, "credential_ref", "") or "").strip()
        if values["api_key"]:
            if not _has_column("credential_ref"):
                raise HTTPException(status_code=503, detail="当前数据库尚未启用安全凭证存储")
            try:
                new_ref = store.put(values["api_key"])
            except SecretStoreError as exc:
                raise HTTPException(status_code=503, detail="安全凭证存储不可用") from exc
        elif values["api_key_env"] and old_ref:
            # Explicitly switching from an encrypted credential to a legacy
            # environment reference must not leave the old ref taking
            # precedence in the adapter.
            _set_endpoint_value(endpoint, "credential_ref", "")
            retire_old_ref = True
        for name in (
            "name", "adapter", "base_url", "chat_path", "model", "api_key_env",
            "timeout_seconds", "note",
        ):
            setattr(endpoint, name, values[name])
        for name in ("priority", "routing_group"):
            _set_endpoint_value(endpoint, name, values[name])
        if new_ref:
            _set_endpoint_value(endpoint, "credential_ref", new_ref)
            endpoint.api_key_env = ""
        audit_from_request(db, request, "UPDATE", "AIModelEndpoint", endpoint.id, "更新 AI 端点")
        _commit_or_fail(db)
    except HTTPException:
        db.rollback()
        _cleanup_new_secret(store, new_ref)
        raise
    except Exception as exc:
        db.rollback()
        _cleanup_new_secret(store, new_ref)
        raise HTTPException(status_code=500, detail="AI 端点保存失败") from exc
    finally:
        db.close()
    if (new_ref or retire_old_ref) and old_ref and old_ref != new_ref:
        with suppress(Exception):
            store.delete(old_ref)
    return RedirectResponse("/ai-models", status_code=303)


@router.post("/ai-models/{endpoint_id}/rotate")
def ai_model_rotate(request: Request, endpoint_id: int, api_key: str = Form(...)):
    admin_only(request)
    try:
        secret = _validate_api_key_input(api_key)
        if not secret:
            raise ValueError("API Key 不能为空")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not _has_column("credential_ref"):
        raise HTTPException(status_code=503, detail="当前数据库尚未启用安全凭证存储")
    store = default_secret_store()
    new_ref: str | None = None
    old_ref = ""
    db = SessionLocal()
    try:
        endpoint = _get_endpoint(db, endpoint_id)
        if str(endpoint.adapter or "").strip().lower() == "mock":
            raise HTTPException(status_code=400, detail="旧 mock 端点不能在此页面轮换")
        old_ref = str(_endpoint_value(endpoint, "credential_ref", "") or "").strip()
        try:
            new_ref = store.rotate(old_ref, secret)
        except SecretStoreError as exc:
            raise HTTPException(status_code=503, detail="安全凭证存储不可用") from exc
        _set_endpoint_value(endpoint, "credential_ref", new_ref)
        endpoint.api_key_env = ""
        audit_from_request(db, request, "ROTATE", "AIModelEndpoint", endpoint.id, "轮换 AI 凭证")
        _commit_or_fail(db)
    except HTTPException:
        db.rollback()
        _cleanup_new_secret(store, new_ref)
        raise
    except Exception as exc:
        db.rollback()
        _cleanup_new_secret(store, new_ref)
        raise HTTPException(status_code=500, detail="AI 凭证轮换失败") from exc
    finally:
        db.close()
    if old_ref and old_ref != new_ref:
        with suppress(Exception):
            store.delete(old_ref)
    return RedirectResponse("/ai-models", status_code=303)


@router.post("/ai-models/{endpoint_id}/revoke")
def ai_model_revoke(request: Request, endpoint_id: int):
    admin_only(request)
    store = default_secret_store()
    ref = ""
    db = SessionLocal()
    try:
        endpoint = _get_endpoint(db, endpoint_id)
        if str(endpoint.adapter or "").strip().lower() == "mock":
            raise HTTPException(status_code=400, detail="旧 mock 端点不能在此页面管理")
        ref = str(_endpoint_value(endpoint, "credential_ref", "") or "").strip()
        # Commit the fail-closed endpoint state first.  If the vault operation
        # fails, a disabled endpoint is safe and the admin can retry revoke.
        endpoint.enabled = False
        audit_from_request(db, request, "REVOKE", "AIModelEndpoint", endpoint.id, "撤销 AI 凭证")
        _commit_or_fail(db)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="AI 凭证撤销失败") from exc
    finally:
        db.close()
    if ref:
        try:
            store.revoke(ref)
        except SecretStoreError as exc:
            raise HTTPException(status_code=503, detail="安全凭证存储不可用") from exc
    return RedirectResponse("/ai-models", status_code=303)


@router.post("/ai-models/{endpoint_id}/delete")
def ai_model_delete(request: Request, endpoint_id: int):
    admin_only(request)
    db = SessionLocal()
    old_ref = ""
    try:
        endpoint = _get_endpoint(db, endpoint_id)
        if str(endpoint.adapter or "").strip().lower() == "mock":
            raise HTTPException(status_code=400, detail="旧 mock 端点不能在此页面删除")
        endpoint_id_text = str(endpoint.id)
        has_audit = db.scalar(
            select(AuditLog.id).where(
                AuditLog.object_type == "AIModelEndpoint",
                AuditLog.object_id == endpoint_id_text,
            ).limit(1),
        )
        if has_audit:
            raise HTTPException(status_code=409, detail="已有审计引用，端点只能禁用或撤销凭证")
        has_jobs = db.scalar(
            select(AIReviewJob.id).where(AIReviewJob.endpoint_id == endpoint_id).limit(1),
        )
        if has_jobs:
            raise HTTPException(status_code=409, detail="已有审查任务引用，端点只能禁用")
        old_ref = str(_endpoint_value(endpoint, "credential_ref", "") or "").strip()
        db.delete(endpoint)
        _commit_or_fail(db)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="AI 端点删除失败") from exc
    finally:
        db.close()
    if old_ref:
        with suppress(Exception):
            default_secret_store().delete(old_ref)
    return RedirectResponse("/ai-models", status_code=303)


@router.post("/ai-models/{endpoint_id}/test", response_class=JSONResponse)
def ai_model_test(request: Request, endpoint_id: int):
    """Run a bounded, no-body probe without returning provider data."""
    admin_only(request)
    db = SessionLocal()
    try:
        endpoint = _get_endpoint(db, endpoint_id)
        if str(endpoint.adapter or "").strip().lower() == "mock":
            return JSONResponse({"status": "UNAVAILABLE", "detail": "旧 mock 端点已禁用"}, status_code=409)
        if not endpoint_is_allowed(endpoint):
            return JSONResponse({"status": "UNAVAILABLE", "detail": "端点不可用"}, status_code=409)
        try:
            base_url = validate_ai_endpoint_url(endpoint.base_url, endpoint.chat_path)
        except ValueError:
            return JSONResponse({"status": "UNAVAILABLE", "detail": "端点地址未通过安全校验"}, status_code=400)
        key = ""
        ref = str(_endpoint_value(endpoint, "credential_ref", "") or "").strip()
        try:
            if ref:
                key = default_secret_store().get(ref) or ""
            elif endpoint.api_key_env:
                env_name = resolve_secret_env_name(endpoint.api_key_env)
                key = os.getenv(env_name or "", "")
        except (SecretStoreError, ValueError):
            return JSONResponse({"status": "UNAVAILABLE", "detail": "凭证不可用"}, status_code=409)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        timeout = max(1, min(int(endpoint.timeout_seconds or 10), 30))
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                response = client.get(base_url, headers=headers)
        except Exception:
            return JSONResponse({"status": "UNAVAILABLE", "detail": "连接失败"}, status_code=502)
        if 200 <= response.status_code < 300:
            return JSONResponse({"status": "READY", "detail": "端点可连接"})
        if response.status_code in {401, 403}:
            return JSONResponse({"status": "AUTH_FAILED", "detail": "凭证未被端点接受"}, status_code=502)
        if response.status_code in {404, 405}:
            return JSONResponse({"status": "REACHABLE", "detail": "服务可连接，请核对接口地址"})
        return JSONResponse({"status": "UNAVAILABLE", "detail": "端点返回错误"}, status_code=502)
    finally:
        db.close()


@router.post("/ai-models/{endpoint_id}/toggle")
def ai_model_toggle(request: Request, endpoint_id: int):
    admin_only(request)
    db = SessionLocal()
    try:
        endpoint = _get_endpoint(db, endpoint_id)
        if str(endpoint.adapter or "").strip().lower() == "mock":
            raise HTTPException(status_code=400, detail="旧 mock 端点不能在此页面启用")
        endpoint.enabled = not endpoint.enabled
        audit_from_request(db, request, "UPDATE", "AIModelEndpoint", endpoint.id, "切换 AI 端点状态")
        _commit_or_fail(db)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="AI 端点状态更新失败") from exc
    finally:
        db.close()
    return RedirectResponse("/ai-models", status_code=303)
