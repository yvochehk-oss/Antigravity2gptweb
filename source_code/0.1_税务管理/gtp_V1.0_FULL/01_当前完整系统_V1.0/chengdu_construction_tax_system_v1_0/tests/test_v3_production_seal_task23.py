from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.cutover.finalization as finalization
from app.cutover.finalization import ProductionSealEvidence
from app.cutover.writer import CutoverError


class FakeDB:
    def __init__(self) -> None:
        self.added=[]; self.commits=0; self.flushes=0
    def get(self, model, key): return None
    def add(self, value) -> None: self.added.append(value)
    def commit(self) -> None: self.commits += 1
    def flush(self) -> None: self.flushes += 1


def _state(**overrides):
    values={"writer_mode":"V3_PRIMARY","legacy_write_enabled":False,"new_fact_write_enabled":True,"legacy_frozen":True,"new_fact_read_mode":"PRIMARY","rag_source":"CANONICAL_FACTS","updated_by":"operator","updated_at":None}
    values.update(overrides); return SimpleNamespace(**values)


def _clean(): return ProductionSealEvidence(0,0,0,0)


def test_production_seal_evidence_clean_only_when_all_zero():
    assert _clean().clean is True
    assert ProductionSealEvidence(1,0,0,0).clean is False


def test_finalization_requires_non_empty_actor():
    with pytest.raises(CutoverError,match="non-empty actor"):
        finalization.finalize_v3_production_cutover(FakeDB(),actor=" ")


def test_finalization_requires_strict_final_route(monkeypatch):
    db=FakeDB(); monkeypatch.setattr(finalization,"get_cutover_state",lambda db,scope,for_update:_state(rag_source="LEGACY"))
    with pytest.raises(CutoverError,match="strict V3_PRIMARY"):
        finalization.finalize_v3_production_cutover(db,actor="operator")


def test_finalization_blocks_dirty_integrity_evidence(monkeypatch):
    db=FakeDB(); monkeypatch.setattr(finalization,"get_cutover_state",lambda db,scope,for_update:_state())
    monkeypatch.setattr(finalization,"collect_production_seal_evidence",lambda db:ProductionSealEvidence(0,0,0,1))
    with pytest.raises(CutoverError,match="integrity evidence"):
        finalization.finalize_v3_production_cutover(db,actor="operator")


def test_finalization_writes_single_seal(monkeypatch):
    db=FakeDB(); monkeypatch.setattr(finalization,"get_cutover_state",lambda db,scope,for_update:_state())
    monkeypatch.setattr(finalization,"collect_production_seal_evidence",lambda db:_clean())
    seal=finalization.finalize_v3_production_cutover(db,actor="operator")
    assert seal.scope=="GLOBAL" and seal.finalized_by=="operator"
    assert seal.evidence_snapshot["canonical_fact_orphan_count"]==0
    assert seal.state_snapshot["rag_source"]=="CANONICAL_FACTS"
    assert db.added==[seal] and db.commits==1 and db.flushes==0


def test_finalization_can_flush_without_committing_for_gate(monkeypatch):
    db=FakeDB(); monkeypatch.setattr(finalization,"get_cutover_state",lambda db,scope,for_update:_state())
    monkeypatch.setattr(finalization,"collect_production_seal_evidence",lambda db:_clean())
    finalization.finalize_v3_production_cutover(db,actor="gate:S23",commit=False)
    assert db.commits==0 and db.flushes==1


def test_finalization_is_idempotent(monkeypatch):
    existing=SimpleNamespace(scope="GLOBAL"); db=FakeDB(); db.get=lambda model,key:existing
    monkeypatch.setattr(finalization,"get_cutover_state",lambda *a,**k:pytest.fail("should not re-evaluate"))
    assert finalization.finalize_v3_production_cutover(db,actor="operator") is existing
    assert db.commits==0


def test_strict_state_rejects_reader_rollback_pair():
    assert finalization._strict_final_state(_state(new_fact_read_mode="SHADOW",rag_source="LEGACY")) is False


def test_strict_state_rejects_legacy_writer_enabled():
    assert finalization._strict_final_state(_state(legacy_write_enabled=True,legacy_frozen=False)) is False


def test_strict_state_accepts_task22_primary_state():
    assert finalization._strict_final_state(_state()) is True
