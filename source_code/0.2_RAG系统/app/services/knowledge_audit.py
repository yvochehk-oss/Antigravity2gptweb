"""Knowledge Audit V2.0 for ProjectRAG V0.3.

Replaces the inline audit logic from api_project_audit() in main.py with a
standalone module that produces richer diagnostics: counts, coverage,
completeness, recency, conflicts, parse failures, metadata gaps, issues, and
recommendations.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..config import METADATA_CONFIDENCE_MIN, PARSE_QUALITY_REVIEW_THRESHOLD
from ..logging_config import get_logger
from ..models import Document, IngestJob
from .conflict_detector import run_conflict_detection, ConflictReport
from .metadata_confidence import (
    score_metadata_confidence,
    aggregate_confidence,
    needs_review,
)

logger = get_logger(__name__)

# Five canonical business categories tracked for coverage
COVERAGE_CATEGORIES = ["material", "labor", "equipment", "subcontract", "tax"]

# Tax sub-categories tracked for tax coverage
TAX_CATEGORIES = ["vat", "enterprise_income", "individual_income", "surtax"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_period_year_month(period: str) -> tuple[int, int] | None:
    """Convert a 'YYYY-MM' period to (year, month) or None on failure."""
    if not period or not isinstance(period, str):
        return None
    parts = period.strip().split("-")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None


def _format_period(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


@dataclass
class AuditResult:
    """Aggregated audit result for a single project."""

    project_id: int
    counts: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    completeness: dict = field(default_factory=dict)
    recency: dict = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)
    parse_failures: list[dict] = field(default_factory=list)
    metadata_gaps: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to a plain JSON-serializable dict."""
        return {
            "project_id": self.project_id,
            "counts": dict(self.counts),
            "coverage": dict(self.coverage),
            "completeness": dict(self.completeness),
            "recency": dict(self.recency),
            "conflicts": list(self.conflicts),
            "parse_failures": list(self.parse_failures),
            "metadata_gaps": list(self.metadata_gaps),
            "issues": list(self.issues),
            "recommendations": list(self.recommendations),
        }


# ------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------

def _summarize_doc(d: Document) -> dict:
    """Project a Document row into the dict shape the audit expects."""
    return {
        "id": d.id,
        "project_id": d.project_id,
        "filename": d.filename,
        "document_type": d.document_type,
        "entity_code": d.entity_code,
        "counterparty_code": d.counterparty_code,
        "business_category": d.business_category,
        "tax_category": getattr(d, "tax_category", "") or "",
        "period": d.period or "",
        "document_date": d.document_date or "",
        "contract_no": d.contract_no or "",
        "parse_status": d.parse_status,
        "parse_message": d.parse_message or "",
        "parse_attempts": d.parse_attempts,
        "duplicate_of_id": d.duplicate_of_id,
        "version_label": d.version_label,
        "version_status": d.version_status,
        "metadata_confidence": d.metadata_confidence,
        "metadata_source": d.metadata_source,
        "created_at": d.created_at,
        "updated_at": d.updated_at,
    }


def _compute_counts(docs: list[dict]) -> dict:
    """Compute basic counts (mirrors V0.2 logic)."""
    counts = {
        "total": len(docs),
        "indexed": 0,
        "duplicates": 0,
        "queued": 0,
        "waiting_mineru": 0,
        "parse_failed": 0,
        "unclassified": 0,
        "missing_business_category": 0,
    }
    for d in docs:
        if d.get("parse_status") == "INDEXED":
            counts["indexed"] += 1
        if d.get("duplicate_of_id"):
            counts["duplicates"] += 1
        if d.get("parse_status") in ("QUEUED", "UPLOADED"):
            counts["queued"] += 1
        if d.get("parse_status") == "WAITING_MINERU":
            counts["waiting_mineru"] += 1
        if d.get("parse_status") == "PARSE_FAILED":
            counts["parse_failed"] += 1
        if not d.get("document_type") or d.get("document_type") == "other":
            counts["unclassified"] += 1
        if not d.get("business_category"):
            counts["missing_business_category"] += 1
    return counts


