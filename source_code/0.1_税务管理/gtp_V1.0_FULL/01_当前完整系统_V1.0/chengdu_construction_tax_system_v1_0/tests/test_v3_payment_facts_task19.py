from __future__ import annotations
import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from sqlalchemy import create_engine,inspect,select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.calc.basis.cash_basis import LEGACY_CASHFLOW_FALLBACK_ALLOWED,require_canonical_payment_fact
from app.calc.penetration.group import cash_snapshot,summarize_cash
from app.models import CashFlow,Project
from app.v3_fact_models import Fact
from app.v3_party_models import InternalEntity,Party
from app.v3_payment_models import LegacyCashflowMap,PaymentFact
ROOT=Path(__file__).resolve().parents[1]
def _load(name,rel):
    spec=importlib.util.spec_from_file_location(name,ROOT/rel);module=importlib.util.module_from_spec(spec);assert spec and spec.loader;spec.loader.exec_module(module);return module
backfill=_load("task19_payment_backfill","scripts/v3/payment_backfill.py");group_builder=_load("task19_group_builder","scripts/v3/group_penetration.py")
def _parties(s):
    for code in ["A08","B01"]:
        e=s.scalar(select(InternalEntity).where(InternalEntity.canonical_code==code))
        if e is None:
            p=s.scalar(select(Party).where(Party.code==code))
            if p is None:p=Party(code=code,name=f"{code} Company",short_name=code,party_type="internal",active=True);s.add(p);s.flush()
            e=InternalEntity(party_id=p.id,canonical_code=code,business_role="company",legal_entity=True,active=True);s.add(e);s.flush()
    ext=s.scalar(select(Party).where(Party.code=="EXT-T19"))
    if ext is None:ext=Party(code="EXT-T19",name="Task19 External",short_name="EXT-T19",party_type="external",active=True);s.add(ext);s.flush()
    return s.scalar(select(InternalEntity).where(InternalEntity.canonical_code=="A08")),s.scalar(select(InternalEntity).where(InternalEntity.canonical_code=="B01")),ext
def _project(s):
    r=s.scalar(select(Project).where(Project.code=="T19-PILOT"))
    if r is None:r=Project(code="T19-PILOT",name="Task19 Pilot",city="Chengdu",contract_total=Decimal("1.00"),tax_method="general",entity_code="A08");s.add(r);s.flush()
    return r
def _payment(s,*,key,payer,payee,amount,dt):
    f=Fact(fact_type="PAYMENT",business_identity_key=key,version_no=1,is_current=True,validation_status="VALID");s.add(f);s.flush();r=PaymentFact(fact_id=f.id,payer_party_id=payer,payee_party_id=payee,transaction_date=dt,amount=Decimal(amount),currency="CNY",settlement_method="BANK_TRANSFER",payment_nature="OTHER");s.add(r);s.flush();return r
def test_task19_schema_and_cash_basis_contract(seeded_app,postgres_test_database_url):
    e=create_engine(postgres_test_database_url,future=True);i=inspect(e);assert {"payment_facts","legacy_cashflow_map"}.issubset(set(i.get_table_names()));cols={c["name"] for c in i.get_columns("payment_facts")};assert {"payer_party_id","payee_party_id","transaction_date","amount","payment_nature"}.issubset(cols);assert "project_id" not in cols and "cost_category" not in cols;assert LEGACY_CASHFLOW_FALLBACK_ALLOWED is False;assert require_canonical_payment_fact(payment_fact_available=True).basis.value=="CASH";e.dispose()
def test_payment_fact_rejects_same_party(seeded_app,postgres_test_database_url):
    e=create_engine(postgres_test_database_url,future=True)
    with Session(e) as s:
        a,_,_=_parties(s);s.commit();f=Fact(fact_type="PAYMENT",business_identity_key="T19|BAD|SAME",version_no=1,is_current=True,validation_status="VALID");s.add(f);s.flush();s.add(PaymentFact(fact_id=f.id,payer_party_id=a.party_id,payee_party_id=a.party_id,transaction_date=date(2025,10,1),amount=Decimal("1"),currency="CNY",settlement_method="UNKNOWN",payment_nature="OTHER"))
        with pytest.raises(IntegrityError):s.commit()
        s.rollback()
    e.dispose()
