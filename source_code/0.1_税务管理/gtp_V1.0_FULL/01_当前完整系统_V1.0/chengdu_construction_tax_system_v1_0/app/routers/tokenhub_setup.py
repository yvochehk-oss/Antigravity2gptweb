"""用户级 TokenHub（腾讯云大模型知识引擎）API Key 配置。

设计目标
---------
为非管理员用户提供一条**单文件、单系统级**的"插钥匙"通道：

1. 任何已登录用户都可以打开设置页；
2. 用户把从 ``https://console.cloud.tencent.com/`` 拿到的 API Key 粘贴进来；
3. 后端立即加密落盘（AES-256-GCM，参见 ``app.services.secret_store``）；
4. 同时在 ``ai_model_endpoints`` 表里**单一**地维护一个名为
   ``tokenhub-default`` 的端点，作为系统级 TokenHub 入口，避免多用户
   各自配置导致路由混乱；
5. 测试连接只做最小代价的 ``/v1/models`` 列表请求，确认 Key 可用后
   再落盘，不消耗模型配额；
6. 撤销、轮换、查看状态都通过统一接口，避免泄露明文 Key。

该模块不修改 ``app.routers.models`` 的管理员专用逻辑，也不引入新的
全局状态；它只是把 TokenHub 这一**单实例**端点的 CRUD 简化出来。
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from ..audit import audit_from_request
from ..db import SessionLocal
from ..dependencies import require_login
from ..models import AIModelEndpoint, User
from ..services.secret_store import (
    SecretStoreError,
    default_secret_store,
    resolve_secret,
)

router = APIRouter(tags=["TokenHub 用户级配置"])

# ---------------------------------------------------------------------------
# 单一 TokenHub 端点的固定标识：与管理员模型管理共享同一张表，但本模块
# 只触碰这一行，避免与人工创建的端点混淆。
# ---------------------------------------------------------------------------
TOKENHUB_ENDPOINT_NAME = "tokenhub-default"
TOKENHUB_DEFAULT_BASE_URL = "https://tokenhub.tencentmaas.com/v1"
TOKENHUB_DEFAULT_MODEL = "deepseek-v4-flash-202605"
TOKENHUB_DEFAULT_CHAT_PATH = "/chat/completions"
TOKENHUB_ROUTING_GROUP = "user-default"

# 腾讯云官方 API Key 通常以 ``sk-`` 前缀；这里只做**格式**校验，匹配不
# 上仍允许用户提交（部分新签发格式不固定前缀），但会给出明确提示。
_API_KEY_HINT = re.compile(r"^sk-[A-Za-z0-9_-]{16,}$")


def _is_valid_key_format(value: str) -> tuple[bool, str]:
    if not value or not value.strip():
        return False, "API Key 不能为空。"
    candidate = value.strip()
    if len(candidate) > 4096:
        return False, "API Key 长度超出 4096 字符限制。"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in candidate):
        return False, "API Key 包含控制字符。"
    if _API_KEY_HINT.fullmatch(candidate):
        return True, ""
    return True, "API Key 不以 'sk-' 开头，将按原样提交；如确认无误请继续。"


def _model_value(value: str | None) -> str:
    candidate = (value or "").strip()
    return candidate or TOKENHUB_DEFAULT_MODEL


def _resolve_api_key_from_env() -> str | None:
    """在没有用户提交 Key 的情况下读取服务端环境变量作为兜底。"""
    for env_name in ("TENCENT_HUNYUAN_API_KEY", "DEEPSEEK_API_KEY"):
        candidate = os.getenv(env_name, "").strip()
        if candidate:
            return candidate
    return None


async def _probe_tokenhub(api_key: str, base_url: str, timeout: float = 8.0) -> dict[str, Any]:
    """用最小代价调用 TokenHub ``/models`` 接口验证 Key 是否可用。

    仅检测：
    - HTTP 200 → Key 通过认证；
    - 401 / 403 / 40001 / 401008 → Key 无效或未开通；
    - 网络层失败 → 标记为不可达。
    """
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "reachable": False,
            "status_code": 0,
            "error": f"无法连接到 TokenHub：{exc.__class__.__name__}",
        }

    status = response.status_code
    if status == 200:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        models: list[str] = []
        if isinstance(payload, dict):
            raw_models = payload.get("data")
            if isinstance(raw_models, list):
                for entry in raw_models[:5]:
                    if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                        models.append(entry["id"])
        return {
            "ok": True,
            "reachable": True,
            "status_code": 200,
            "error": None,
            "models": models,
        }

    snippet = response.text[:200] if response.content else ""
    if status in (401, 403):
        return {
            "ok": False,
            "reachable": True,
            "status_code": status,
            "error": "TokenHub 鉴权失败（HTTP 401/403），请检查 API Key 是否正确。",
        }
    if status == 429:
        return {
            "ok": False,
            "reachable": True,
            "status_code": status,
            "error": "TokenHub 返回限流（HTTP 429），请稍后重试或更换 Key。",
        }
    if status in (402,):
        return {
            "ok": False,
            "reachable": True,
            "status_code": status,
            "error": "TokenHub 提示余额耗尽（HTTP 402），请前往腾讯云控制台充值。",
        }
    return {
        "ok": False,
        "reachable": True,
        "status_code": status,
        "error": f"TokenHub 探测失败：HTTP {status} {snippet}".strip(),
    }


def _existing_endpoint(db) -> AIModelEndpoint | None:
    return db.execute(
        select(AIModelEndpoint).where(AIModelEndpoint.name == TOKENHUB_ENDPOINT_NAME)
    ).scalars().first()


def _serialize(endpoint: AIModelEndpoint | None, *, has_user_key: bool) -> dict[str, Any]:
    env_fallback = bool(_resolve_api_key_from_env())
    if endpoint is None:
        return {
            "configured": False,
            "has_user_key": False,
            "env_fallback": env_fallback,
            "endpoint": None,
        }
    return {
        "configured": True,
        "has_user_key": has_user_key,
        "env_fallback": env_fallback,
        "endpoint": {
            "id": endpoint.id,
            "name": endpoint.name,
            "base_url": endpoint.base_url,
            "chat_path": endpoint.chat_path,
            "model": endpoint.model,
            "enabled": bool(endpoint.enabled),
            "routing_group": endpoint.routing_group,
            "priority": int(endpoint.priority or 0),
            "updated_at": endpoint.updated_at.isoformat() if getattr(endpoint, "updated_at", None) else None,
            "note": endpoint.note or "",
        },
    }


@router.get("/tokenhub-setup/status")
def tokenhub_setup_status(_user: User = Depends(require_login)) -> JSONResponse:
    """读取当前用户级 TokenHub 端点状态（不返回 Key 明文）。"""
    db = SessionLocal()
    try:
        endpoint = _existing_endpoint(db)
        has_user_key = False
        if endpoint is not None:
            ref = (endpoint.credential_ref or "").strip()
            if ref:
                has_user_key = resolve_secret(ref) is not None
        return JSONResponse(_serialize(endpoint, has_user_key=has_user_key))
    finally:
        db.close()


@router.post("/tokenhub-setup/test")
async def tokenhub_setup_test(
    request: Request,
    payload: dict[str, Any],
    _user: User = Depends(require_login),
) -> JSONResponse:
    """测试用户在表单里粘贴的 API Key 是否能连通 TokenHub。

    该接口**不写入**任何存储，仅返回探测结果。
    """
    api_key = str(payload.get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="请输入 API Key")
    ok, message = _is_valid_key_format(api_key)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    result = await _probe_tokenhub(api_key, TOKENHUB_DEFAULT_BASE_URL)
    response_body = {**result, "format_warning": message if message else None}
    return JSONResponse(response_body)


@router.post("/tokenhub-setup/save")
async def tokenhub_setup_save(
    request: Request,
    payload: dict[str, Any],
    _user: User = Depends(require_login),
) -> JSONResponse:
    """保存用户提交的 API Key，创建或更新单一 TokenHub 端点。"""
    api_key = str(payload.get("api_key") or "").strip()
    model = _model_value(str(payload.get("model") or ""))
    if not api_key:
        raise HTTPException(status_code=400, detail="请输入 API Key")
    ok, message = _is_valid_key_format(api_key)
    if not ok:
        raise HTTPException(status_code=400, detail=message)

    probe = await _probe_tokenhub(api_key, TOKENHUB_DEFAULT_BASE_URL)
    if not probe.get("ok"):
        detail = probe.get("error") or "TokenHub 连接测试失败"
        raise HTTPException(status_code=400, detail=detail)

    store = default_secret_store()
    new_ref: str | None = None
    old_ref_to_revoke: str | None = None
    db = SessionLocal()
    try:
        existing = _existing_endpoint(db)
        if existing is not None:
            old_ref_to_revoke = (existing.credential_ref or "").strip() or None
        try:
            new_ref = store.put(api_key)
        except SecretStoreError as exc:
            raise HTTPException(status_code=503, detail="安全凭证存储不可用") from exc

        if existing is None:
            endpoint = AIModelEndpoint(
                name=TOKENHUB_ENDPOINT_NAME,
                adapter="openai_compatible",
                base_url=TOKENHUB_DEFAULT_BASE_URL,
                chat_path=TOKENHUB_DEFAULT_CHAT_PATH,
                model=model,
                api_key_env="",
                enabled=True,
                timeout_seconds=60,
                note="由用户在 TokenHub 设置向导中配置",
                routing_group=TOKENHUB_ROUTING_GROUP,
                priority=10,
            )
            if hasattr(endpoint, "credential_ref"):
                setattr(endpoint, "credential_ref", new_ref)
            db.add(endpoint)
        else:
            existing.base_url = TOKENHUB_DEFAULT_BASE_URL
            existing.chat_path = TOKENHUB_DEFAULT_CHAT_PATH
            existing.model = model
            existing.api_key_env = ""
            existing.enabled = True
            existing.note = "由用户在 TokenHub 设置向导中更新"
            if hasattr(existing, "credential_ref"):
                setattr(existing, "credential_ref", new_ref)
            endpoint = existing

        audit_from_request(
            db, request, "UPSERT", "AIModelEndpoint",
            getattr(endpoint, "id", 0), "用户保存 TokenHub API Key",
        )
        db.commit()
    except HTTPException:
        db.rollback()
        if new_ref:
            try:
                default_secret_store().revoke(new_ref)
            except SecretStoreError:
                pass
        raise
    except Exception as exc:
        db.rollback()
        if new_ref:
            try:
                default_secret_store().revoke(new_ref)
            except SecretStoreError:
                pass
        raise HTTPException(status_code=500, detail="TokenHub 配置保存失败") from exc
    finally:
        db.close()

    if old_ref_to_revoke and old_ref_to_revoke != new_ref:
        try:
            default_secret_store().revoke(old_ref_to_revoke)
        except SecretStoreError:
            pass

    return JSONResponse({
        "ok": True,
        "endpoint": {
            "id": endpoint.id,
            "name": endpoint.name,
            "base_url": endpoint.base_url,
            "chat_path": endpoint.chat_path,
            "model": endpoint.model,
            "enabled": bool(endpoint.enabled),
            "routing_group": endpoint.routing_group,
            "priority": int(endpoint.priority or 0),
            "note": endpoint.note or "",
        },
        "models": probe.get("models") or [],
    })


@router.post("/tokenhub-setup/revoke")
def tokenhub_setup_revoke(
    request: Request,
    _user: User = Depends(require_login),
) -> JSONResponse:
    """撤销当前 TokenHub 端点保存的 API Key，并禁用该端点。"""
    store = default_secret_store()
    db = SessionLocal()
    try:
        endpoint = _existing_endpoint(db)
        if endpoint is None:
            return JSONResponse({"ok": True, "message": "当前未配置 TokenHub 端点。"})
        ref = (endpoint.credential_ref or "").strip()
        if ref:
            try:
                store.revoke(ref)
            except SecretStoreError as exc:
                raise HTTPException(status_code=503, detail="凭证撤销失败") from exc
        if hasattr(endpoint, "credential_ref"):
            setattr(endpoint, "credential_ref", "")
        endpoint.enabled = False
        endpoint.note = "已由用户撤销 TokenHub API Key"
        audit_from_request(
            db, request, "REVOKE", "AIModelEndpoint",
            endpoint.id, "用户撤销 TokenHub API Key",
        )
        db.commit()
        return JSONResponse({"ok": True, "message": "已撤销 TokenHub API Key。"})
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="TokenHub 撤销失败") from exc
    finally:
        db.close()


__all__ = ["router"]
