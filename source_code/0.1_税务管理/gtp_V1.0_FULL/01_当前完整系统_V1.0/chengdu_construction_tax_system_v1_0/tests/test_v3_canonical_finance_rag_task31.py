from __future__ import annotations
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import inspect
import pytest

import app.ai.canonical_context as canonical_context
import app.ai.context as context
from app.cutover.reader import ReaderRoute
from app.domain.finance.canonical_four_flow import CANONICAL_FOUR_FLOW_V1, CanonicalFourFlow
from app.domain.finance.canonical_project_finance import CANONICAL_PROJECT_FINANCE_V1, CanonicalProjectFinance
from app.domain.tax.canonical_project_tax import TASK31_CANONICAL_TAX_READ_V1, CanonicalProjectTax, CanonicalProjectTaxReadError
from app.domain.tax.project_tax_analysis import RULESET_VERSION, canonical_hash

ROOT=Path(__file__).parents[1]

def alloc(fid,aid,net,vat,gross,status="CONFIRMED"):
    return SimpleNamespace(id=aid,fact_id=fid,project_id=1,is_current=True,status=status,allocation_method="EXPLICIT",confidence="HIGH",allocated_net=Decimal(net),allocated_vat=Decimal(vat),allocated_gross=Decimal(gross))
def fact(fid,kind,status="VALID"):
    return SimpleNamespace(id=fid,fact_type=kind,business_identity_key=f"BI-{fid}",version_no=1,is_current=True,validation_status=status)

class FinanceSession:
    def __init__(self,mapping): self.mapping=mapping
    def get(self,model,key): return self.mapping.get((model.__name__,key))

class Rows:
    def __init__(self,rows): self.rows=rows
    def all(self): return list(self.rows)

class SequenceSession:
    def __init__(self,sequences,mapping=None,scalar_value=None): self.sequences=list(sequences); self.mapping=mapping or {}; self.scalar_value=scalar_value
    def scalars(self,stmt): return Rows(self.sequences.pop(0))
    def scalar(self,stmt): return self.scalar_value
    def get(self,model,key): return self.mapping.get((model.__name__,key))


def test_task31_versions_and_task17_ruleset():
    assert CANONICAL_PROJECT_FINANCE_V1=="CANONICAL_PROJECT_FINANCE_V1"
    assert CANONICAL_FOUR_FLOW_V1=="CANONICAL_FOUR_FLOW_V1"
    assert TASK31_CANONICAL_TAX_READ_V1=="TASK31_CANONICAL_TAX_READ_V1"
    assert RULESET_VERSION=="V3_PROJECT_TAX_ANALYSIS_V1"


def test_finance_uses_allocations_and_excludes_needs_review(monkeypatch):
    session=FinanceSession({
        ("ContractFact",1):SimpleNamespace(contract_number="C1",contract_date=date(2026,1,1),contract_category=None,buyer_party_id=1,seller_party_id=2,currency="CNY"),
        ("InvoiceFact",2):SimpleNamespace(invoice_identity_key="I2",invoice_number="INV2",invoice_date=date(2026,1,2),invoice_status="VALID",seller_party_id=2,buyer_party_id=1,currency="CNY"),
        ("PaymentFact",3):SimpleNamespace(payer_party_id=1,payee_party_id=2,transaction_date=date(2026,1,3),bank_reference="P3",settlement_method="BANK_TRANSFER",payment_nature="NORMAL",currency="CNY"),
    })
    service=CanonicalProjectFinance(session)
    monkeypatch.setattr(service,"_rows",lambda pid:[
        (alloc(1,11,"1000","0","1000"),fact(1,"CONTRACT")),
        (alloc(2,12,"100","13","113"),fact(2,"INVOICE")),
        (alloc(3,13,"80","0","80"),fact(3,"PAYMENT")),
        (alloc(4,14,"50","6.5","56.5"),fact(4,"INVOICE","NEEDS_REVIEW")),
    ])
    result=service.read(1)
    assert result["summary"]=={"allocated_contract_amount":"1000.00","allocated_invoice_net":"100.00","allocated_invoice_vat":"13.00","allocated_invoice_gross":"113.00","allocated_payment_amount":"80.00","contract_count":1,"invoice_count":1,"payment_count":1}
    assert 4 not in result["eligible_fact_ids"] and result["evidence_quality"][0]["code"]=="FACT_NEEDS_REVIEW"


def test_voided_invoice_not_in_totals(monkeypatch):
    service=CanonicalProjectFinance(FinanceSession({("InvoiceFact",2):SimpleNamespace(invoice_status="VOIDED")}))
    monkeypatch.setattr(service,"_rows",lambda pid:[(alloc(2,12,"100","13","113"),fact(2,"INVOICE"))])
    result=service.read(1)
    assert result["summary"]["allocated_invoice_gross"]=="0.00" and result["evidence_quality"][0]["code"]=="INVOICE_VOIDED_EXCLUDED"


