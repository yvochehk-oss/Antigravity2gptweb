#!/usr/bin/env python3
"""Task19 legacy CashFlow -> canonical PaymentFact PLAN/APPLY pilot bridge."""
from __future__ import annotations
import argparse, json, os
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from app.domain.facts.payment import PaymentCandidate, PaymentFactError, canonical_hash, legacy_direction_parties, money, validate_candidate
from app.models import CashFlow
from app.v3_fact_models import Fact
from app.v3_party_models import InternalEntity, Party
from app.v3_payment_models import LegacyCashflowMap, PaymentFact

EXPECTED_HEAD = "90_v3_payment_facts"
PLAN_KIND = "V3_TASK19_PAYMENT_BACKFILL_PLAN"

def _database_url() -> str:
    value=os.getenv("DATABASE_URL","").strip()
    if not value: raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql","postgres"}: raise SystemExit("Task19 Payment backfill is PostgreSQL-only")
    return value

def _head(session: Session) -> str:
    return str(session.connection().exec_driver_sql("SELECT version_num FROM alembic_version_tax").scalar_one())

def _resolve_party(session: Session, code: str):
    code=(code or "").strip()
    if not code: return None,"blank party code"
    candidates=set()
    party=session.scalar(select(Party).where(Party.code==code))
    if party is not None: candidates.add(int(party.id))
    internal=session.scalar(select(InternalEntity).where(InternalEntity.canonical_code==code))
    if internal is not None: candidates.add(int(internal.party_id))
    candidates.update(int(v) for v in session.execute(text("SELECT party_id FROM external_parties WHERE code=:code AND party_id IS NOT NULL"),{"code":code}).scalars().all())
    if len(candidates)==1: return next(iter(candidates)),None
    if not candidates: return None,f"party code {code!r} is unresolved"
    return None,f"party code {code!r} resolves ambiguously to {sorted(candidates)}"

def _parse_date(value):
    if not value or not str(value).strip(): return None,"transaction_date missing; legacy period must not be promoted to a payment date"
    try: return date.fromisoformat(str(value).strip()),None
    except ValueError: return None,f"invalid transaction_date {value!r}"

def _legacy_snapshot(row):
    return {"id":row.id,"project_id":row.project_id,"entity_code":row.entity_code,"counterparty_code":row.counterparty_code,"direction":row.direction,"amount":str(row.amount),"period":row.period,"transaction_date":row.transaction_date,"bank_reference":row.bank_reference,"source_fingerprint":row.source_fingerprint,"note":row.note}

def make_plan(session: Session, *, legacy_cashflow_id: int) -> dict[str, Any]:
    head = _head(session)
    if not (head == EXPECTED_HEAD or head >= EXPECTED_HEAD): raise ValueError(f"formal DB head must be at least {EXPECTED_HEAD}, got {head}")

    row=session.get(CashFlow,legacy_cashflow_id)
    if row is None: raise ValueError(f"legacy cashflow {legacy_cashflow_id} not found")
    entity,eerr=_resolve_party(session,row.entity_code); counterparty,cerr=_resolve_party(session,row.counterparty_code); transaction_date,derr=_parse_date(row.transaction_date)
    blockers=[x for x in (eerr,cerr,derr) if x]
    if row.source_fingerprint:
        duplicate_ids=session.execute(select(CashFlow.id).where(CashFlow.source_fingerprint==row.source_fingerprint,CashFlow.id!=row.id).order_by(CashFlow.id)).scalars().all()
        if duplicate_ids: blockers.append(f"duplicate legacy source_fingerprint {row.source_fingerprint!r} also appears on rows {list(duplicate_ids)}")
    candidate=None
    if not blockers:
        try:
            payer,payee=legacy_direction_parties(direction=row.direction,entity_party_id=int(entity),counterparty_party_id=int(counterparty))
            payment=validate_candidate(PaymentCandidate(payer_party_id=payer,payee_party_id=payee,transaction_date=transaction_date,amount=money(row.amount),currency="CNY",bank_reference=row.bank_reference or None,settlement_method="BANK_TRANSFER" if row.bank_reference else "UNKNOWN",payment_nature="OTHER"))
            candidate=payment.as_dict()
        except (PaymentFactError,ValueError) as exc: blockers.append(str(exc))
    snap=_legacy_snapshot(row)
    core={"kind":PLAN_KIND,"version":1,"legacy_cashflow":snap,"legacy_snapshot_sha256":canonical_hash(snap),"candidate":candidate,"status":"READY" if not blockers else "NEEDS_REVIEW","blockers":sorted(set(blockers))}
    return {**core,"plan_digest":canonical_hash(core)}

