from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.ai.context as context
import app.cutover.reader as reader
from app.cutover.writer import CutoverError


class FakeDB:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def _state(**overrides):
    values = {"writer_mode":"V3_PRIMARY","legacy_write_enabled":False,"new_fact_write_enabled":True,"legacy_frozen":True,"new_fact_read_mode":"SHADOW","rag_source":"LEGACY","updated_by":"test","updated_at":None}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_reader_route_accepts_legacy_pair(monkeypatch):
    monkeypatch.setattr(reader,"get_cutover_state",lambda db:_state())
    route=reader.get_reader_route(FakeDB())
    assert route.mode=="SHADOW" and route.rag_source=="LEGACY" and route.is_canonical is False


def test_reader_route_accepts_canonical_pair(monkeypatch):
    monkeypatch.setattr(reader,"get_cutover_state",lambda db:_state(new_fact_read_mode="PRIMARY",rag_source="CANONICAL_FACTS"))
    assert reader.get_reader_route(FakeDB()).is_canonical is True


def test_reader_route_rejects_split_state(monkeypatch):
    monkeypatch.setattr(reader,"get_cutover_state",lambda db:_state(new_fact_read_mode="PRIMARY",rag_source="LEGACY"))
    with pytest.raises(CutoverError,match="split state"): reader.get_reader_route(FakeDB())


def test_primary_cutover_requires_v3_primary_writer(monkeypatch):
    state=_state(writer_mode="DUAL_WRITE",legacy_write_enabled=True,legacy_frozen=False)
    monkeypatch.setattr(reader,"get_cutover_state",lambda db,for_update=False:state)
    with pytest.raises(CutoverError,match="V3_PRIMARY"): reader.transition_reader_rag_to_primary(FakeDB(),actor="operator")


def test_primary_cutover_rejects_unresolved_production_diffs(monkeypatch):
    state=_state(); monkeypatch.setattr(reader,"get_cutover_state",lambda db,for_update=False:state); monkeypatch.setattr(reader,"unresolved_production_diff_count",lambda db:2)
    with pytest.raises(CutoverError,match="2 unresolved"): reader.transition_reader_rag_to_primary(FakeDB(),actor="operator")


def test_primary_cutover_switches_reader_rag_only(monkeypatch):
    state=_state(); db=FakeDB(); monkeypatch.setattr(reader,"get_cutover_state",lambda db,for_update=False:state); monkeypatch.setattr(reader,"unresolved_production_diff_count",lambda db:0)
    reader.transition_reader_rag_to_primary(db,actor="operator")
    assert (state.new_fact_read_mode,state.rag_source)==("PRIMARY","CANONICAL_FACTS")
    assert (state.writer_mode,state.legacy_write_enabled,state.new_fact_write_enabled,state.legacy_frozen)==("V3_PRIMARY",False,True,True)
    assert db.commits==1


def test_reader_rollback_never_unfreezes_writer(monkeypatch):
    state=_state(new_fact_read_mode="PRIMARY",rag_source="CANONICAL_FACTS"); db=FakeDB(); monkeypatch.setattr(reader,"get_cutover_state",lambda db,for_update=False:state)
    reader.rollback_reader_rag_to_legacy(db,actor="operator")
    assert (state.new_fact_read_mode,state.rag_source)==("SHADOW","LEGACY")
    assert (state.writer_mode,state.legacy_write_enabled,state.new_fact_write_enabled,state.legacy_frozen)==("V3_PRIMARY",False,True,True)


def test_context_legacy_route_marks_source(monkeypatch):
    monkeypatch.setattr(context,"get_reader_route",lambda db:reader.ReaderRoute("SHADOW","LEGACY")); monkeypatch.setattr(context,"_build_legacy_context",lambda db,pid,scope:{"scope":scope})
    payload=context.build_context(FakeDB(),1,"invoice")
    assert payload["data_source"]=="LEGACY"


def test_context_canonical_route_fails_closed_without_legacy_fallback(monkeypatch):
    monkeypatch.setattr(context,"get_reader_route",lambda db:reader.ReaderRoute("PRIMARY","CANONICAL_FACTS"))
    monkeypatch.setattr(context,"build_canonical_context_native",lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError("canonical read failed")))
    called={"legacy":False}
    monkeypatch.setattr(context,"_build_legacy_context",lambda *args,**kwargs:called.update(legacy=True))
    with pytest.raises(RuntimeError,match="canonical read failed"): context.build_context(FakeDB(),1,"invoice")
    assert called["legacy"] is False


def test_context_canonical_route_is_native_v3(monkeypatch):
    monkeypatch.setattr(context,"get_reader_route",lambda db:reader.ReaderRoute("PRIMARY","CANONICAL_FACTS"))
    monkeypatch.setattr(context,"build_canonical_context_native",lambda db,pid,scope:{"scope":scope,"invoices":[{"fact_id":22,"invoice_no":"V3-22"}]})
    called={"legacy":False}
    monkeypatch.setattr(context,"_build_legacy_context",lambda *args,**kwargs:called.update(legacy=True))
    payload=context.build_context(FakeDB(),1,"invoice")
    assert payload["data_source"]=="CANONICAL_FACTS" and payload["invoices"]==[{"fact_id":22,"invoice_no":"V3-22"}]
    assert called["legacy"] is False
