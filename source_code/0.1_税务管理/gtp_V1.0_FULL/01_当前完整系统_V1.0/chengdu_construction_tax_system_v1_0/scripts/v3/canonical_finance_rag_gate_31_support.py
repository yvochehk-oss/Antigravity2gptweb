"""Fixture and read-only monitoring helpers for Gate S31."""
from __future__ import annotations
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import re
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.models import Project
from app.v3_contract_models import ContractFact
from app.v3_fact_models import Fact, FactRelationship, InvoiceFact
from app.v3_fact_relationship_evidence_models import FactRelationshipEvidence
from app.v3_party_models import Party, SourceDocument
from app.v3_payment_models import PaymentFact
from app.v3_project_analysis_models import FactProjectAllocation

class ReadOnlyMonitor:
    LEGACY_READ_TABLES={"contracts","invoices","cashflows","fulfillments","real_costs","budgets","progress"}
    CORE_WRITE_TABLES={"facts","invoice_facts","contract_facts","payment_facts","projects","fact_project_allocations","fact_relationships","fact_relationship_evidence","project_tax_analysis","project_tax_analysis_components","calculation_runs","tax_period_states","writer_cutover_states","v3_cutover_finalizations",*LEGACY_READ_TABLES}
    def __init__(self):
        self.legacy_business_read_attempted=False; self.core_write_attempted=False; self.production_seal_write_attempted=False; self.cutover_state_write_attempted=False; self.legacy_write_attempted=False
        self._dml=re.compile(r'^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"?([A-Za-z0-9_]+)"?',re.I)
    def before_cursor_execute(self,conn,cursor,statement,parameters,context,executemany):
        sql=str(statement); match=self._dml.match(sql)
        if match:
            table=match.group(1).lower()
            if table in self.CORE_WRITE_TABLES:
                self.core_write_attempted=True
                self.production_seal_write_attempted |= table=="v3_cutover_finalizations"
                self.cutover_state_write_attempted |= table=="writer_cutover_states"
                self.legacy_write_attempted |= table in self.LEGACY_READ_TABLES
                raise AssertionError(f"Gate S31 forbidden DML: {table}")
        if sql.lstrip().lower().startswith("select"):
            low=sql.lower()
            for table in self.LEGACY_READ_TABLES:
                if re.search(rf'\b(?:from|join)\s+"?{re.escape(table)}"?\b',low):
                    self.legacy_business_read_attempted=True
                    raise AssertionError(f"Gate S31 legacy business read: {table}")

def table_count(session:Session,name:str):
    exists=session.execute(text("SELECT to_regclass(:n)"),{"n":f"public.{name}"}).scalar_one()
    return int(session.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one()) if exists else None

def fact_snapshot(session:Session,ids:list[int]):
    stmt=select(Fact.id,Fact.fact_type,Fact.business_identity_key,Fact.version_no,Fact.is_current,Fact.supersedes_fact_id,Fact.validation_status).where(Fact.id.in_(ids)).order_by(Fact.id)
    return [tuple(row) for row in session.execute(stmt).all()]

def _party(session,token,suffix):
    row=Party(code=f"S31-{suffix}-{token}",name=f"S31 Party {suffix} {token}",short_name=f"S31 {suffix}",party_type="external",active=True); session.add(row); session.flush(); return row

def _fact(session,token,suffix,kind,status="VALID"):
    row=Fact(fact_type=kind,business_identity_key=f"S31|{kind}|{suffix}|{token}",version_no=1,is_current=True,supersedes_fact_id=None,validation_status=status); session.add(row); session.flush(); return row

def _allocation(session,fact,project,net,vat,gross,suffix):
    row=FactProjectAllocation(fact_id=fact.id,project_id=project.id,allocation_version=1,is_current=True,supersedes_allocation_id=None,allocated_net=Decimal(net),allocated_vat=Decimal(vat),allocated_gross=Decimal(gross),allocation_method="EXPLICIT",confidence="HIGH",status="CONFIRMED",proposal_source="HUMAN",source_document_id=None,rule_version="TASK29_PROJECT_ATTRIBUTION_V1",reviewed_by="gate:S31",reviewed_at=datetime.now(timezone.utc),note=f"S31 fixture {suffix}"); session.add(row); session.flush()

