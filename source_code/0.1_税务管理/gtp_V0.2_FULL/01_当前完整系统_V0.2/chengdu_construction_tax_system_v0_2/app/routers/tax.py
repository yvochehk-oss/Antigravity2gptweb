"""V0.2: 法人月度税务台账。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from ..templates import templates

from ..calc import rebuild_tax_ledger
from ..db import SessionLocal
from ..models import TaxRule

router = APIRouter()


@router.get("/tax-ledger", response_class=HTMLResponse)
def tax_ledger(request: Request, period: str = "2026-08"):
    db = SessionLocal()
    rows = rebuild_tax_ledger(db, period)
    unreviewed = db.execute(
        select(TaxRule).where(TaxRule.reviewed == False)  # noqa: E712
    ).scalars().all()
    db.close()
    return templates.TemplateResponse(
        request,
        "tax_ledger.html",
    {

        "request": request, "rows": rows, "period": period,
        "unreviewed": len(unreviewed),
    },
    )