#!/usr/bin/env python3
"""Task17 explicit Fact -> Project allocation PLAN/APPLY and review helper."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.domain.tax.project_tax_analysis import canonical_hash
from app.models import Project
from app.v3_fact_models import Fact
from app.v3_project_analysis_models import FactProjectAllocation

EXPECTED_HEAD = "88_v3_project_tax_analysis"
PLAN_KIND = "V3_TASK17_PROJECT_ALLOCATION_PLAN"
ALLOWED_METHODS = {"EXPLICIT", "SOURCE_DOCUMENT", "MANUAL", "PROPORTIONAL", "RULE_BASED"}
ALLOWED_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
ALLOWED_STATUS = {"CANDIDATE", "NEEDS_REVIEW", "CONFIRMED"}
ALLOWED_PROPOSAL_SOURCE = {"HUMAN", "DOCUMENT", "RULE_ENGINE", "AI", "LEGACY"}


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Task17 Project Allocation is PostgreSQL-only")
    return value


def _head(session: Session) -> str:
    return str(session.connection().exec_driver_sql("SELECT version_num FROM alembic_version_tax").scalar_one())


def _money(value: str) -> Decimal:
    result = Decimal(value).quantize(Decimal("0.01"))
    if not result.is_finite():
        raise ValueError("allocation amount must be finite")
    return result


def make_plan(session: Session, *, fact_id: int, project_id: int, allocated_net: Decimal, allocated_vat: Decimal, allocated_gross: Decimal, allocation_method: str, confidence: str, status: str, proposal_source: str, source_document_id: int | None, rule_version: str | None, reviewed_by: str | None, note: str | None) -> dict[str, Any]:
    if _head(session) != EXPECTED_HEAD:
        raise ValueError(f"formal DB head must be {EXPECTED_HEAD}")
    fact = session.get(Fact, fact_id)
    project = session.get(Project, project_id)
    if fact is None:
        raise ValueError(f"unknown Fact: {fact_id}")
    if project is None:
        raise ValueError(f"unknown Project: {project_id}")
    method = allocation_method.strip().upper()
    confidence = confidence.strip().upper()
    status = status.strip().upper()
    proposal_source = proposal_source.strip().upper()
    if method not in ALLOWED_METHODS:
        raise ValueError(f"unsupported allocation method: {method}")
    if confidence not in ALLOWED_CONFIDENCE:
        raise ValueError(f"unsupported confidence: {confidence}")
    if status not in ALLOWED_STATUS:
        raise ValueError(f"unsupported initial status: {status}")
    if proposal_source not in ALLOWED_PROPOSAL_SOURCE:
        raise ValueError(f"unsupported proposal source: {proposal_source}")
    if proposal_source == "AI" and status != "CANDIDATE":
        raise ValueError("AI allocation proposal must start as CANDIDATE")
    if status == "CONFIRMED" and not str(reviewed_by or "").strip():
        raise ValueError("CONFIRMED allocation requires --reviewed-by")
    if method == "SOURCE_DOCUMENT" and source_document_id is None:
        raise ValueError("SOURCE_DOCUMENT allocation requires --source-document-id")
    if method == "RULE_BASED" and not str(rule_version or "").strip():
        raise ValueError("RULE_BASED allocation requires --rule-version")
    if abs((allocated_net + allocated_vat) - allocated_gross) > Decimal("0.01"):
        raise ValueError("allocated_net + allocated_vat must equal allocated_gross")
    current = session.scalar(select(FactProjectAllocation).where(FactProjectAllocation.fact_id == fact_id, FactProjectAllocation.project_id == project_id, FactProjectAllocation.is_current.is_(True)))
    if current is not None:
        raise ValueError(f"current allocation already exists for fact/project: allocation_id={current.id}; review or resolve it first")
    latest = session.scalar(select(FactProjectAllocation).where(FactProjectAllocation.fact_id == fact_id, FactProjectAllocation.project_id == project_id).order_by(FactProjectAllocation.allocation_version.desc(), FactProjectAllocation.id.desc()).limit(1))
    next_version = (int(latest.allocation_version) + 1) if latest is not None else 1
    core = {
        "kind": PLAN_KIND,
        "version": 1,
        "fact_id": fact_id,
        "fact_type": fact.fact_type,
        "project_id": project_id,
        "project_code": project.code,
        "allocation_version": next_version,
        "supersedes_allocation_id": latest.id if latest is not None else None,
        "allocated_net": str(allocated_net),
        "allocated_vat": str(allocated_vat),
        "allocated_gross": str(allocated_gross),
        "allocation_method": method,
        "confidence": confidence,
        "status": status,
        "proposal_source": proposal_source,
        "source_document_id": source_document_id,
        "rule_version": rule_version,
        "reviewed_by": reviewed_by,
        "note": note,
    }
    return {**core, "plan_digest": canonical_hash(core)}


def apply_plan(session: Session, *, plan: dict[str, Any], expected_plan_digest: str) -> dict[str, Any]:
    if plan.get("plan_digest") != expected_plan_digest:
        raise ValueError("Task17 allocation plan digest mismatch")
    core = {key: value for key, value in plan.items() if key != "plan_digest"}
    if canonical_hash(core) != expected_plan_digest:
        raise ValueError("Task17 allocation plan content changed after review")
    fresh = make_plan(
        session,
        fact_id=int(plan["fact_id"]),
        project_id=int(plan["project_id"]),
        allocated_net=Decimal(plan["allocated_net"]),
        allocated_vat=Decimal(plan["allocated_vat"]),
        allocated_gross=Decimal(plan["allocated_gross"]),
        allocation_method=plan["allocation_method"],
        confidence=plan["confidence"],
        status=plan["status"],
        proposal_source=plan["proposal_source"],
        source_document_id=plan.get("source_document_id"),
        rule_version=plan.get("rule_version"),
        reviewed_by=plan.get("reviewed_by"),
        note=plan.get("note"),
    )
    if fresh["plan_digest"] != expected_plan_digest:
        raise ValueError("stale Task17 allocation plan")
    now = datetime.now(timezone.utc)
    row = FactProjectAllocation(
        fact_id=int(plan["fact_id"]),
        project_id=int(plan["project_id"]),
        allocation_version=int(plan["allocation_version"]),
        supersedes_allocation_id=plan.get("supersedes_allocation_id"),
        is_current=True,
        allocated_net=Decimal(plan["allocated_net"]),
        allocated_vat=Decimal(plan["allocated_vat"]),
        allocated_gross=Decimal(plan["allocated_gross"]),
        allocation_method=plan["allocation_method"],
        confidence=plan["confidence"],
        status=plan["status"],
        proposal_source=plan["proposal_source"],
        source_document_id=plan.get("source_document_id"),
        rule_version=plan.get("rule_version"),
        reviewed_by=plan.get("reviewed_by") if plan["status"] == "CONFIRMED" else None,
        reviewed_at=now if plan["status"] == "CONFIRMED" else None,
        note=plan.get("note"),
    )
    session.add(row)
    session.flush()
    return {"status": "CREATED", "allocation_id": row.id, "fact_id": row.fact_id, "project_id": row.project_id, "allocation_status": row.status, "allocation_version": row.allocation_version, "plan_digest": expected_plan_digest}


def review_allocation(session: Session, *, allocation_id: int, decision: str, reviewed_by: str, note: str | None = None) -> dict[str, Any]:
    if _head(session) != EXPECTED_HEAD:
        raise ValueError(f"formal DB head must be {EXPECTED_HEAD}")
    reviewer = reviewed_by.strip()
    if not reviewer:
        raise ValueError("reviewed_by is required")
    decision = decision.strip().upper()
    if decision not in {"CONFIRMED", "REJECTED"}:
        raise ValueError("decision must be CONFIRMED or REJECTED")
    row = session.get(FactProjectAllocation, allocation_id)
    if row is None:
        raise ValueError(f"unknown allocation: {allocation_id}")
    if not row.is_current or row.status not in {"CANDIDATE", "NEEDS_REVIEW"}:
        raise ValueError("only current CANDIDATE/NEEDS_REVIEW allocations may be reviewed")
    row.status = decision
    row.reviewed_by = reviewer
    row.reviewed_at = datetime.now(timezone.utc)
    if decision == "REJECTED":
        row.is_current = False
    if note:
        row.note = note
    session.flush()
    return {"status": decision, "allocation_id": row.id, "fact_id": row.fact_id, "project_id": row.project_id, "allocation_version": row.allocation_version, "is_current": row.is_current, "reviewed_by": reviewer}


def _write(path: str | None, payload: Any) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if path:
        Path(path).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--fact-id", type=int, required=True)
    common.add_argument("--project-id", type=int, required=True)
    common.add_argument("--net", required=True)
    common.add_argument("--vat", required=True)
    common.add_argument("--gross", required=True)
    common.add_argument("--method", required=True)
    common.add_argument("--confidence", default="HIGH")
    common.add_argument("--status", default="CONFIRMED")
    common.add_argument("--proposal-source", default="HUMAN")
    common.add_argument("--source-document-id", type=int)
    common.add_argument("--rule-version")
    common.add_argument("--reviewed-by")
    common.add_argument("--note")
    plan_parser = sub.add_parser("plan", parents=[common])
    plan_parser.add_argument("--json")
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--plan", required=True)
    apply_parser.add_argument("--expected-plan-digest", required=True)
    apply_parser.add_argument("--confirm-database", required=True)
    apply_parser.add_argument("--json")
    review_parser = sub.add_parser("review")
    review_parser.add_argument("--allocation-id", type=int, required=True)
    review_parser.add_argument("--decision", choices=["CONFIRMED", "REJECTED"], required=True)
    review_parser.add_argument("--reviewed-by", required=True)
    review_parser.add_argument("--note")
    review_parser.add_argument("--confirm-database", required=True)
    review_parser.add_argument("--json")
    args = parser.parse_args()
    database_url = _database_url()
    if args.command in {"apply", "review"} and make_url(database_url).database != args.confirm_database:
        raise SystemExit("--confirm-database does not match DATABASE_URL database")
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    with Session(engine) as session:
        if args.command == "plan":
            payload = make_plan(session, fact_id=args.fact_id, project_id=args.project_id, allocated_net=_money(args.net), allocated_vat=_money(args.vat), allocated_gross=_money(args.gross), allocation_method=args.method, confidence=args.confidence, status=args.status, proposal_source=args.proposal_source, source_document_id=args.source_document_id, rule_version=args.rule_version, reviewed_by=args.reviewed_by, note=args.note)
        elif args.command == "apply":
            plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
            payload = apply_plan(session, plan=plan, expected_plan_digest=args.expected_plan_digest)
            session.commit()
        else:
            payload = review_allocation(session, allocation_id=args.allocation_id, decision=args.decision, reviewed_by=args.reviewed_by, note=args.note)
            session.commit()
    _write(args.json, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
