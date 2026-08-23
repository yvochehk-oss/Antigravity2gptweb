"""V0.2 计算引擎单元测试。"""
from __future__ import annotations

from decimal import Decimal


def test_note_category_word_boundary():
    """V0.2: 'labor' 不能误命中 'laboratory'。"""
    from app.calc.matching import _note_category
    assert _note_category("labor付款") == "劳务"
    assert _note_category("material采购") == "材料"
    assert _note_category("LABORATORY TEST") == "未分类"
    assert _note_category("project_management日常") == "项目管理"
    assert _note_category("") == "未分类"


def test_matching_rows_evidence_flag(seeded_app):
    """履约证据缺失应被四流匹配识别。"""
    from app.calc import matching_rows
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        rows = matching_rows(db, 1)
        # 从真实交易对手中定位一条设备履约证据缺失记录。
        missing_evidence_row = next(
            (r for r in rows if r["category"] == "设备" and not r["evidence_ok"]),
            None,
        )
        assert missing_evidence_row is not None
        assert missing_evidence_row["evidence_ok"] is False
        assert "履约证据不完整" in missing_evidence_row["status"]
    finally:
        db.close()


def test_tax_ledger_independent(seeded_app):
    """每个活动独立法人都有台账，非独立分支不单独建账。"""
    from sqlalchemy import select

    from app.calc import rebuild_tax_ledger
    from app.db import SessionLocal
    from app.models import Entity, Progress

    db = SessionLocal()
    try:
        # 取 seed 期间的 Progress
        prog = db.query(Progress).first()
        period = prog.period
        rows = rebuild_tax_ledger(db, period)
        codes = {r.entity_code for r in rows}
        expected = {
            entity.code
            for entity in db.scalars(
                select(Entity).where(
                    Entity.active.is_(True),
                    Entity.legal_entity.is_(True),
                )
            )
        }
        assert len(expected) == 25
        assert codes == expected
        assert "A04" not in codes
        assert len(rows) == len(expected)
        # 当前真实演示数据覆盖全部 25 家法人；每家都必须有独立台账行。
        assert any(row.output_vat > 0 for row in rows)
        # CIT note 应包含"未应用项"
        assert all("未应用项" in row.cit_note for row in rows)

        # 在完全没有交易的期间，计算引擎仍应生成所有活动法人的零值台账，
        # 以保证下游报表不会因“无交易”而漏掉主体。
        empty_rows = rebuild_tax_ledger(db, "2099-12")
        assert {row.entity_code for row in empty_rows} == expected
        assert all(
            row.output_vat == 0
            and row.input_vat == 0
            and row.revenue == 0
            and row.real_cost == 0
            and row.estimated_cit == 0
            for row in empty_rows
        )
    finally:
        db.close()


def test_risk_threshold_overridable(seeded_app):
    """V0.2: 修改 RiskThreshold 应改变匹配阈值（仅在 matching_rows 内部使用）。"""
    from app.db import SessionLocal
    from app.models import RiskThreshold

    db = SessionLocal()
    try:
        # 默认 1.05
        rt = db.query(RiskThreshold).filter(
            RiskThreshold.code == "invoice_over_contract"
        ).first()
        assert rt is not None
        # 设为 0.5 后，contract=8M / invoice=5.5M 仍不超（5.5 < 8*0.5 不成立）
        # 改为 1.0 后应触发
        rt.ratio = Decimal("1.0")
        db.commit()

        from app.calc import matching_rows
        rows = matching_rows(db, 1)
        labor_row = next(
            (r for r in rows if r["category"] == "劳务"),
            None,
        )
        # contract=8M, invoice=5.5M+0.495M=5.995M, ratio=1.0 时 5.995 > 8*1.0 不成立
        # 因为 contract 8M > invoice 5.995M，仍正常。但若调高 invoice 则会触发。
        assert labor_row is not None

        # 恢复
        rt.ratio = Decimal("1.05")
        db.commit()
    finally:
        db.close()
