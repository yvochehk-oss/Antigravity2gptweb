"""V0.2 冒烟测试：建表 + seed + 经营驾驶舱数字。"""
from __future__ import annotations

from decimal import Decimal


def test_smoke(seeded_app):
    from app.db import SessionLocal
    from app.calc import consolidated

    db = SessionLocal()
    try:
        d = consolidated(db)
        assert d["rows"], "no projects"
        assert d["revenue"] > 0, "revenue should be > 0"
        assert d["cost"] > 0, "real cost should be > 0"
        # V0.2 演示项目应确认收入 23,000,000；真实底层成本 22,000,000
        # （B工资社保 4.35M + C外采 7.55M + D折维燃 1.25M + A项目部 1.2M
        #  + 甲1.1M + 乙0.65M + 丙4.3M + 丁1.6M = 22,000,000）
        assert Decimal("23000000") in [d["revenue"]]
        assert d["cost"] == Decimal("22000000")
        # 项目利润 = 23,000,000 - 22,000,000 = 1,000,000
        assert d["profit"] == Decimal("1000000")
    finally:
        db.close()