def _compute_coverage(docs: list[dict]) -> dict:
    """Compute business_category and tax coverage."""
    cats: set[str] = set()
    tax_cats: set[str] = set()
    types: set[str] = set()
    for d in docs:
        if d.get("business_category"):
            cats.add(d["business_category"])
        if d.get("tax_category"):
            tax_cats.add(d["tax_category"])
        if d.get("document_type"):
            types.add(d["document_type"])

    coverage: dict = {}
    for cat in COVERAGE_CATEGORIES:
        coverage[cat] = cat in cats

    coverage["tax_detail"] = {t: (t in tax_cats) for t in TAX_CATEGORIES}
    coverage["missing_tax"] = sum(
        1 for t in TAX_CATEGORIES if t not in tax_cats
    )
    coverage["has_main_contract"] = "main_contract" in types
    return coverage


def _compute_completeness(docs: list[dict]) -> dict:
    """Compute per-month coverage for documents that carry a period.

    Returns:
        Dict with:
        - by_category: {category: {period: count}}
        - missing_periods_by_category: {category: ['YYYY-MM', ...]}
        - total_periods_seen: int
    """
    by_category: dict[str, dict[str, int]] = {}
    for d in docs:
        period = d.get("period")
        cat = d.get("business_category")
        if not period or not cat:
            continue
        bucket = by_category.setdefault(cat, {})
        bucket[period] = bucket.get(period, 0) + 1

    missing_by_cat: dict[str, list[str]] = {}
    for cat, periods in by_category.items():
        parsed = []
        for p in periods:
            ym = _parse_period_year_month(p)
            if ym is None:
                continue
            parsed.append(ym)
        if len(parsed) < 2:
            continue
        parsed_sorted = sorted(set(parsed))
        min_y, min_m = parsed_sorted[0]
        max_y, max_m = parsed_sorted[-1]

        expected: list[tuple[int, int]] = []
        y, m = min_y, min_m
        while (y, m) <= (max_y, max_m):
            expected.append((y, m))
            m += 1
            if m > 12:
                m = 1
                y += 1

        present = set(parsed_sorted)
        missing = [_format_period(y, m) for y, m in expected if (y, m) not in present]
        if missing:
            missing_by_cat[cat] = missing

    all_periods = {p for periods in by_category.values() for p in periods}
    return {
        "by_category": by_category,
        "missing_periods_by_category": missing_by_cat,
        "total_periods_seen": len(all_periods),
    }


def _compute_recency(docs: list[dict]) -> dict:
    """Compute last-update timestamp and most recent period per category."""
    by_category: dict[str, dict] = {}
    for d in docs:
        cat = d.get("business_category")
        if not cat:
            continue
        bucket = by_category.setdefault(
            cat,
            {"last_updated_at": "", "last_period": "", "document_count": 0},
        )
        bucket["document_count"] += 1
        updated = d.get("updated_at") or ""
        if updated and (not bucket["last_updated_at"] or updated > bucket["last_updated_at"]):
            bucket["last_updated_at"] = updated
        period = d.get("period") or ""
        # Compare periods as (year, month) for correct ordering
        cur = _parse_period_year_month(bucket["last_period"])
        new = _parse_period_year_month(period)
        if new is not None and (cur is None or new > cur):
            bucket["last_period"] = period

    return {"by_category": by_category}


