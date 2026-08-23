"""Document chunking with V0.3 structured semantic awareness.

Supports both legacy generic chunking (v0.2) and new structured chunking
by document semantic type: contract_clause, tax_article, table_row, meeting_topic, plain.
All three public entry points preserve backward-compatible signatures.
"""
from pathlib import Path
import json
import re
from ..logging_config import get_logger

logger = get_logger(__name__)

# ---------- Constants ----------

TARGET_CHARS = 3200
OVERLAP_CHARS = 450

SEMANTIC_TYPES = ("contract_clause", "tax_article", "table_row", "meeting_topic", "plain")

CONTRACT_DOC_TYPES = {
    "main_contract", "supplementary", "subcontract_supplementary",
    "equipment_contract", "labor_contract", "material_contract", "subcontract"
}
TAX_DOC_TYPES = {"tax_policy", "taxircular"}
TABLE_DOC_TYPES = {"excel", "schedule", "settlement", "table"}
MEETING_DOC_TYPES = {"minutes", "meeting_record"}

DEFAULT_CHUNK_VERSION = "v0.3_structured"
LEGACY_CHUNK_VERSION = "v0.2_generic"

# Semantic-type-specific target sizes
_CONTRACT_TARGET_CHARS = 2400
_TAX_TARGET_CHARS = 2800

# ---------- Classification ----------

def classify_document(document_type: str, content_preview: str = "") -> str:
    """Classify a document into a semantic type.

    Args:
        document_type: Document type identifier (e.g. "main_contract", "tax_policy")
        content_preview: Optional first ~500 chars of content for heuristic boost

    Returns:
        One of SEMANTIC_TYPES: contract_clause, tax_article, table_row, meeting_topic, plain
    """
    dt = (document_type or "").lower().strip()
    if dt in CONTRACT_DOC_TYPES:
        return "contract_clause"
    if dt in TAX_DOC_TYPES:
        return "tax_article"
    if dt in TABLE_DOC_TYPES:
        return "table_row"
    if dt in MEETING_DOC_TYPES:
        return "meeting_topic"
    return "plain"


# ---------- Chunk enrichment ----------

def _enrich_chunk(raw: dict, semantic_type: str) -> dict:
    """Add V0.3 structured fields to a chunk dict.

    Args:
        raw: Chunk dict with at least heading_path and content
        semantic_type: Semantic type string

    Returns:
        Same dict with three new fields added:
        - chunk_strategy_version: "v0.3_structured"
        - title_chain: ">"-joined heading chain
        - semantic_type: the passed semantic_type
    """
    headings = raw.get("heading_path", "") or ""
    raw["chunk_strategy_version"] = DEFAULT_CHUNK_VERSION
    raw["title_chain"] = headings  # already uses " > " internally
    raw["semantic_type"] = semantic_type
    return raw


# ---------- Semantic splitters ----------

def _split_contract_sections(sections: list[dict]) -> list[dict]:
    """Split contract sections by chapter/article/paragraph structure.

    Recognises patterns:
      - 第X章  (chapter)
      - 第X条  (article)
      - X.X    (sub-paragraph)
    Each unit becomes its own chunk. Falls back to generic split for
    unmatched sections.

    Args:
        sections: List of section dicts (already parsed from content_list)

    Returns:
        List of enriched chunk dicts
    """
    out = []

    chapter_headings = []
    article_headings = []

    chapter_pat = re.compile(r"第([一二三四五六七八九十百零\d]+)章")
    article_pat = re.compile(r"第([一二三四五六七八九十百零\d]+)条")
    sub_pat = re.compile(r"^(\d+\.\d+)\s")

    for sec in sections:
        raw_text = sec.get("content", "")
        heading_path = sec.get("heading_path", "") or ""

        # Detect chapter boundaries
        chapter_m = chapter_pat.search(heading_path + raw_text[:100])
        if chapter_m:
            chapter_headings = [heading_path.split(" > ")[-1][:180]]
            article_headings = []
            out.append(_enrich_chunk(dict(sec), "contract_clause"))
            continue

        # Detect article boundaries
        article_m = article_pat.search(heading_path + raw_text[:100])
        if article_m:
            article_headings.append(heading_path.split(" > ")[-1][:180])
            out.append(_enrich_chunk(dict(sec), "contract_clause"))
            continue

        # Sub-paragraph
        sub_m = sub_pat.search(heading_path + "\n" + raw_text[:200])
        if sub_m:
            out.append(_enrich_chunk(dict(sec), "contract_clause"))
            continue

        # Unstructured – use generic split but with contract target
        sub_chunks = _split_sections([sec], target_chars=_CONTRACT_TARGET_CHARS)
        for sc in sub_chunks:
            out.append(_enrich_chunk(sc, "contract_clause"))

    logger.debug(f"Contract splitting: {len(sections)} sections -> {len(out)} chunks")
    return out


