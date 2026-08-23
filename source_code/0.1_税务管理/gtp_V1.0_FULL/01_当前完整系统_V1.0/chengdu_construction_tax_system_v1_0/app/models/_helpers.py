"""V0.2: SQLAlchemy ORM helpers - relationship expressions and computed helpers.

Separated from the models to keep the model definitions clean and avoid
circular import issues.  These helpers are imported by models.py when needed.
"""
from __future__ import annotations

from sqlalchemy import Index
from sqlalchemy.orm import relationship

# Import the Base from db so relationships can reference it without circular imports
from .db import Base


def _build_entity_relationships():
    """Build cross-table relationships for Entity model.

    Returns a dict of relationship names to relationship() calls.
    Called after all model classes are defined to avoid forward-reference issues.
    """
    from .models import Entity, ExternalParty, Project

    return {
        "children": relationship(
            Entity,
            back_populates="parent",
            foreign_keys=[Entity.parent_entity_code],
        ),
        "parent": relationship(
            Entity,
            back_populates="children",
            foreign_keys=[Entity.parent_entity_code],
            remote_side=[Entity.code],
        ),
        "bank_accounts": relationship("EntityBankAccount", back_populates="entity"),
    }


def _build_project_relationships():
    """Build cross-table relationships for Project model."""
    from .models import (
        Contract, Invoice, CashFlow, Fulfillment,
        RealCost, Progress, Budget, RiskEvent,
        AIReviewJob, AIReviewBatch, RemediationTask,
        SyncLog, FactsSnapshot, TaxPaymentRecord,
    )

    return {
        "contracts": relationship(Contract, back_populates="project"),
        "invoices": relationship(Invoice, back_populates="project"),
        "cashflows": relationship(CashFlow, back_populates="project"),
        "fulfillments": relationship(Fulfillment, back_populates="project"),
        "real_costs": relationship(RealCost, back_populates="project"),
        "progress_records": relationship(Progress, back_populates="project"),
        "budgets": relationship(Budget, back_populates="project"),
        "risk_events": relationship(RiskEvent, back_populates="project"),
        "ai_review_jobs": relationship(AIReviewJob, back_populates="project"),
        "ai_review_batches": relationship(AIReviewBatch, back_populates="project"),
        "remediation_tasks": relationship(RemediationTask, back_populates="project"),
        "sync_logs": relationship(SyncLog, back_populates="project"),
        "facts_snapshots": relationship(FactsSnapshot, back_populates="project"),
        "tax_payment_records": relationship(TaxPaymentRecord, back_populates="project"),
    }


def _build_contract_relationships():
    """Build relationships for Contract model."""
    from .models import Contract, Project

    return {
        "project": relationship(Project, back_populates="contracts"),
    }


def _build_invoice_relationships():
    """Build relationships for Invoice model."""
    from .models import Invoice, Project

    return {
        "project": relationship(Project, back_populates="invoices"),
    }


def _build_cashflow_relationships():
    """Build relationships for CashFlow model."""
    from .models import CashFlow, Project

    return {
        "project": relationship(Project, back_populates="cashflows"),
    }


def _build_fulfillment_relationships():
    """Build relationships for Fulfillment model."""
    from .models import Fulfillment, Project

    return {
        "project": relationship(Project, back_populates="fulfillments"),
    }


def _build_real_cost_relationships():
    """Build relationships for RealCost model."""
    from .models import RealCost, Project

    return {
        "project": relationship(Project, back_populates="real_costs"),
    }


def _build_progress_relationships():
    """Build relationships for Progress model."""
    from .models import Progress, Project

    return {
        "project": relationship(Project, back_populates="progress_records"),
    }


def _build_budget_relationships():
    """Build relationships for Budget model."""
    from .models import Budget, Project

    return {
        "project": relationship(Project, back_populates="budgets"),
    }


def _build_risk_event_relationships():
    """Build relationships for RiskEvent model."""
    from .models import RiskEvent, Project

    return {
        "project": relationship(Project, back_populates="risk_events"),
    }


def _build_tax_payment_record_relationships():
    """Build relationships for TaxPaymentRecord model."""
    from .models import TaxPaymentRecord, Project

    return {
        "project": relationship(Project, back_populates="tax_payment_records"),
    }


def _build_ai_review_job_relationships():
    """Build relationships for AIReviewJob model."""
    from .models import AIReviewJob, Project, AIModelEndpoint, AIPromptTemplate

    return {
        "project": relationship(Project, back_populates="ai_review_jobs"),
        "endpoint": relationship(AIModelEndpoint),
        "prompt_template": relationship(AIPromptTemplate),
        "batch": relationship("AIReviewBatch", back_populates="jobs"),
    }


def _build_ai_review_batch_relationships():
    """Build relationships for AIReviewBatch model."""
    from .models import AIReviewBatch, Project, AIConsensusReport

    return {
        "project": relationship(Project, back_populates="ai_review_batches"),
        "consensus_report": relationship(AIConsensusReport, back_populates="batch", uselist=False),
        "jobs": relationship("AIReviewJob", back_populates="batch"),
    }


def _build_remediation_task_relationships():
    """Build relationships for RemediationTask model."""
    from .models import RemediationTask, Project

    return {
        "project": relationship(Project, back_populates="remediation_tasks"),
    }


def _build_sync_log_relationships():
    """Build relationships for SyncLog model."""
    from .models import SyncLog, Project, SyncPending

    return {
        "project": relationship(Project, back_populates="sync_logs"),
        "pending_items": relationship(SyncPending, back_populates="sync_log"),
    }