def _collect_parse_failures(db: Session, docs: list[dict]) -> list[dict]:
    """Collect docs that failed parsing or have low parse_quality_score.

    Pulls the latest IngestJob per document to obtain parse_quality_score.
    """
    doc_ids = [d["id"] for d in docs if d.get("id")]
    if not doc_ids:
        return []

    latest_jobs_subq = (
        select(
            IngestJob.document_id,
            func.max(IngestJob.id).label("max_id"),
        )
        .where(IngestJob.document_id.in_(doc_ids))
        .group_by(IngestJob.document_id)
        .subquery()
    )
    jobs = db.execute(
        select(IngestJob).join(
            latest_jobs_subq, IngestJob.id == latest_jobs_subq.c.max_id
        )
    ).scalars().all()
    job_by_doc = {j.document_id: j for j in jobs}

    failures: list[dict] = []
    for d in docs:
        doc_id = d.get("id")
        status = d.get("parse_status")
        job = job_by_doc.get(doc_id)
        quality = float(getattr(job, "parse_quality_score", 0.0) or 0.0) if job else 0.0

        reasons = []
        if status == "PARSE_FAILED":
            reasons.append("parse_status=PARSE_FAILED")
        if job and quality < PARSE_QUALITY_REVIEW_THRESHOLD:
            reasons.append(f"parse_quality_score={quality:.1f} < {PARSE_QUALITY_REVIEW_THRESHOLD}")

        if reasons:
            failures.append({
                "document_id": doc_id,
                "filename": d.get("filename", ""),
                "parse_status": status,
                "parse_quality_score": quality,
                "parse_message": d.get("parse_message", ""),
                "reasons": reasons,
            })
    return failures


def _collect_metadata_gaps(docs: list[dict]) -> list[dict]:
    """Collect documents whose metadata confidence is below the review threshold.

    The threshold used here is METADATA_CONFIDENCE_MIN (per-document aggregate).
    Each gap entry includes the per-field breakdown for triage.
    """
    gaps: list[dict] = []
    for d in docs:
        metadata_payload = {
            "document_type": d.get("document_type", ""),
            "entity_code": d.get("entity_code", ""),
            "counterparty_code": d.get("counterparty_code", ""),
            "business_category": d.get("business_category", ""),
            "period": d.get("period", ""),
            "document_date": d.get("document_date", ""),
            "contract_no": d.get("contract_no", ""),
        }
        # Include invoice_no if present in the doc dict
        if "invoice_no" in d:
            metadata_payload["invoice_no"] = d.get("invoice_no", "")

        source = d.get("metadata_source") or "filename"
        confs = score_metadata_confidence(metadata_payload, source)
        overall = aggregate_confidence(confs)

        # Need to look up stored metadata_confidence if available
        stored = float(d.get("metadata_confidence") or 0.0)
        effective = overall if overall > 0 else stored

        if effective < METADATA_CONFIDENCE_MIN or needs_review(metadata_payload, confs):
            weak_fields = [
                {"field": k, "confidence": round(v.confidence, 3)}
                for k, v in confs.items()
                if v.confidence < METADATA_CONFIDENCE_MIN
            ]
            gaps.append({
                "document_id": d.get("id"),
                "filename": d.get("filename", ""),
                "overall_confidence": round(effective, 3),
                "weak_fields": weak_fields,
                "metadata_source": source,
            })
    return gaps