def test_four_flow_consumes_explicit_edges_and_reports_unlinked():
    facts=[fact(1,"CONTRACT"),fact(2,"INVOICE"),fact(3,"PAYMENT"),fact(4,"PAYMENT")]
    rels=[SimpleNamespace(id=10,relationship_type="INVOICE_FOR_CONTRACT",source_fact_id=2,target_fact_id=1,reason="explicit"),SimpleNamespace(id=11,relationship_type="PAYMENT_FOR_INVOICE",source_fact_id=3,target_fact_id=2,reason="explicit")]
    ev=[SimpleNamespace(relationship_id=10),SimpleNamespace(relationship_id=11)]
    result=CanonicalFourFlow(SequenceSession([facts,rels,ev])).read(1,eligible_fact_ids={1,2,3,4})
    assert result["coverage"]["invoice_contract_edge_count"]==1 and result["coverage"]["payment_invoice_edge_count"]==1
    assert result["coverage"]["unlinked_payment_fact_ids"]==[4]
    assert result["invoice_payment_amount_allocation"]["supported"] is False


def test_cross_project_edge_excluded_without_inference():
    facts=[fact(1,"CONTRACT"),fact(2,"INVOICE")]
    rels=[SimpleNamespace(id=10,relationship_type="PAYMENT_FOR_INVOICE",source_fact_id=999,target_fact_id=2,reason="explicit")]
    result=CanonicalFourFlow(SequenceSession([facts,rels,[SimpleNamespace(relationship_id=10)]])).read(1,eligible_fact_ids={1,2})
    assert result["relationships"]==[] and any(x["code"]=="CROSS_PROJECT_RELATIONSHIP_EXCLUDED" for x in result["evidence_quality"])


def analysis_fixture():
    comps=[SimpleNamespace(id=1,component_type="OUTPUT_VAT",taxable_amount=Decimal("100"),tax_amount=Decimal("13"),fact_project_allocation_id=5,output_vat_event_id=10,input_vat_claim_id=None,tax_prepayment_fact_id=None),SimpleNamespace(id=2,component_type="INPUT_VAT",taxable_amount=None,tax_amount=Decimal("3"),fact_project_allocation_id=6,output_vat_event_id=None,input_vat_claim_id=11,tax_prepayment_fact_id=None),SimpleNamespace(id=3,component_type="TAX_PREPAYMENT",taxable_amount=None,tax_amount=Decimal("2"),fact_project_allocation_id=None,output_vat_event_id=None,input_vat_claim_id=None,tax_prepayment_fact_id=12)]
    payload={"project_id":1,"tax_type":"VAT","basis":"TAX","output_taxable_net":"100.00","output_vat":"13.00","claimed_input_vat":"3.00","tax_prepayment":"2.00","net_vat_before_entity_credit":"10.00","net_vat_after_project_prepayment":"8.00","allocation_coverage_status":"FULL"}
    analysis=SimpleNamespace(id=20,project_id=1,reporting_party_id=10,tax_period=date(2026,1,1),tax_type="VAT",basis="TAX",input_snapshot_sha256="a"*64,result_sha256=canonical_hash(payload),allocation_coverage_status="FULL",output_taxable_net=Decimal("100"),output_vat=Decimal("13"),claimed_input_vat=Decimal("3"),tax_prepayment=Decimal("2"),net_vat_before_entity_credit=Decimal("10"),net_vat_after_project_prepayment=Decimal("8"))
    return comps,analysis


def test_tax_adapter_reuses_task17_persisted_result():
    comps,analysis=analysis_fixture(); state=SimpleNamespace(id=1,current_run_id=7,reporting_party_id=10,tax_period=date(2026,1,1),state="OPEN"); run=SimpleNamespace(id=7,run_status="SUCCEEDED",tax_type="PROJECT_TAX",ruleset_version=RULESET_VERSION,reporting_party_id=10,tax_period=date(2026,1,1)); a5=SimpleNamespace(id=5,fact_id=105,is_current=True,status="CONFIRMED",project_id=1); a6=SimpleNamespace(id=6,fact_id=106,is_current=True,status="CONFIRMED",project_id=1); valid=SimpleNamespace(is_current=True,validation_status="VALID")
    session=SequenceSession([[state],comps],{("CalculationRun",7):run,("FactProjectAllocation",5):a5,("FactProjectAllocation",6):a6,("Fact",105):valid,("Fact",106):valid,("Fact",12):valid},analysis)
    result=CanonicalProjectTax(session).read(1)
    assert result["analyses"][0]["result_hash_verified"] is True and result["analyses"][0]["net_vat_after_project_prepayment"]=="8.00"