def _build_sync_pending_relationships():
    """Build relationships for SyncPending model."""
    from .models import SyncPending, SyncLog, Project

    return {
        "sync_log": relationship(SyncLog, back_populates="pending_items"),
        "project": relationship(Project, back_populates="sync_logs"),
    }


def _build_facts_snapshot_relationships():
    """Build relationships for FactsSnapshot model."""
    from .models import FactsSnapshot, Project

    return {
        "project": relationship(Project, back_populates="facts_snapshots"),
    }


def _build_entity_bank_account_relationships():
    """Build relationships for EntityBankAccount model."""
    from .models import EntityBankAccount, Entity

    return {
        "entity": relationship(Entity, back_populates="bank_accounts"),
    }


def _build_ai_review_result_relationships():
    """Build relationships for AIReviewResult model."""
    from .models import AIReviewResult, AIReviewJob

    return {
        "job": relationship(AIReviewJob, back_populates="result", uselist=False),
    }


def _build_ai_prompt_template_relationships():
    """Build relationships for AIPromptTemplate model."""
    return {}


def _build_ai_consensus_report_relationships():
    """Build relationships for AIConsensusReport model."""
    from .models import AIConsensusReport, AIReviewBatch

    return {
        "batch": relationship(AIReviewBatch, back_populates="consensus_report"),
    }


def _build_project_rag_map_relationships():
    """Build relationships for ProjectRAGMap model."""
    from .models import ProjectRAGMap, Project

    return {
        "project": relationship(Project, back_populates="rag_map"),
    }


def _build_user_relationships():
    """Build relationships for User model."""
    return {}


def apply_all_relationships() -> None:
    """Apply all cross-table relationships to model classes.

    Call this after importing all models to wire up SQLAlchemy relationships.
    """
    for name, rel in _build_entity_relationships().items():
        setattr(__import__("app.models", fromlist=["Entity"]).Entity, name, rel)

    for name, rel in _build_project_relationships().items():
        setattr(__import__("app.models", fromlist=["Project"]).Project, name, rel)

    for name, rel in _build_contract_relationships().items():
        setattr(__import__("app.models", fromlist=["Contract"]).Contract, name, rel)

    for name, rel in _build_invoice_relationships().items():
        setattr(__import__("app.models", fromlist=["Invoice"]).Invoice, name, rel)

    for name, rel in _build_cashflow_relationships().items():
        setattr(__import__("app.models", fromlist=["CashFlow"]).CashFlow, name, rel)

    for name, rel in _build_fulfillment_relationships().items():
        setattr(__import__("app.models", fromlist=["Fulfillment"]).Fulfillment, name, rel)

    for name, rel in _build_real_cost_relationships().items():
        setattr(__import__("app.models", fromlist=["RealCost"]).RealCost, name, rel)

    for name, rel in _build_progress_relationships().items():
        setattr(__import__("app.models", fromlist=["Progress"]).Progress, name, rel)

    for name, rel in _build_budget_relationships().items():
        setattr(__import__("app.models", fromlist=["Budget"]).Budget, name, rel)

    for name, rel in _build_risk_event_relationships().items():
        setattr(__import__("app.models", fromlist=["RiskEvent"]).RiskEvent, name, rel)

    for name, rel in _build_tax_payment_record_relationships().items():
        setattr(__import__("app.models", fromlist=["TaxPaymentRecord"]).TaxPaymentRecord, name, rel)

    for name, rel in _build_ai_review_job_relationships().items():
        setattr(__import__("app.models", fromlist=["AIReviewJob"]).AIReviewJob, name, rel)

    for name, rel in _build_ai_review_batch_relationships().items():
        setattr(__import__("app.models", fromlist=["AIReviewBatch"]).AIReviewBatch, name, rel)

    for name, rel in _build_remediation_task_relationships().items():
        setattr(__import__("app.models", fromlist=["RemediationTask"]).RemediationTask, name, rel)

    for name, rel in _build_sync_log_relationships().items():
        setattr(__import__("app.models", fromlist=["SyncLog"]).SyncLog, name, rel)

    for name, rel in _build_sync_pending_relationships().items():
        setattr(__import__("app.models", fromlist=["SyncPending"]).SyncPending, name, rel)

    for name, rel in _build_facts_snapshot_relationships().items():
        setattr(__import__("app.models", fromlist=["FactsSnapshot"]).FactsSnapshot, name, rel)

    for name, rel in _build_entity_bank_account_relationships().items():
        setattr(__import__("app.models", fromlist=["EntityBankAccount"]).EntityBankAccount, name, rel)

    for name, rel in _build_ai_review_result_relationships().items():
        setattr(__import__("app.models", fromlist=["AIReviewResult"]).AIReviewResult, name, rel)

    for name, rel in _build_ai_prompt_template_relationships().items():
        setattr(__import__("app.models", fromlist=["AIPromptTemplate"]).AIPromptTemplate, name, rel)

    for name, rel in _build_ai_consensus_report_relationships().items():
        setattr(__import__("app.models", fromlist=["AIConsensusReport"]).AIConsensusReport, name, rel)

    for name, rel in _build_project_rag_map_relationships().items():
        setattr(__import__("app.models", fromlist=["ProjectRAGMap"]).ProjectRAGMap, name, rel)

    for name, rel in _build_user_relationships().items():
        setattr(__import__("app.models", fromlist=["User"]).User, name, rel)
