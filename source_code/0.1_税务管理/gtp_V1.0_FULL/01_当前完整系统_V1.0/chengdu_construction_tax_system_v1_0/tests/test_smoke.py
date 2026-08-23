"""V0.2 冒烟测试：建表 + seed + 经营驾驶舱数字。"""
from __future__ import annotations

from decimal import Decimal


def test_smoke(seeded_app):
    from app.calc import consolidated
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        d = consolidated(db)
        assert d["rows"], "no projects"
        assert d["revenue"] > 0, "revenue should be > 0"
        assert d["cost"] > 0, "real cost should be > 0"
        # 验证包含宜宾示范项目基准指标：确认收入 23,000,000；真实底层成本 22,000,000
        p1 = next((r for r in d["rows"] if "宜宾" in r["project"].name), None)
        assert p1 is not None
        assert p1["revenue"] == Decimal("23000000")
        assert p1["real_cost"] == Decimal("22000000")
        assert p1["revenue"] - p1["real_cost"] == Decimal("1000000")
        # 整体项目库汇总收入和成本大于0
        assert d["revenue"] >= Decimal("23000000")
        assert d["cost"] >= Decimal("22000000")
    finally:
        db.close()