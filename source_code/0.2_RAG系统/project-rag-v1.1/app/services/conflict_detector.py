"""Knowledge conflict detection service for ProjectRAG V0.3.

Detects version conflicts, value mismatches, and period gaps across documents
belonging to the same project. Persists findings to the KnowledgeConflict table.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable
from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from ..models import Document, KnowledgeConflict
from ..logging_config import get_logger

logger = get_logger(__name__)

# Fields where value mismatches indicate a real conflict
_COMPARABLE_FIELDS = [
    "contract_amount",
    "total_amount",
    "amount",
    "tax_vat_input",
    "tax_vat_output",
    "tax_vat_paid",
    "tax_total",
    "tax_income_amount",
    "tax_income_paid",
    "tax_individual_amount",
    "tax_individual_paid",
    "tax_stamp_duty",
    "tax_land",
    "tax_environmental",
    "tax_surtax_urban",
    "tax_surtax_edu",
    "tax_surtax_local_edu",
    "invoice_no",
    "contract_no",
    "period",
    "counterparty_code",
    "entity_code",
    "business_category",
    "document_date",
]

# Document types that count as "contract" documents for clustering
_CONTRACT_DOC_TYPES = {
    "labor_contract",
    "equipment_contract",
    "material_contract",
    "subcontract_contract",
    "main_contract",
    "supplementary_agreement",
}

# Business categories that map to recurring per-period document sets
_RECURRING_CATEGORIES = {
    "labor",
    "equipment",
    "material",
    "subcontract",
    "tax",
}


def _now() -> str:
    """UTC ISO timestamp for record keeping."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ConflictReport:
    """One detected knowledge conflict."""

    project_id: int
    field_name: str
    conflict_type: str  # value_mismatch | version_gap | period_gap
    versions: list[dict] = field(default_factory=list)
    recommended_document_id: int = 0
    detail: str = ""

    def to_dict(self) -> dict:
        """Convert to a plain JSON-serializable dict."""
        return {
            "project_id": self.project_id,
            "field_name": self.field_name,
            "conflict_type": self.conflict_type,
            "versions": list(self.versions),
            "recommended_document_id": self.recommended_document_id,
            "detail": self.detail,
        }


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _doc_to_summary(d: dict) -> dict:
    """Project only the columns we need onto a minimal dict."""
    return {
        "document_id": d.get("id") or d.get("document_id"),
        "filename": d.get("filename", ""),
        "version_label": d.get("version_label", ""),
        "version_status": d.get("version_status", ""),
        "document_date": d.get("document_date", ""),
        "value": d.get("value", ""),
        "entity_code": d.get("entity_code", ""),
        "contract_no": d.get("contract_no", ""),
        "business_category": d.get("business_category", ""),
        "period": d.get("period", ""),
        "document_type": d.get("document_type", ""),
    }


def _is_contract_doc(d: dict) -> bool:
    """Return True if the document is a contract-type document."""
    doc_type = (d.get("document_type") or "").lower()
    if doc_type in _CONTRACT_DOC_TYPES:
        return True
    # Heuristic: documents with a contract_no and a non-empty contract_no
    # but no settlement/record suffix are also treated as contracts
    if d.get("contract_no"):
        return True
    return False