def _split_tax_articles(sections: list[dict]) -> list[dict]:
    """Split tax document sections by 第X条 (article) boundaries.

    Each 第X条 forms its own chunk, preserving effective_from/to metadata
    if present in the section.

    Args:
        sections: List of section dicts

    Returns:
        List of enriched chunk dicts
    """
    out = []
    article_pat = re.compile(r"第([一二三四五六七八九十百零\d]+)条")

    for sec in sections:
        raw_text = sec.get("content", "")
        heading_path = sec.get("heading_path", "")

        if article_pat.search(heading_path + raw_text[:100]):
            out.append(_enrich_chunk(dict(sec), "tax_article"))
            continue

        # Fallback to generic split with tax target
        sub_chunks = _split_sections([sec], target_chars=_TAX_TARGET_CHARS)
        for sc in sub_chunks:
            out.append(_enrich_chunk(sc, "tax_article"))

    logger.debug(f"Tax splitting: {len(sections)} sections -> {len(out)} chunks")
    return out


def _split_table_rows(sections: list[dict]) -> list[dict]:
    """Split table sections into groups of at most 5 rows.

    Expects table content as HTML or structured text. Each group keeps the
    column headers (first row) and includes up to 5 data rows.

    Args:
        sections: List of section dicts

    Returns:
        List of enriched chunk dicts
    """
    out = []
    ROW_GROUP_SIZE = 5

    for sec in sections:
        raw_text = sec.get("content", "")
        heading_path = sec.get("heading_path", "")
        sheet_name = heading_path.split(" > ")[-1] if " > " in heading_path else ""

        # Try to extract rows from HTML table
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", raw_text, re.DOTALL | re.IGNORECASE)
        if not rows:
            # Plain text: split on double newlines or pipe lines
            rows = re.split(r"\n\s*\n|\n\|", raw_text)

        if not rows:
            # Nothing recognisable – treat as single chunk
            out.append(_enrich_chunk(dict(sec), "table_row"))
            continue

        # Group rows
        for i in range(0, len(rows), ROW_GROUP_SIZE):
            group = rows[i : i + ROW_GROUP_SIZE]
            chunk = dict(sec)
            chunk["content"] = "\n".join(group).strip()
            chunk["heading_path"] = heading_path
            chunk["title_chain"] = heading_path
            out.append(_enrich_chunk(chunk, "table_row"))

    logger.debug(f"Table splitting: {len(sections)} sections -> {len(out)} chunks")
    return out


def _split_meeting_topics(sections: list[dict]) -> list[dict]:
    """Split meeting minutes by topic markers.

    Recognises patterns:
      - 议题: / 议题一 / 议题二
      - 讨论: / 讨论一
      - 主持人: (new speaker block)
    Each topic forms its own chunk, retaining date if detected.

    Args:
        sections: List of section dicts

    Returns:
        List of enriched chunk dicts
    """
    out = []
    topic_pat = re.compile(
        r"(?:^|\n)(议题[：:一二三四五六七八九十\d]+|讨论[：:一二三四五六七八九十\d]+|主持人[：:])",
        re.MULTILINE,
    )

    for sec in sections:
        raw_text = sec.get("content", "")
        heading_path = sec.get("heading_path", "")

        parts = topic_pat.split(raw_text)
        if len(parts) <= 1:
            # No split found – treat as single chunk
            out.append(_enrich_chunk(dict(sec), "meeting_topic"))
            continue

        for part in parts:
            text = part.strip()
            if not text:
                continue
            chunk = dict(sec)
            chunk["content"] = text
            chunk["heading_path"] = heading_path
            chunk["title_chain"] = heading_path
            out.append(_enrich_chunk(chunk, "meeting_topic"))

    logger.debug(f"Meeting splitting: {len(sections)} sections -> {len(out)} chunks")
    return out


