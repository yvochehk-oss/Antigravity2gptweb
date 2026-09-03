"""Phase 3 retirement guard for legacy Tax/RAG and VAT bypass channels.

Canonical Facts remain the durable business-fact write boundary.  FVAT-3 also
retires the pre-statutory TaxLedger rebuild routes and replaces the legacy
entity-tax-ledger HTTP collection with a single-scope compatibility proxy to
the Canonical Formal VAT statutory resource.
"""
from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError

from ..db import SessionLocal
from ..dependencies import require_role
from .formal_vat_statutory import (
    FormalVatStatutoryResourceIntegrityError,
    FormalVatStatutoryResourceNotFoundError,
    get_formal_vat_statutory_resource,
)
from .legal_entity_fact_periods import LegalEntityNotFoundError


_RETIRED_POST_PATHS = (
    "/rag-sync/sync",
    "/rag-sync/sync-batch",
    "/rag-sync/sync-pending",
    "/sync-pending",
    "/api/sync-pending",
    "/rag-sync/pending/{pending_id}/confirm",
    "/rag-sync/pending/{pending_id}/confirm-contract-and-create-parties",
    "/rag-sync/pending/{pending_id}/reject",
)
_FORMAL_VAT_RETIRED_POST_PATHS = (
    "/api/tax-ledger/rebuild",
    "/tax-ledger/rebuild",
)
_LEGACY_ENTITY_TAX_LEDGER_PATH = "/api/entity-tax-ledger"
_CANONICAL_FORMAL_VAT_READ_TEMPLATE = (
    "/api/v3/legal-entities/{entity_code}/statutory-vat?period={period}"
)
_CANONICAL_FORMAL_VAT_REBUILD_TEMPLATE = (
    "/api/v3/legal-entities/{entity_code}/statutory-vat/rebuild?period={period}"
)
_PERIOD_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
_reader_dependency = Depends(require_role("admin", "operator"))


def _remove_routes(app: FastAPI, paths: Iterable[str], method: str = "POST") -> int:
    targets = set(paths)
    method = method.upper()
    kept = []
    removed = 0
    for route in app.router.routes:
        route_path = getattr(route, "path", "")
        methods = {
            str(item).upper()
            for item in (getattr(route, "methods", None) or set())
        }
        if route_path in targets and method in methods:
            removed += 1
            continue
        kept.append(route)
    app.router.routes[:] = kept
    return removed


async def _gone(_request: Request) -> None:
    raise HTTPException(
        status_code=410,
        detail=(
            "Phase 3 已物理退役旧 Tax/RAG 同步写通道；"
            "业务事实只能由 RAG Canonical Facts 写入，Tax 为只读消费者。"
        ),
    )


async def _formal_vat_legacy_rebuild_gone(_request: Request) -> None:
    """Fail closed before any legacy TaxLedger rebuild code can execute."""
    raise HTTPException(
        status_code=410,
        detail={
            "code": "LEGACY_TAX_LEDGER_REBUILD_RETIRED",
            "detail": (
                "旧 TaxLedger 重建写入口已永久退役；正式 VAT 只能通过 "
                "Canonical Statutory 资源按法人、期间确定性重建。"
            ),
            "canonical_endpoint_template": _CANONICAL_FORMAL_VAT_REBUILD_TEMPLATE,
        },
    )