def build_one(session: Session, *, legacy_cashflow_id: int, expected_plan_digest: str|None=None) -> dict[str, Any]:
    head = _head(session)
    if not (head == EXPECTED_HEAD or head >= EXPECTED_HEAD): raise ValueError(f"formal DB head must be at least {EXPECTED_HEAD}, got {head}")

    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),{"scope":f"PAYMENT_BACKFILL:{legacy_cashflow_id}"})
    existing=session.get(LegacyCashflowMap,legacy_cashflow_id)
    if existing is not None: return {"status":"NO_CHANGE","legacy_cashflow_id":legacy_cashflow_id,"map_status":existing.status,"payment_fact_id":existing.payment_fact_id}
    plan=make_plan(session,legacy_cashflow_id=legacy_cashflow_id)
    if expected_plan_digest is not None and plan["plan_digest"]!=expected_plan_digest: raise ValueError("stale Task19 plan: legacy CashFlow snapshot changed after PLAN review")
    if plan["status"]!="READY":
        session.add(LegacyCashflowMap(legacy_cashflow_id=legacy_cashflow_id,payment_fact_id=None,status="NEEDS_REVIEW",reason="; ".join(plan["blockers"])))
        session.flush(); return {"status":"NEEDS_REVIEW","legacy_cashflow_id":legacy_cashflow_id,"blockers":plan["blockers"]}
    identity=f"PAYMENT|LEGACY_CASHFLOW|ROW|{legacy_cashflow_id}"
    fact=session.scalar(select(Fact).where(Fact.business_identity_key==identity,Fact.is_current.is_(True)))
    candidate=plan["candidate"]
    if fact is None:
        fact=Fact(fact_type="PAYMENT",business_identity_key=identity,version_no=1,is_current=True,validation_status="VALID")
        session.add(fact); session.flush()
        session.add(PaymentFact(fact_id=fact.id,payer_party_id=candidate["payer_party_id"],payee_party_id=candidate["payee_party_id"],transaction_date=date.fromisoformat(candidate["transaction_date"]),amount=Decimal(candidate["amount"]),currency=candidate["currency"],payer_account_id=candidate["payer_account_id"],payee_account_id=candidate["payee_account_id"],bank_reference=candidate["bank_reference"],settlement_method=candidate["settlement_method"],payment_nature=candidate["payment_nature"]))
        session.flush()
    else:
        payment=session.get(PaymentFact,fact.id)
        if payment is None: raise ValueError("existing PAYMENT Fact is missing PaymentFact projection")
        expected=(int(candidate["payer_party_id"]),int(candidate["payee_party_id"]),date.fromisoformat(candidate["transaction_date"]),Decimal(candidate["amount"]),candidate["currency"],candidate["bank_reference"],candidate["settlement_method"],candidate["payment_nature"])
        actual=(int(payment.payer_party_id),int(payment.payee_party_id),payment.transaction_date,Decimal(payment.amount),payment.currency,payment.bank_reference,payment.settlement_method,payment.payment_nature)
        if actual!=expected: raise ValueError("existing PAYMENT Fact projection conflicts with deterministic Task19 plan")
    session.add(LegacyCashflowMap(legacy_cashflow_id=legacy_cashflow_id,payment_fact_id=fact.id,status="MIGRATED",reason="Task19 deterministic legacy direction/party/date bridge"))
    session.flush(); return {"status":"MIGRATED","legacy_cashflow_id":legacy_cashflow_id,"payment_fact_id":fact.id,"plan_digest":plan["plan_digest"]}

def _write(path,payload):
    rendered=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)
    if path: Path(path).write_text(rendered+"\n",encoding="utf-8")
    else: print(rendered)

def main() -> int:
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("plan"); p.add_argument("--legacy-cashflow-id",type=int,required=True); p.add_argument("--json")
    a=sub.add_parser("apply"); a.add_argument("--legacy-cashflow-id",type=int,required=True); a.add_argument("--expected-plan-digest"); a.add_argument("--confirm-database",required=True); a.add_argument("--json")
    args=parser.parse_args(); engine=create_engine(_database_url(),future=True,pool_pre_ping=True)
    with Session(engine) as session:
        if args.command=="plan": payload=make_plan(session,legacy_cashflow_id=args.legacy_cashflow_id)
        else:
            database=session.connection().exec_driver_sql("SELECT current_database()").scalar_one()
            if database!=args.confirm_database: raise SystemExit(f"--confirm-database mismatch: expected {database!r}")
            payload=build_one(session,legacy_cashflow_id=args.legacy_cashflow_id,expected_plan_digest=args.expected_plan_digest); session.commit()
        _write(args.json,payload)
    return 0

if __name__=="__main__": raise SystemExit(main())
