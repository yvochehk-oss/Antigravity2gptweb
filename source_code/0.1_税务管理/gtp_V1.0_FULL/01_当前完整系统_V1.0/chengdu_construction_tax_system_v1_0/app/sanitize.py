"""V0.2: 强化脱敏：同时匹配 key 与 value 正则。"""
from __future__ import annotations

import re
from typing import Any

# key 黑名单
_BLOCKED_KEY_TOKENS = (
    "身份证", "银行卡号", "银行账号", "手机号", "联系电话", "联系人电话",
)

# value 正则
_ID_RE = re.compile(r"\b\d{17}[\dXx]\b")
_PHONE_RE = re.compile(r"\b1[3-9]\d{9}\b")
_BANK_RE = re.compile(r"\b\d{16,19}\b")


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if any(t in k for t in _BLOCKED_KEY_TOKENS):
                out[k] = "[REDACTED]"
            else:
                out[k] = _clean(v)
        return out
    if isinstance(value, list):
        return [_clean(x) for x in value]
    if isinstance(value, str):
        s = value
        s = _ID_RE.sub("[ID]", s)
        s = _PHONE_RE.sub("[PHONE]", s)
        s = _BANK_RE.sub("[BANK]", s)
        return s
    return value


def sanitize_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """统一脱敏入口。"""
    return _clean(ctx)  # type: ignore[return-value]