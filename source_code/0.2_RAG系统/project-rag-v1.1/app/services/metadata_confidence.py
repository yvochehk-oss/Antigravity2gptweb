"""Metadata confidence scoring service for ProjectRAG V0.3.

Provides per-field confidence scores based on extraction source and heuristic rules.
"""
from dataclasses import dataclass
import re
from ..logging_config import get_logger

logger = get_logger(__name__)

from ..domain.entities import (
    CANONICAL_ENTITY_CODES,
    VIRTUAL_ENTITY_CODES,
    is_canonical_entity_code,
    is_canonical_external_code,
    is_canonical_internal_code,
    is_canonical_party_code,
)
from ..logging_config import get_logger

logger = get_logger(__name__)

# Confidence ranges by source
_SOURCE_RANGES: dict[str, tuple[float, float]] = {
    "filename":      (0.55, 0.85),
    "content_refine": (0.70, 0.95),
    "llm":          (0.75, 0.95),
    "user":         (1.0, 1.0),
}

# Business categories that represent contract documents
_CONTRACT_CATEGORIES = {
    "labor", "equipment", "material", "subcontract",
    "labor_contract", "equipment_contract", "material_contract", "subcontract_contract",
    "main_contract", "supplementary_agreement",
}


@dataclass
class FieldConfidence:
    """A metadata field value together with its confidence score."""
    value: str
    confidence: float


def score_metadata_confidence(
    metadata: dict,
    source: str = "filename"
) -> dict[str, FieldConfidence]:
    """Score confidence for each field in a metadata dict.

    Args:
        metadata: Dict of metadata fields. Expected keys include
            document_type, entity_code, counterparty_code, business_category,
            period, document_date, contract_no, invoice_no, and any other
            string-valued fields present.
        source: One of "filename" | "content_refine" | "llm" | "user",
            indicating how the metadata was extracted.

    Returns:
        Dict mapping field_name -> FieldConfidence(value, confidence).
        Fields that are absent from the input dict are omitted from the result.
    """
    base_range = _SOURCE_RANGES.get(source, (0.50, 0.90))
    base_low, base_high = base_range

    scores: dict[str, FieldConfidence] = {}

    for field, value in metadata.items():
        if not isinstance(field, str):
            continue

        # Empty value → zero confidence
        if value is None or str(value).strip() == "":
            scores[field] = FieldConfidence(value=str(value) if value is not None else "", confidence=0.0)
            continue

        raw = str(value).strip()
        conf = _score_field(field, raw, source, base_low, base_high, metadata)
        scores[field] = FieldConfidence(value=raw, confidence=conf)

    return scores


def _score_field(
    field: str,
    raw: str,
    source: str,
    base_low: float,
    base_high: float,
    metadata: dict | None = None,
) -> float:
    """Compute a confidence score for a single field value.

    Args:
        field: Field name.
        raw: Field value string.
        source: Extraction source.
        base_low: Base low for source range.
        base_high: Base high for source range.

    Returns:
        Confidence score in [0.0, 1.0].
    """
    conf = (base_low + base_high) / 2.0

    fn = field.lower()

    # ---- General format bonuses ----
    if fn == "period":
        # YYYY-MM or YYYY-M format gets a boost
        if re.fullmatch(r"20\d{2}-(?:0?[1-9]|1[0-2])", raw):
            conf = min(conf + 0.10, 1.0)
    elif fn == "entity_code":
        # A/B/C/D are roles, never entity identifiers.  Only a canonical
        # code can score as an entity; unresolved candidates are intentionally
        # low confidence until the canonical cache validates them.
        if raw in VIRTUAL_ENTITY_CODES or not is_canonical_party_code(raw):
            return 0.0
        resolution = str((metadata or {}).get("entity_resolution_status", "")).upper()
        if resolution == "RESOLVED":
            conf = min(conf + 0.08, 1.0)
        else:
            # Lexically valid is not the same as master-resolved.
            conf = min(conf * 0.5, 0.45)
    elif fn == "invoice_no":
        # Chinese invoice numbers are typically 12 or 20 digits
        digits = re.sub(r"\D", "", raw)
        if len(digits) == 12 or len(digits) == 20:
            conf = min(conf + 0.10, 1.0)
        elif len(digits) >= 8:
            conf = min(conf + 0.05, 1.0)
    elif fn == "document_type":
        # Not "other" means classification succeeded
        if raw.lower() not in ("other", "其它"):
            conf = min(conf + 0.08, 1.0)

    # ---- Source-specific adjustments ----
    if source == "filename":
        # filename extraction is less reliable
        conf = min(conf, base_high - 0.05)
    elif source == "content_refine":
        # second-pass refinement is more reliable
        conf = max(conf, base_low + 0.10)
    elif source == "user":
        conf = 1.0

    return round(min(max(conf, 0.0), 1.0), 3)