def test_legacy_out_and_in_map_directions(seeded_app,postgres_test_database_url):
    e=create_engine(postgres_test_database_url,future=True)
    with Session(e) as s:
        a,_,ext=_parties(s);p=_project(s);out=CashFlow(project_id=p.id,entity_code="A08",counterparty_code="EXT-T19",direction="out",amount=Decimal("255"),period="2025-10",transaction_date="2025-10-10",bank_reference="T19-OUT",source_fingerprint="T19-OUT-FP",note="pilot");inc=CashFlow(project_id=p.id,entity_code="A08",counterparty_code="EXT-T19",direction="in",amount=Decimal("350"),period="2025-10",transaction_date="2025-10-11",bank_reference="T19-IN",source_fingerprint="T19-IN-FP",note="pilot");s.add_all([out,inc]);s.commit()
        for legacy in (out,inc):plan=backfill.make_plan(s,legacy_cashflow_id=legacy.id);assert plan["status"]=="READY";assert backfill.build_one(s,legacy_cashflow_id=legacy.id,expected_plan_digest=plan["plan_digest"])["status"]=="MIGRATED";s.commit()
        op=s.get(PaymentFact,s.get(LegacyCashflowMap,out.id).payment_fact_id);ip=s.get(PaymentFact,s.get(LegacyCashflowMap,inc.id).payment_fact_id);assert (op.payer_party_id,op.payee_party_id)==(a.party_id,ext.id);assert (ip.payer_party_id,ip.payee_party_id)==(ext.id,a.party_id)
    e.dispose()
def test_missing_transaction_date_fails_closed_to_review(seeded_app,postgres_test_database_url):
    e=create_engine(postgres_test_database_url,future=True)
    with Session(e) as s:
        _parties(s);p=_project(s);r=CashFlow(project_id=p.id,entity_code="A08",counterparty_code="EXT-T19",direction="out",amount=Decimal("1"),period="2025-10",transaction_date=None,note="missing date");s.add(r);s.commit();plan=backfill.make_plan(s,legacy_cashflow_id=r.id);assert plan["status"]=="NEEDS_REVIEW";result=backfill.build_one(s,legacy_cashflow_id=r.id,expected_plan_digest=plan["plan_digest"]);s.commit();assert result["status"]=="NEEDS_REVIEW";assert s.get(LegacyCashflowMap,r.id).payment_fact_id is None;assert s.scalar(select(Fact).where(Fact.business_identity_key==f"PAYMENT|LEGACY_CASHFLOW|ROW|{r.id}")) is None
    e.dispose()
def test_payment_backfill_is_idempotent(seeded_app,postgres_test_database_url):
    e=create_engine(postgres_test_database_url,future=True)
    with Session(e) as s:
        _parties(s);p=_project(s);r=CashFlow(project_id=p.id,entity_code="A08",counterparty_code="EXT-T19",direction="out",amount=Decimal("2"),period="2025-11",transaction_date="2025-11-01",note="idem");s.add(r);s.commit();first=backfill.build_one(s,legacy_cashflow_id=r.id);s.commit();second=backfill.build_one(s,legacy_cashflow_id=r.id);s.commit();assert first["status"]=="MIGRATED" and second["status"]=="NO_CHANGE";assert first["payment_fact_id"]==second["payment_fact_id"]
    e.dispose()
def test_group_cash_uses_payment_fact_and_eliminates_internal_transfer(seeded_app,postgres_test_database_url):
    e=create_engine(postgres_test_database_url,future=True)
    with Session(e) as s:
        a,b,ext=_parties(s);_payment(s,key="T19|CASH|IN",payer=ext.id,payee=a.party_id,amount="350",dt=date(2025,12,2));_payment(s,key="T19|CASH|OUT",payer=a.party_id,payee=ext.id,amount="255",dt=date(2025,12,3));_payment(s,key="T19|CASH|INT",payer=a.party_id,payee=b.party_id,amount="300",dt=date(2025,12,4));s.commit();snap=cash_snapshot(s,period="2025-12");tot=summarize_cash(snap);assert tot=={"cash_inflow":Decimal("350.00"),"cash_outflow":Decimal("255.00"),"net_cash":Decimal("95.00")};assert sum(Decimal(r["amount"]) for r in snap["components"] if r["component_type"]=="INTERNAL_CASH_ELIMINATION")==Decimal("300.00");built=group_builder.build_one(s,anchor_entity="A08",period="2025-12",basis="CASH",created_by="task19-test");s.commit();assert built["status"] in {"BUILT","NO_CHANGE"}
    e.dispose()