def _safe_float(v) -> float | None:
    """Convert a value to float if possible, else None."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pick_recommended(docs: list[dict]) -> int:
    """Pick the recommended document id from a cluster.

    Selection logic:
    1. Prefer version_status == "effective".
    2. Among those, pick the one with the latest document_date.
    """
    effective = [d for d in docs if (d.get("version_status") or "").lower() == "effective"]
    pool = effective or docs
    if not pool:
        return 0

    def _key(d: dict) -> str:
        return str(d.get("document_date") or "")

    pool_sorted = sorted(pool, key=_key, reverse=True)
    return int(pool_sorted[0].get("id") or pool_sorted[0].get("document_id") or 0)


def _cluster_key(d: dict) -> tuple:
    """Clustering key for contract comparison: (entity_code, contract_no)."""
    return (
        str(d.get("entity_code") or ""),
        str(d.get("contract_no") or ""),
    )


def _period_key(p: str) -> tuple | None:
    """Convert 'YYYY-MM' string to (year, month) tuple. Returns None on failure."""
    if not p or not isinstance(p, str):
        return None
    parts = p.strip().split("-")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None


def _format_period(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def detect_value_conflicts(
    project_id: int,
    documents: list[dict],
) -> list[ConflictReport]:
    """Detect value mismatches among documents of the same contract.

    Clusters contract-like documents by (entity_code, contract_no). For each
    cluster, compares each value field across documents and emits a report
    if more than one distinct non-empty value is found.

    Args:
        project_id: Project ID being analyzed.
        documents: List of dicts with document fields.

    Returns:
        List of ConflictReport instances.
    """
    reports: list[ConflictReport] = []

    clusters: dict[tuple, list[dict]] = {}
    for d in documents:
        if not _is_contract_doc(d):
            continue
        key = _cluster_key(d)
        if not key[1]:
            # skip docs without a contract number
            continue
        clusters.setdefault(key, []).append(d)

    for key, docs in clusters.items():
        if len(docs) < 2:
            continue
        recommended = _pick_recommended(docs)

        for fname in _COMPARABLE_FIELDS:
            values_by_doc: dict[str, list[dict]] = {}
            for d in docs:
                raw = d.get(fname)
                if raw is None or raw == "":
                    continue
                # normalize float values for comparison
                fv = _safe_float(raw)
                if fv is not None:
                    val_key = f"{fv:.4f}"
                else:
                    val_key = str(raw).strip()
                values_by_doc.setdefault(val_key, []).append(d)

            if len(values_by_doc) < 2:
                continue

            versions = [
                {"value": v, "documents": [_doc_to_summary(d) for d in ds]}
                for v, ds in values_by_doc.items()
            ]
            detail = (
                f"字段 {fname} 在合同 {key[1]} 下出现 {len(values_by_doc)} 个不同值。"
                f"推荐以 document_id={recommended} 为准。"
            )
            reports.append(
                ConflictReport(
                    project_id=project_id,
                    field_name=fname,
                    conflict_type="value_mismatch",
                    versions=versions,
                    recommended_document_id=recommended,
                    detail=detail,
                )
            )

    return reports


def detect_period_gaps(
    project_id: int,
    documents: list[dict],
) -> list[ConflictReport]:
    """Detect missing periods in recurring business categories.

    For each business_category that produces recurring per-month documents
    (labor/equipment/material/subcontract/tax), find the contiguous range of
    periods covered and report any gaps.

    Args:
        project_id: Project ID.
        documents: List of document dicts.

    Returns:
        List of ConflictReport instances.
    """
    reports: list[ConflictReport] = []

    # Bucket documents by business_category
    by_cat: dict[str, list[dict]] = {}
    for d in documents:
        cat = (d.get("business_category") or "").lower()
        if not cat or cat not in _RECURRING_CATEGORIES:
            continue
        period = _period_key(d.get("period"))
        if period is None:
            continue
        by_cat.setdefault(cat, []).append({"doc": d, "period": period})

    for cat, items in by_cat.items():
        if len(items) < 2:
            continue
        # unique sorted periods
        unique_periods = sorted({item["period"] for item in items})
        min_y, min_m = unique_periods[0]
        max_y, max_m = unique_periods[-1]

        expected: list[tuple[int, int]] = []
        y, m = min_y, min_m
        while (y, m) <= (max_y, max_m):
            expected.append((y, m))
            m += 1
            if m > 12:
                m = 1
                y += 1

        present = set(unique_periods)
        missing = [p for p in expected if p not in present]
        if not missing:
            continue

        versions: list[dict] = [
            {"period": _format_period(y, m), "status": "missing"}
            for y, m in missing
        ]
        for y, m in unique_periods:
            versions.append({
                "period": _format_period(y, m),
                "status": "present",
                "documents": [
                    _doc_to_summary(item["doc"])
                    for item in items
                    if item["period"] == (y, m)
                ],
            })

        detail = (
            f"业务类别 {cat} 缺少 {len(missing)} 个连续月份："
            + ", ".join(_format_period(y, m) for y, m in missing)
        )
        reports.append(
            ConflictReport(
                project_id=project_id,
                field_name="period",
                conflict_type="period_gap",
                versions=versions,
                recommended_document_id=0,
                detail=detail,
            )
        )

    return reports


def detect_version_conflicts(
    project_id: int,
    documents: list[dict],
) -> list[ConflictReport]:
    """Detect inconsistent version labels within a contract cluster.

    If multiple documents share the same (entity_code, contract_no) and have
    distinct version_label values, but the newest version is not marked
    "effective", that is reported as a version_gap conflict.

    Args:
        project_id: Project ID.
        documents: List of document dicts.

    Returns:
        List of ConflictReport instances.
    """
    reports: list[ConflictReport] = []

    clusters: dict[tuple, list[dict]] = {}
    for d in documents:
        if not d.get("contract_no"):
            continue
        key = _cluster_key(d)
        clusters.setdefault(key, []).append(d)

    for key, docs in clusters.items():
        if len(docs) < 2:
            continue

        labels = sorted({(d.get("version_label") or "V1") for d in docs})
        if len(labels) < 2:
            continue

        # Find the latest version by document_date regardless of status
        latest = max(
            docs,
            key=lambda d: (str(d.get("document_date") or ""), int(d.get("id") or d.get("document_id") or 0)),
        )
        latest_status = (latest.get("version_status") or "").lower()

        if latest_status == "effective":
            # newest version is effective → no gap
            continue

        recommended = _pick_recommended(docs)
        versions = [_doc_to_summary(d) for d in docs]
        detail = (
            f"合同 {key[1]} (实体 {key[0]}) 有 {len(labels)} 个版本 "
            f"({', '.join(labels)})，最新版本状态为 "
            f"'{latest_status or 'unknown'}'，未生效。"
        )
        reports.append(
            ConflictReport(
                project_id=project_id,
                field_name="version_label",
                conflict_type="version_gap",
                versions=versions,
                recommended_document_id=recommended,
                detail=detail,
            )
        )

    return reports


def _persist_reports(db: Session, project_id: int, reports: Iterable[ConflictReport]) -> int:
    """Persist ConflictReport objects to the KnowledgeConflict table.

    Returns the number of records persisted.
    """
    import json

    count = 0
    # Remove existing open conflicts for this project before reinserting
    db.execute(
        delete(KnowledgeConflict).where(
            KnowledgeConflict.project_id == project_id,
            KnowledgeConflict.status == "open",
        )
    )

    now = _now()
    for r in reports:
        row = KnowledgeConflict(
            project_id=r.project_id,
            field_name=r.field_name,
            conflict_type=r.conflict_type,
            document_ids_json=json.dumps(
                [
                    v.get("document_id")
                    for v in r.versions
                    if isinstance(v, dict) and "document_id" in v
                ],
                ensure_ascii=False,
            ),
            detail_json=json.dumps(
                {
                    "versions": r.versions,
                    "recommended_document_id": r.recommended_document_id,
                    "detail": r.detail,
                },
                ensure_ascii=False,
            ),
            status="open",
            detected_at=now,
        )
        db.add(row)
        count += 1

    db.commit()
    return count


def run_conflict_detection(
    db: Session,
    project_id: int,
    persist: bool = True,
) -> list[ConflictReport]:
    """Run all conflict detection passes for a project.

    Args:
        db: SQLAlchemy session.
        project_id: Project ID to analyze.
        persist: If True, persist results to KnowledgeConflict table.

    Returns:
        Combined list of ConflictReport objects.
    """
    docs = db.execute(
        select(Document).where(Document.project_id == project_id)
    ).scalars().all()

    doc_dicts = [
        {
            "id": d.id,
            "filename": d.filename,
            "document_type": d.document_type,
            "entity_code": d.entity_code,
            "contract_no": d.contract_no,
            "period": d.period,
            "document_date": d.document_date,
            "version_label": d.version_label,
            "version_status": d.version_status,
            "business_category": d.business_category,
            "counterparty_code": d.counterparty_code,
            "invoice_no": d.invoice_no,
            # tax / amount fields
            "tax_vat_input": d.tax_vat_input,
            "tax_vat_output": d.tax_vat_output,
            "tax_vat_paid": d.tax_vat_paid,
            "tax_total": d.tax_total,
            "tax_income_amount": d.tax_income_amount,
            "tax_income_paid": d.tax_income_paid,
            "tax_individual_amount": d.tax_individual_amount,
            "tax_individual_paid": d.tax_individual_paid,
            "tax_stamp_duty": d.tax_stamp_duty,
            "tax_land": d.tax_land,
            "tax_environmental": d.tax_environmental,
            "tax_surtax_urban": d.tax_surtax_urban,
            "tax_surtax_edu": d.tax_surtax_edu,
            "tax_surtax_local_edu": d.tax_surtax_local_edu,
        }
        for d in docs
    ]

    value_reports = detect_value_conflicts(project_id, doc_dicts)
    period_reports = detect_period_gaps(project_id, doc_dicts)
    version_reports = detect_version_conflicts(project_id, doc_dicts)

    all_reports: list[ConflictReport] = []
    all_reports.extend(value_reports)
    all_reports.extend(period_reports)
    all_reports.extend(version_reports)

    logger.info(
        f"Conflict detection for project {project_id}: "
        f"{len(value_reports)} value, {len(period_reports)} period, "
        f"{len(version_reports)} version conflicts"
    )

    if persist and all_reports:
        saved = _persist_reports(db, project_id, all_reports)
        logger.info(f"Persisted {saved} conflict records for project {project_id}")

    return all_reports