"""V0.2 脱敏测试。"""
from __future__ import annotations


def test_sanitize_keyword_redaction():
    from app.sanitize import sanitize_context
    ctx = {"身份证号": "110101199001011234", "姓名": "张三"}
    out = sanitize_context(ctx)
    assert out["身份证号"] == "[REDACTED]"
    assert out["姓名"] == "张三"


def test_sanitize_value_redaction():
    from app.sanitize import sanitize_context
    ctx = {
        "name": "李四",
        "phone": "13800138000",
        "note": "客户 110101199001011234 在 13800138000 咨询",
        "id_card_in_text": "no id here",
    }
    out = sanitize_context(ctx)
    assert out["phone"] == "[PHONE]"
    assert "[ID]" in out["note"]
    assert "[PHONE]" in out["note"]
    assert out["id_card_in_text"] == "no id here"


def test_sanitize_nested():
    from app.sanitize import sanitize_context
    ctx = {"data": {"银行卡号": "62226000123456789"}}
    out = sanitize_context(ctx)
    assert out["data"]["银行卡号"] == "[REDACTED]"