#!/usr/bin/env python3
"""Gate S19: canonical PaymentFact + legacy CashFlow bridge + CASH activation."""
from __future__ import annotations
import json, os
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from app.calc.basis.cash_basis import LEGACY_CASHFLOW_FALLBACK_ALLOWED
EXPECTED_HEAD="90_v3_payment_facts"; ROOT=Path(__file__).resolve().parents[2]; PROHIBITED={"project_id","cost_category","recognized_cost","accrual_period","invoice_id"}
def _url():
    v=os.getenv("DATABASE_URL","").strip()
    if not v: raise SystemExit("DATABASE_URL is required")
    if make_url(v).get_backend_name() not in {"postgresql","postgres"}: raise SystemExit("Gate S19 is PostgreSQL-only")
    return v
def main():
    engine=create_engine(_url(),future=True,pool_pre_ping=True); failures=[]; evidence={}
    with Session(engine) as session:
        db=session.connection().exec_driver_sql("SELECT current_database()").scalar_one(); head=str(session.connection().exec_driver_sql("SELECT version_num FROM alembic_version_tax").scalar_one()); ins=inspect(session.connection()); tables=set(ins.get_table_names(schema="public")); disk_heads=list(ScriptDirectory.from_config(Config(str(ROOT/"alembic.ini"))).get_heads()); evidence.update(database=db,alembic_db_heads=[head],alembic_disk_heads=disk_heads)
        if head!=EXPECTED_HEAD: failures.append(f"database head must be {EXPECTED_HEAD}, got {head}")
        if disk_heads!=[EXPECTED_HEAD]: failures.append(f"disk heads must be {[EXPECTED_HEAD]}, got {disk_heads}")
        for t in ["payment_facts","legacy_cashflow_map"]:
            if t not in tables: failures.append(f"missing table {t}")
        cols={r["name"] for r in ins.get_columns("payment_facts")} if "payment_facts" in tables else set(); prohibited=sorted(cols&PROHIBITED); evidence["payment_fact_prohibited_columns"]=prohibited
        if prohibited: failures.append(f"PaymentFact contains prohibited mixed-basis columns: {prohibited}")
        payment_count=int(session.connection().exec_driver_sql("SELECT count(*) FROM payment_facts").scalar_one()) if "payment_facts" in tables else 0; migrated=int(session.connection().exec_driver_sql("SELECT count(*) FROM legacy_cashflow_map WHERE status='MIGRATED'").scalar_one()) if "legacy_cashflow_map" in tables else 0; review=int(session.connection().exec_driver_sql("SELECT count(*) FROM legacy_cashflow_map WHERE status='NEEDS_REVIEW'").scalar_one()) if "legacy_cashflow_map" in tables else 0; evidence.update(payment_fact_count=payment_count,legacy_migrated_count=migrated,legacy_needs_review_count=review,cash_legacy_fallback_allowed=bool(LEGACY_CASHFLOW_FALLBACK_ALLOWED))
        if LEGACY_CASHFLOW_FALLBACK_ALLOWED: failures.append("legacy cashflows fallback must remain disabled")
        if payment_count<1: failures.append("Task19 requires at least one canonical PaymentFact pilot")
        if migrated<1: failures.append("Task19 requires at least one MIGRATED legacy CashFlow pilot")
        checks={"invalid_payment_fact_rows":"SELECT count(*) FROM payment_facts pf JOIN facts f ON f.id=pf.fact_id WHERE pf.payer_party_id=pf.payee_party_id OR pf.amount<=0 OR f.fact_type<>'PAYMENT' OR f.is_current IS NOT TRUE OR f.validation_status<>'VALID'","migrated_map_without_payment":"SELECT count(*) FROM legacy_cashflow_map m LEFT JOIN payment_facts pf ON pf.fact_id=m.payment_fact_id WHERE m.status='MIGRATED' AND pf.fact_id IS NULL","review_map_with_payment":"SELECT count(*) FROM legacy_cashflow_map WHERE status='NEEDS_REVIEW' AND payment_fact_id IS NOT NULL","missing_date_migrated":"SELECT count(*) FROM legacy_cashflow_map m JOIN cashflows c ON c.id=m.legacy_cashflow_id WHERE m.status='MIGRATED' AND (c.transaction_date IS NULL OR btrim(c.transaction_date)='')","legacy_direction_semantic_errors":"SELECT count(*) FROM legacy_cashflow_map m JOIN cashflows c ON c.id=m.legacy_cashflow_id JOIN payment_facts pf ON pf.fact_id=m.payment_fact_id JOIN internal_entities ie ON ie.canonical_code=c.entity_code WHERE m.status='MIGRATED' AND ((lower(c.direction)='out' AND pf.payer_party_id<>ie.party_id) OR (lower(c.direction)='in' AND pf.payee_party_id<>ie.party_id) OR lower(c.direction) NOT IN ('in','out'))","cash_component_lineage_errors":"SELECT count(*) FROM group_penetration_components gpc WHERE gpc.component_type IN ('CASH_INFLOW','CASH_OUTFLOW','INTERNAL_CASH_ELIMINATION') AND (gpc.payment_fact_id IS NULL OR gpc.fulfillment_fact_id IS NOT NULL OR gpc.entity_vat_ledger_id IS NOT NULL)","duplicate_migrated_source_fingerprints":"SELECT count(*) FROM (SELECT c.source_fingerprint FROM legacy_cashflow_map m JOIN cashflows c ON c.id=m.legacy_cashflow_id WHERE m.status='MIGRATED' AND c.source_fingerprint IS NOT NULL AND btrim(c.source_fingerprint)<>'' GROUP BY c.source_fingerprint HAVING count(*)>1) d"}
        for k,sql in checks.items():
            v=int(session.connection().exec_driver_sql(sql).scalar_one()); evidence[k]=v
            if v: failures.append(f"{k}={v}")
        cash_results=int(session.connection().exec_driver_sql("SELECT count(*) FROM group_penetration_results WHERE basis='CASH' AND status='READY'").scalar_one()); evidence["official_group_cash_result_count"]=cash_results
        if cash_results<1: failures.append("Task19 requires at least one READY canonical GROUP_CASH pilot")
    payload={"gate":"S19","status":"PASS" if not failures else "FAIL","evidence":evidence,"failures":failures}; print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)); return 0 if not failures else 1
if __name__=="__main__": raise SystemExit(main())