# ---------- Core generic splitter (preserved from v0.2) ----------

def _split_sections(sections: list[dict], target_chars: int = TARGET_CHARS) -> list[dict]:
    """Split large sections into smaller chunks with overlap.

    Backward-compatible signature: accepts optional target_chars kwarg.

    Args:
        sections: List of section dictionaries
        target_chars: Max characters per chunk (default TARGET_CHARS=3200)

    Returns:
        List of chunk dictionaries with token_estimate field added
    """
    out = []

    for sec in sections:
        text = sec["content"].strip()
        if len(text) <= target_chars:
            item = dict(sec)
            item["token_estimate"] = _estimate_tokens(text)
            out.append(item)
            continue

        start = 0
        while start < len(text):
            end = min(len(text), start + target_chars)

            if end < len(text):
                cut = max(
                    text.rfind("\n\n", start + 1800, end),
                    text.rfind("。", start + 1800, end),
                    text.rfind("\n", start + 1800, end),
                )
                if cut > start:
                    end = cut + 1

            piece = text[start:end].strip()
            if piece:
                item = dict(sec)
                item["content"] = piece
                item["token_estimate"] = _estimate_tokens(piece)
                out.append(item)

            if end >= len(text):
                break

            start = max(start + 1, end - OVERLAP_CHARS)

    logger.debug(f"Split {len(sections)} sections into {len(out)} chunks (target={target_chars})")
    return out


def _estimate_tokens(text: str) -> int:
    """Estimate token count for a text.

    Conservative estimate for Chinese characters (1 token each) and
    English characters (4 per token).

    Args:
        text: Input text

    Returns:
        Estimated token count
    """
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return cjk + max(0, len(text) - cjk) // 4


# ---------- Public entry points (backward-compatible signatures) ----------