def _legacy_entity_tax_ledger_proxy(
    period: str | None = Query(default=None, min_length=7, max_length=7),
    entity: str | None = Query(default=None, min_length=1, max_length=64),
    entity_code: str | None = Query(default=None, min_length=1, max_length=64),
    project_id: int | None = Query(default=None, ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    _user=_reader_dependency,
) -> dict[str, Any]:
    """Proxy the legacy read URL to the one canonical statutory VAT resource.

    The old collection semantics are intentionally not reproduced.  A formal
    VAT resource is one legal entity and one tax period, so legacy callers must
    provide that same scope.  Pagination arguments remain accepted only so old
    clients do not fail before the scope migration error can be explained.
    """
    del page, page_size, _user
    if project_id is not None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "FORMAL_VAT_PROJECT_SCOPE_NOT_ALLOWED",
                "detail": (
                    "/api/entity-tax-ledger 不接受 project_id；正式 VAT 必须按法人、期间读取。"
                ),
                "canonical_endpoint_template": _CANONICAL_FORMAL_VAT_READ_TEMPLATE,
            },
        )
    if period is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "FORMAL_VAT_PERIOD_REQUIRED",
                "detail": "legacy 兼容入口必须显式提供 period=YYYY-MM。",
                "canonical_endpoint_template": _CANONICAL_FORMAL_VAT_READ_TEMPLATE,
            },
        )
    if not _PERIOD_RE.fullmatch(period):
        raise HTTPException(status_code=422, detail="period 必须为 YYYY-MM 格式")

    legacy_entity = str(entity or "").strip()
    canonical_entity = str(entity_code or "").strip()
    if legacy_entity and canonical_entity and legacy_entity.casefold() != canonical_entity.casefold():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "FORMAL_VAT_ENTITY_SCOPE_CONFLICT",
                "detail": "entity 与 entity_code 不能冲突。",
                "canonical_endpoint_template": _CANONICAL_FORMAL_VAT_READ_TEMPLATE,
            },
        )
    wanted_entity = canonical_entity or legacy_entity
    if not wanted_entity:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "FORMAL_VAT_ENTITY_REQUIRED",
                "detail": "legacy 兼容入口必须显式提供 entity_code（或旧别名 entity）。",
                "canonical_endpoint_template": _CANONICAL_FORMAL_VAT_READ_TEMPLATE,
            },
        )

    db = SessionLocal()
    try:
        return get_formal_vat_statutory_resource(db, wanted_entity, period)
    except LegalEntityNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "LEGAL_ENTITY_NOT_FOUND",
                "detail": "未找到有效的内部法人主体",
            },
        ) from None
    except FormalVatStatutoryResourceNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FORMAL_VAT_STATUTORY_RESOURCE_NOT_FOUND",
                "detail": "该法人及期间尚无正式 VAT 法定资源；读取端点不会现场重算。",
            },
        ) from None
    except FormalVatStatutoryResourceIntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "FORMAL_VAT_STATUTORY_RESOURCE_INVALID",
                "detail": str(exc),
            },
        ) from exc
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Formal VAT 法定资源数据源暂时不可用，请稍后重试。",
        ) from None
    finally:
        db.close()


def install_phase3_retirement(app: FastAPI) -> dict[str, object]:
    """Replace every retired legacy boundary with a fail-closed cutover route."""
    removed = _remove_routes(app, _RETIRED_POST_PATHS)
    removed_formal_vat_writers = _remove_routes(app, _FORMAL_VAT_RETIRED_POST_PATHS)
    removed_legacy_formal_vat_readers = _remove_routes(
        app, (_LEGACY_ENTITY_TAX_LEDGER_PATH,), method="GET"
    )

    for index, path in enumerate(_RETIRED_POST_PATHS, start=1):
        app.add_api_route(
            path,
            _gone,
            methods=["POST"],
            status_code=410,
            name=f"phase3_retired_tax_write_{index}",
            tags=["deprecated"],
        )

    for index, path in enumerate(_FORMAL_VAT_RETIRED_POST_PATHS, start=1):
        app.add_api_route(
            path,
            _formal_vat_legacy_rebuild_gone,
            methods=["POST"],
            status_code=410,
            name=f"fvat3_retired_tax_ledger_rebuild_{index}",
            tags=["deprecated"],
        )

    app.add_api_route(
        _LEGACY_ENTITY_TAX_LEDGER_PATH,
        _legacy_entity_tax_ledger_proxy,
        methods=["GET"],
        response_model=None,
        name="fvat3_legacy_entity_tax_ledger_proxy",
        summary="Legacy 法人 VAT URL -> Canonical Formal VAT Statutory Resource",
        tags=["deprecated"],
    )

    return {
        "phase": 3,
        "source_of_truth": "canonical_facts",
        "removed_legacy_writers": removed + removed_formal_vat_writers,
        "retired_paths": list(_RETIRED_POST_PATHS) + list(_FORMAL_VAT_RETIRED_POST_PATHS),
        "formal_vat": {
            "source_of_truth": "formal_vat_statutory",
            "removed_legacy_writers": removed_formal_vat_writers,
            "removed_legacy_readers": removed_legacy_formal_vat_readers,
            "legacy_read_proxy": _LEGACY_ENTITY_TAX_LEDGER_PATH,
            "canonical_read_template": _CANONICAL_FORMAL_VAT_READ_TEMPLATE,
            "canonical_rebuild_template": _CANONICAL_FORMAL_VAT_REBUILD_TEMPLATE,
        },
    }


__all__ = ["install_phase3_retirement"]
