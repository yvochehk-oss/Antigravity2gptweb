"""Explicit PostgreSQL bootstrap hook.

Normal service startup never calls this module.  A seed operation is opt-in
and requires an operator-supplied project code/name, so a fresh database can
never acquire a historical placeholder project by accident.
Alembic remains the only schema writer.
"""

import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .db import init_db
from .models import Project
from .session import get_db_session


def now() -> str:
    """Get current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run():
    """Validate the schema and optionally insert one explicit project.

    Set ``PROJECT_RAG_SEED_PROJECT_CODE`` and
    ``PROJECT_RAG_SEED_PROJECT_NAME`` for a deliberate bootstrap.  With no
    code configured the operation is a safe, idempotent schema-readiness
    check and performs no data write.
    """
    init_db()
    project_code = os.getenv("PROJECT_RAG_SEED_PROJECT_CODE", "").strip()
    project_name = os.getenv("PROJECT_RAG_SEED_PROJECT_NAME", "").strip()
    if not project_code:
        print("PostgreSQL schema is ready; no project seed requested")
        return
    if not project_name:
        raise RuntimeError("PROJECT_RAG_SEED_PROJECT_NAME is required when PROJECT_RAG_SEED_PROJECT_CODE is set")
    location = os.getenv("PROJECT_RAG_SEED_LOCATION", "").strip()
    amount_text = os.getenv("PROJECT_RAG_SEED_CONTRACT_AMOUNT", "").strip()
    if not location or not amount_text:
        raise RuntimeError(
            "PROJECT_RAG_SEED_LOCATION and PROJECT_RAG_SEED_CONTRACT_AMOUNT "
            "are required for an explicit project bootstrap"
        )
    try:
        contract_amount = Decimal(amount_text)
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError("PROJECT_RAG_SEED_CONTRACT_AMOUNT must be a decimal amount") from exc
    if contract_amount < 0:
        raise RuntimeError("PROJECT_RAG_SEED_CONTRACT_AMOUNT cannot be negative")

    db = get_db_session()

    try:
        # Check if the explicitly requested project already exists.
        existing = db.query(Project).filter(Project.project_code == project_code).first()

        if not existing:
            db.add(
                Project(
                    project_code=project_code,
                    name=project_name,
                    external_system="construction-tax",
                    external_project_id=os.getenv("PROJECT_RAG_SEED_EXTERNAL_ID", "").strip(),
                    contract_amount=contract_amount,
                    location=location,
                    note=os.getenv("PROJECT_RAG_SEED_NOTE", "").strip(),
                    created_at=now(),
                    updated_at=now(),
                )
            )
            db.commit()
            print(f"Created project: {project_code}")
        else:
            print(f"Project already exists: {project_code}")

    finally:
        db.close()


if __name__ == "__main__":
    run()