def test_tax_hash_mismatch_fails_closed():
    comps,analysis=analysis_fixture(); analysis.result_sha256="0"*64; state=SimpleNamespace(id=1,current_run_id=7,reporting_party_id=10,tax_period=date(2026,1,1),state="OPEN"); run=SimpleNamespace(id=7,run_status="SUCCEEDED",tax_type="PROJECT_TAX",ruleset_version=RULESET_VERSION,reporting_party_id=10,tax_period=date(2026,1,1)); a5=SimpleNamespace(id=5,fact_id=105,is_current=True,status="CONFIRMED",project_id=1); a6=SimpleNamespace(id=6,fact_id=106,is_current=True,status="CONFIRMED",project_id=1); valid=SimpleNamespace(is_current=True,validation_status="VALID")
    with pytest.raises(CanonicalProjectTaxReadError,match="hash"):
        CanonicalProjectTax(SequenceSession([[state],comps],{("CalculationRun",7):run,("FactProjectAllocation",5):a5,("FactProjectAllocation",6):a6,("Fact",105):valid,("Fact",106):valid,("Fact",12):valid},analysis)).read(1)


def test_context_canonical_branch_never_calls_legacy(monkeypatch):
    monkeypatch.setattr(context,"get_reader_route",lambda db:ReaderRoute("PRIMARY","CANONICAL_FACTS")); monkeypatch.setattr(context,"build_canonical_context_native",lambda db,pid,scope:{"scope":scope}); called={"legacy":False}; monkeypatch.setattr(context,"_build_legacy_context",lambda *a,**k:called.update(legacy=True))
    assert context.build_context(object(),1,"invoice")["data_source"]=="CANONICAL_FACTS" and called["legacy"] is False


def test_context_canonical_failure_has_no_fallback(monkeypatch):
    monkeypatch.setattr(context,"get_reader_route",lambda db:ReaderRoute("PRIMARY","CANONICAL_FACTS")); monkeypatch.setattr(context,"build_canonical_context_native",lambda *a,**k:(_ for _ in ()).throw(RuntimeError("canonical failed"))); called={"legacy":False}; monkeypatch.setattr(context,"_build_legacy_context",lambda *a,**k:called.update(legacy=True))
    with pytest.raises(RuntimeError): context.build_context(object(),1,"invoice")
    assert called["legacy"] is False


def test_canonical_context_has_no_legacy_business_fact_queries():
    source=(ROOT/"app/ai/canonical_context.py").read_text(encoding="utf-8")
    for token in ("select(Budget)","select(Contract)","select(Invoice)","select(CashFlow)","project_summary(","matching_rows(","rebuild_tax_ledger("):
        assert token not in source


def test_read_models_are_zero_write_and_no_amount_matching():
    files=["app/domain/finance/canonical_project_finance.py","app/domain/finance/canonical_four_flow.py","app/domain/tax/canonical_project_tax.py","app/ai/canonical_context.py"]
    source="\n".join((ROOT/f).read_text(encoding="utf-8") for f in files).lower()
    for token in ("session.add(","session.delete(",".commit(",".flush(","levenshtein","embedding","invoice_paid","outstanding"):
        assert token not in source


def test_task31_adds_no_migration_99():
    assert not list((ROOT/"alembic/versions").glob("99*v3*finance*"))

def test_tax_adapter_rejects_nonvalid_underlying_fact():
    comps,analysis=analysis_fixture(); state=SimpleNamespace(id=1,current_run_id=7,reporting_party_id=10,tax_period=date(2026,1,1),state="OPEN"); run=SimpleNamespace(id=7,run_status="SUCCEEDED",tax_type="PROJECT_TAX",ruleset_version=RULESET_VERSION,reporting_party_id=10,tax_period=date(2026,1,1)); a5=SimpleNamespace(id=5,fact_id=105,is_current=True,status="CONFIRMED",project_id=1); a6=SimpleNamespace(id=6,fact_id=106,is_current=True,status="CONFIRMED",project_id=1); valid=SimpleNamespace(is_current=True,validation_status="VALID"); review=SimpleNamespace(is_current=True,validation_status="NEEDS_REVIEW")
    with pytest.raises(CanonicalProjectTaxReadError,match="non-current/non-VALID"):
        CanonicalProjectTax(SequenceSession([[state],comps],{("CalculationRun",7):run,("FactProjectAllocation",5):a5,("FactProjectAllocation",6):a6,("Fact",105):review,("Fact",106):valid,("Fact",12):valid},analysis)).read(1)