def aggregate_confidence(field_confs: dict[str, FieldConfidence]) -> float:
    """Compute a weighted average confidence across all scored fields.

    High-weight fields (entity_code, document_type, business_category, period)
    contribute more heavily to the aggregate score.

    Args:
        field_confs: Dict from score_metadata_confidence().

    Returns:
        Weighted average confidence in [0.0, 1.0].
        Returns 0.0 if the input dict is empty.
    """
    # Weights for key fields
    HIGH_WEIGHT_FIELDS = {
        "entity_code", "document_type", "business_category", "period"
    }
    MEDIUM_WEIGHT_FIELDS = {
        "contract_no", "invoice_no", "document_date", "counterparty_code"
    }

    total_weight = 0.0
    weighted_sum = 0.0

    for field, fc in field_confs.items():
        if field in HIGH_WEIGHT_FIELDS:
            weight = 2.0
        elif field in MEDIUM_WEIGHT_FIELDS:
            weight = 1.0
        else:
            weight = 0.5

        weighted_sum += fc.confidence * weight
        total_weight += weight

    if total_weight == 0:
        return 0.0

    return round(weighted_sum / total_weight, 3)


def needs_review(
    metadata: dict,
    confs: dict[str, FieldConfidence] | None = None,
    threshold: float = 0.75,
) -> bool:
    """Check whether a document needs manual metadata review.

    Args:
        metadata: Raw metadata dict.
        confs: Pre-computed confidence scores. If None, scores are computed
            inline using the metadata_source field of metadata (default
            "filename" if absent).
        threshold: Minimum acceptable confidence for key fields.

    Returns:
        True if any key field has confidence below the threshold.
    """
    # Key fields that always require review if confidence is low
    KEY_FIELDS = {"document_type", "entity_code", "business_category", "period"}

    # For contract-category docs, contract_no is also a key field
    if metadata.get("business_category") in _CONTRACT_CATEGORIES:
        KEY_FIELDS = KEY_FIELDS | {"contract_no"}

    if confs is None:
        source = str(metadata.get("metadata_source", "filename"))
        confs = score_metadata_confidence(metadata, source)

    for field in KEY_FIELDS:
        if field in confs and confs[field].confidence < threshold:
            return True

    return False


def enrich_metadata_with_confidence(
    metadata: dict,
    source: str,
) -> dict:
    """Enrich a metadata dict with per-field confidence scores.

    Args:
        metadata: Input metadata dict.
        source: Extraction source passed to score_metadata_confidence().

    Returns:
        A new dict where every field value is replaced by
        ``{"value": <original>, "confidence": <score>}``.
        The top-level dict also contains ``overall_confidence``.
    """
    confs = score_metadata_confidence(metadata, source)

    enriched: dict = {}
    for field, value in metadata.items():
        if not isinstance(field, str):
            continue
        if field in confs:
            fc = confs[field]
            enriched[field] = {"value": fc.value, "confidence": fc.confidence}
        else:
            enriched[field] = {"value": str(value) if value is not None else "", "confidence": 0.0}

    enriched["overall_confidence"] = aggregate_confidence(confs)

    return enriched