def _build_issues_and_recommendations(result: AuditResult) -> None:
    """Populate issues and recommendations based on existing result fields."""
    counts = result.counts
    coverage = result.coverage
    completeness = result.completeness

    # ---- Count-based issues ----
    for key, msg in [
        ("waiting_mineru", "存在等待 MinerU 解析的资料"),
        ("parse_failed", "存在解析失败资料"),
        ("unclassified", "存在未分类资料，需要确认元数据"),
        ("duplicates", "存在重复文件，系统已阻止重复索引"),
        ("missing_business_category", "存在缺少业务类别的资料"),
    ]:
        if counts.get(key):
            result.issues.append({
                "type": key.upper(),
                "count": counts[key],
                "message": msg,
            })

    # ---- Coverage gaps ----
    for cat in COVERAGE_CATEGORIES:
        if not coverage.get(cat):
            result.issues.append({
                "type": "CATEGORY_GAP",
                "category": cat,
                "message": f"尚未识别到 {cat} 类资料；如项目存在该业务，请补充",
            })
            result.recommendations.append(f"建议检查项目中是否存在 {cat} 类业务资料")

    if not coverage.get("has_main_contract"):
        result.issues.append({
            "type": "MISSING_MAIN_CONTRACT",
            "count": 1,
            "message": "未识别到项目主合同",
        })

    missing_tax = coverage.get("missing_tax", 0)
    if missing_tax:
        result.issues.append({
            "type": "TAX_CATEGORY_GAP",
            "count": missing_tax,
            "message": f"税务细分类别缺失 {missing_tax} 项（VAT/所得税/个税/附加税）",
        })

    # ---- Completeness issues ----
    missing_periods = completeness.get("missing_periods_by_category", {}) or {}
    for cat, periods in missing_periods.items():
        result.issues.append({
            "type": "PERIOD_GAP",
            "category": cat,
            "count": len(periods),
            "missing_periods": periods,
            "message": f"业务类别 {cat} 缺少 {len(periods)} 个连续月份",
        })
        result.recommendations.append(
            f"补齐 {cat} 类 {', '.join(periods[:6])}"
            + (" 等" if len(periods) > 6 else "")
        )

    # ---- Conflict-driven issues ----
    for c in result.conflicts:
        result.issues.append({
            "type": "CONFLICT",
            "conflict_type": c.get("conflict_type"),
            "field_name": c.get("field_name"),
            "recommended_document_id": c.get("recommended_document_id"),
            "detail": c.get("detail", ""),
        })

    # ---- Parse failures ----
    if result.parse_failures:
        result.issues.append({
            "type": "PARSE_FAILURES",
            "count": len(result.parse_failures),
            "message": "部分文档解析失败或解析质量低于阈值",
        })
        result.recommendations.append("检查解析失败的文件，可能是格式问题或文件损坏")

    # ---- Metadata gaps ----
    if result.metadata_gaps:
        result.issues.append({
            "type": "METADATA_GAPS",
            "count": len(result.metadata_gaps),
            "message": "存在元数据置信度低于阈值的文档",
        })
        result.recommendations.append("建议批量复核元数据置信度较低的文档")

    # ---- Generic worker hints ----
    if counts.get("queued", 0) > counts.get("indexed", 0):
        result.recommendations.append("仍有文档在队列中等待处理，请确认 Worker 正在运行")

    if counts.get("unclassified", 0) > 0:
        result.recommendations.append("建议批量审核未分类文档，确保元数据准确")


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def audit_knowledge(
    db: Session,
    project_id: int,
    run_conflict_detection: bool = True,
) -> AuditResult:
    """Run the full audit pipeline for a project.

    Args:
        db: SQLAlchemy session.
        project_id: Project to audit.
        run_conflict_detection: If True, also run conflict detection and
            persist results to KnowledgeConflict.

    Returns:
        AuditResult dataclass containing all diagnostics.
    """
    docs = db.execute(
        select(Document).where(Document.project_id == project_id)
    ).scalars().all()
    doc_dicts = [_summarize_doc(d) for d in docs]

    result = AuditResult(project_id=project_id)

    # Counts (preserves V0.2 behavior)
    result.counts = _compute_counts(doc_dicts)

    # Coverage
    result.coverage = _compute_coverage(doc_dicts)

    # Completeness
    result.completeness = _compute_completeness(doc_dicts)

    # Recency
    result.recency = _compute_recency(doc_dicts)

    # Parse failures
    result.parse_failures = _collect_parse_failures(db, doc_dicts)

    # Metadata gaps
    result.metadata_gaps = _collect_metadata_gaps(doc_dicts)

    # Conflicts (optional)
    if run_conflict_detection:
        conflicts: list[ConflictReport] = []
        try:
            conflicts = run_conflict_detection(db, project_id, persist=True)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Conflict detection failed for project {project_id}: {e}")
        result.conflicts = [c.to_dict() for c in conflicts]

    # Issues + recommendations
    _build_issues_and_recommendations(result)

    logger.info(
        f"Audit complete for project {project_id}: "
        f"{result.counts.get('total', 0)} docs, "
        f"{len(result.parse_failures)} parse failures, "
        f"{len(result.metadata_gaps)} metadata gaps, "
        f"{len(result.conflicts)} conflicts"
    )

    return result