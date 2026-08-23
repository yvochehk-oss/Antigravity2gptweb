"""Database initialization with demo data."""
from datetime import datetime, timezone
from .db import init_db
from .session import get_db_session
from .models import Project


def now() -> str:
    """Get current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run():
    """Initialize database and seed demo project."""
    init_db()
    db = get_db_session()

    try:
        # Check if demo project exists
        existing = db.query(Project).filter(
            Project.project_code == "YB-DEMO-001"
        ).first()

        if not existing:
            db.add(Project(
                project_code="YB-DEMO-001",
                name="宜宾示范项目",
                external_system="construction-tax",
                external_project_id="1",
                note="ProjectRAG V0.2 Optimized 示范知识空间",
                created_at=now(),
                updated_at=now()
            ))
            db.commit()
            print("Created demo project: YB-DEMO-001")
        else:
            print("Demo project already exists: YB-DEMO-001")

    finally:
        db.close()


if __name__ == "__main__":
    run()