def _edge(session,token,source,target,kind,suffix):
    rel=FactRelationship(source_fact_id=source.id,target_fact_id=target.id,relationship_type=kind,reason="S31 explicit fixture edge"); session.add(rel); session.flush()
    doc=SourceDocument(source_system="GATE_S31",external_document_id=f"S31-DOC-{suffix}-{token}",filename=f"S31-{suffix}.pdf",mime_type="application/pdf",file_sha256=sha256(f"{token}:{suffix}".encode()).hexdigest(),document_type="contract",status="VALIDATED"); session.add(doc); session.flush()
    ev=FactRelationshipEvidence(relationship_id=rel.id,source_document_id=doc.id,source_extraction_id=f"S31-EX-{suffix}-{token}",evidence_type="EXPLICIT_REFERENCE",reference_type="TARGET_FACT_ID",reference_value=str(target.id),evidence_text=f"Gate S31 explicit {kind} reference",page_no=1,confidence=Decimal("0.99000"),ruleset_version="TASK30_FACT_RELATIONSHIP_RULESET_V1",evidence_fingerprint=sha256(f"S31:{rel.id}:{doc.id}:{target.id}".encode()).hexdigest(),submitted_by="gate:S31"); session.add(ev); session.flush()

def make_fixture(session:Session,token:str):
    project=Project(code=f"S31-{token}",project_code=f"S31-{token}",name=f"S31 Project {token}",city="Chengdu",contract_total=Decimal("1000000.00"),tax_method="general"); session.add(project); session.flush()
    a,b=_party(session,token,"A"),_party(session,token,"B")
    contract=_fact(session,token,"C","CONTRACT"); invoice=_fact(session,token,"I","INVOICE"); payment=_fact(session,token,"P","PAYMENT"); unlinked=_fact(session,token,"PU","PAYMENT"); review=_fact(session,token,"R","INVOICE","NEEDS_REVIEW")
    session.add(ContractFact(fact_id=contract.id,buyer_party_id=a.id,seller_party_id=b.id,contract_number=f"S31-C-{token}",contract_date=date(2026,9,1),contract_category=None,contract_amount=Decimal("1000.00"),currency="CNY"))
    session.add(InvoiceFact(fact_id=invoice.id,seller_party_id=b.id,buyer_party_id=a.id,invoice_identity_key=f"S31-INV-ID-{token}",invoice_identity_version="S31_V1",invoice_number=f"S31-INV-{token}",invoice_date=date(2026,9,1),invoice_status="VALID",gross_amount=Decimal("113.00"),net_amount=Decimal("100.00"),vat_amount=Decimal("13.00"),currency="CNY"))
    session.add(PaymentFact(fact_id=payment.id,payer_party_id=a.id,payee_party_id=b.id,transaction_date=date(2026,9,1),amount=Decimal("80.00"),currency="CNY",bank_reference=f"S31-PAY-{token}",settlement_method="BANK_TRANSFER",payment_nature="NORMAL")); session.add(PaymentFact(fact_id=unlinked.id,payer_party_id=a.id,payee_party_id=b.id,transaction_date=date(2026,9,2),amount=Decimal("20.00"),currency="CNY",bank_reference=f"S31-PAY-U-{token}",settlement_method="BANK_TRANSFER",payment_nature="NORMAL"))
    session.add(InvoiceFact(fact_id=review.id,seller_party_id=b.id,buyer_party_id=a.id,invoice_identity_key=f"S31-REV-ID-{token}",invoice_identity_version="S31_V1",invoice_number=f"S31-REV-{token}",invoice_date=date(2026,9,1),invoice_status="VALID",gross_amount=Decimal("56.50"),net_amount=Decimal("50.00"),vat_amount=Decimal("6.50"),currency="CNY")); session.flush()
    for args in ((contract,"1000","0","1000","C"),(invoice,"100","13","113","I"),(payment,"80","0","80","P"),(unlinked,"20","0","20","PU"),(review,"50","6.5","56.5","R")): _allocation(session,args[0],project,*args[1:])
    _edge(session,token,invoice,contract,"INVOICE_FOR_CONTRACT","IC"); _edge(session,token,payment,invoice,"PAYMENT_FOR_INVOICE","PI"); _edge(session,token,payment,contract,"PAYMENT_FOR_CONTRACT","PC")
    session.flush(); return project,contract,invoice,payment,unlinked,review
