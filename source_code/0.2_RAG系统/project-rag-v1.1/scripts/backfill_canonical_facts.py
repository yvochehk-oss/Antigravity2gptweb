#!/usr/bin/env python3
"""Backfill Canonical Facts for historical indexed RAG documents."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Document
from app.services.canonical_facts import promote_document_to_canonical_facts


def main() -> None:
    promoted = 0
    skipped = 0
    with SessionLocal() as db:
        documents = db.scalars(
            select(Document).where(Document.parse_status == "INDEXED").order_by(Document.id.asc())
        ).all()
        for doc in documents:
            ids = promote_document_to_canonical_facts(db, doc)
            if ids:
                promoted += len(ids)
            else:
                skipped += 1
        db.commit()
    print(f"canonical facts backfill complete: promoted={promoted} skipped={skipped}")


if __name__ == "__main__":
    main()