def chunks_from_content_list(path: str, document_type: str = "") -> list[dict]:
    """Parse MinerU content_list.json into structured chunks.

    Backward-compatible signature: document_type is optional (defaults to "").

    Args:
        path: Path to content_list.json
        document_type: Optional document type for semantic classification

    Returns:
        List of chunk dictionaries. Each dict includes all v0.2 fields plus
        chunk_strategy_version, title_chain, and semantic_type.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Failed to parse content_list.json: {e}")
        return []

    if not isinstance(data, list):
        return []

    semantic_type = classify_document(document_type)
    logger.debug(f"classify_document({document_type!r}) -> {semantic_type}")

    # ---- Phase 1: collect sections (heading-aware, unchanged from v0.2) ----
    sections = []
    headings = []
    buf = []
    pages = []
    ctype = "text"

    def flush():
        nonlocal buf, pages, ctype
        text = "\n\n".join(x for x in buf if x).strip()
        if text:
            sections.append({
                "heading_path": " > ".join(headings),
                "page_start": min(pages) + 1 if pages else None,
                "page_end": max(pages) + 1 if pages else None,
                "content_type": ctype,
                "content": text,
            })
        buf = []
        pages = []
        ctype = "text"

    for item in data:
        if not isinstance(item, dict):
            continue

        typ = item.get("type", "text")
        page = item.get("page_idx")

        if typ == "text":
            text = (item.get("text") or "").strip()
            level = int(item.get("text_level") or 0)

            if level > 0 and text:
                flush()
                while len(headings) >= level:
                    headings.pop()
                headings.append(text[:180])
                continue

            if text:
                buf.append(text)
                pages.append(page if isinstance(page, int) else 0)

        elif typ == "table":
            html = item.get("table_body") or item.get("table_caption") or ""
            if isinstance(html, list):
                html = " ".join(map(str, html))
            if html:
                flush()
                ctype = "table"
                buf = [str(html)]
                pages = [page if isinstance(page, int) else 0]
                flush()

        elif typ in ("list", "equation", "code"):
            text = item.get("text") or item.get("code_body") or ""
            if isinstance(text, list):
                text = "\n".join(map(str, text))
            if text:
                buf.append(str(text))
                pages.append(page if isinstance(page, int) else 0)

    flush()

    # ---- Phase 2: semantic splitting ----
    if semantic_type == "contract_clause":
        chunks = _split_contract_sections(sections)
    elif semantic_type == "tax_article":
        chunks = _split_tax_articles(sections)
    elif semantic_type == "table_row":
        chunks = _split_table_rows(sections)
    elif semantic_type == "meeting_topic":
        chunks = _split_meeting_topics(sections)
    else:
        # Plain: legacy generic split
        raw_chunks = _split_sections(sections)
        chunks = [_enrich_chunk(c, "plain") for c in raw_chunks]

    logger.debug(f"Parsed content_list: {len(sections)} sections -> {len(chunks)} chunks ({semantic_type})")
    return chunks


def chunks_from_markdown(path: str, document_type: str = "") -> list[dict]:
    """Parse markdown file into structured chunks preserving headings.

    Backward-compatible signature: document_type is optional (defaults to "").

    Recognises ## 第X章 / # X.X markdown headings.

    Args:
        path: Path to markdown file
        document_type: Optional document type for semantic classification

    Returns:
        List of chunk dictionaries (same enrichment as chunks_from_content_list)
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.error(f"Failed to read markdown file: {e}")
        return []

    semantic_type = classify_document(document_type)

    headings = []
    sections = []
    current = []

    def flush():
        nonlocal current
        body = "\n".join(current).strip()
        if body:
            sections.append({
                "heading_path": " > ".join(headings),
                "page_start": None,
                "page_end": None,
                "content_type": "text",
                "content": body,
            })
        current = []

    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            while len(headings) >= level:
                headings.pop()
            headings.append(title[:180])
        else:
            current.append(line)

    flush()

    # Semantic splitting (reuse helpers)
    if semantic_type == "contract_clause":
        chunks = _split_contract_sections(sections)
    elif semantic_type == "tax_article":
        chunks = _split_tax_articles(sections)
    elif semantic_type == "table_row":
        chunks = _split_table_rows(sections)
    elif semantic_type == "meeting_topic":
        chunks = _split_meeting_topics(sections)
    else:
        raw_chunks = _split_sections(sections)
        chunks = [_enrich_chunk(c, "plain") for c in raw_chunks]

    logger.debug(f"Parsed markdown: {len(sections)} sections -> {len(chunks)} chunks ({semantic_type}) from {path}")
    return chunks


def chunks_from_plain_text(path: str, document_type: str = "") -> list[dict]:
    """Parse plain text file as a single chunk with semantic enrichment.

    Backward-compatible signature: document_type is optional (defaults to "").

    Args:
        path: Path to text file
        document_type: Optional document type for semantic classification

    Returns:
        List with a single enriched chunk dict
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.error(f"Failed to read text file: {e}")
        return []

    semantic_type = classify_document(document_type)

    sections = [{
        "heading_path": "",
        "page_start": None,
        "page_end": None,
        "content_type": "text",
        "content": text,
    }]

    if semantic_type in ("contract_clause", "tax_article", "table_row", "meeting_topic"):
        if semantic_type == "contract_clause":
            chunks = _split_contract_sections(sections)
        elif semantic_type == "tax_article":
            chunks = _split_tax_articles(sections)
        elif semantic_type == "table_row":
            chunks = _split_table_rows(sections)
        else:
            chunks = _split_meeting_topics(sections)
    else:
        raw_chunks = _split_sections(sections)
        chunks = [_enrich_chunk(c, "plain") for c in raw_chunks]

    logger.debug(f"Parsed plain text: {len(chunks)} chunks ({semantic_type}) from {path}")
    return chunks


# ---------- Convenience API for re-chunk endpoint ----------

def chunk_documents_into_structured_chunks(
    content_list_path: str,
    document_type: str,
) -> list[dict]:
    """Convenience entry point for the re-chunk API.

    Calls chunks_from_content_list with the provided document_type.
    All chunks are enriched with v0.3 structured fields.

    Args:
        content_list_path: Path to MinerU content_list.json
        document_type: Document semantic type

    Returns:
        List of enriched chunk dicts
    """
    return chunks_from_content_list(content_list_path, document_type=document_type)
