"""Built-in IDP parser and parse-quality helpers."""

import json
from pathlib import Path

from ..config import PARSE_QUALITY_REVIEW_THRESHOLD, PARSED_DIR
from ..logging_config import get_logger

logger = get_logger(__name__)

class NativeParserError(RuntimeError):
    """Raised when the built-in parser cannot extract usable content."""


def detect_image_mime_type(file_path: str) -> str:
    """Detect correct image MIME type from file extension."""
    ext = Path(file_path).suffix.lower()
    if ext in (".png",):
        return "image/png"
    elif ext in (".webp",):
        return "image/webp"
    elif ext in (".gif",):
        return "image/gif"
    elif ext in (".bmp",):
        return "image/bmp"
    return "image/jpeg"


def validate_canonical_schema(data: dict) -> bool:
    """Validate that parsed dict strictly adheres to the required Canonical Document Schema."""
    if not isinstance(data, dict):
        return False
    required_keys = {"document_type", "title", "text_content", "fields", "confidence"}
    if not required_keys.issubset(data.keys()):
        return False
    if not isinstance(data.get("document_type"), str) or not data["document_type"].strip():
        return False
    if not isinstance(data.get("title"), str):
        return False
    if not isinstance(data.get("text_content"), str) or not data["text_content"].strip():
        return False
    if not isinstance(data.get("fields"), dict):
        return False
    conf = data.get("confidence")
    if not isinstance(conf, (int, float)) or not (0.0 <= float(conf) <= 1.0):
        return False
    return True


def parse_image_with_paddleocr_vl(image_path: str) -> dict | None:
    """Call PaddleOCR-VL-1.6 multimodal endpoint for image/scan extraction with fail-closed Schema parsing."""
    import base64
    import requests
    import os
    import re

    vl_endpoint = os.getenv("PADDLE_OCR_VL_ENDPOINT", "http://127.0.0.1:8935/v1/chat/completions")
    mime_type = detect_image_mime_type(image_path)
    try:
        with open(image_path, "rb") as f:
            b64_str = base64.b64encode(f.read()).decode("utf-8")
        
        prompt_text = (
            "请识别该单据/扫描件，并严格以 JSON 格式输出如下 Schema（不要在 JSON 前后输出任何多余文字）：\n"
            "{\n"
            '  "document_type": "发票/完税证明/合同/验收单/其他",\n'
            '  "title": "单据标题描述",\n'
            '  "text_content": "票面全文识别文本",\n'
            '  "fields": {"发票代码": "", "发票号码": "", "开票日期": "", "购买方": "", "销售方": "", "金额": "", "税额": "", "价税合计": ""},\n'
            '  "confidence": 0.98\n'
            "}"
        )
        
        payload = {
            "model": "PaddleOCR-VL-1.6",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_str}"}},
                        {"type": "text", "text": prompt_text}
                    ]
                }
            ],
            "temperature": 0.1,
            "max_tokens": 1024
        }
        r = requests.post(vl_endpoint, json=payload, timeout=35)
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"].strip()
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
            raw_json = json_match.group(1) if json_match else content
            try:
                structured_data = json.loads(raw_json)
                if validate_canonical_schema(structured_data):
                    extracted_text = structured_data.get("text_content") or content
                    return {"text": extracted_text, "structured_json": structured_data}
                else:
                    logger.warning(f"PaddleOCR-VL output failed Schema validation: {content[:100]}")
                    return None
            except Exception as parse_err:
                logger.warning(f"JSON decode failed for PaddleOCR-VL output: {parse_err}")
                return None
    except Exception as e:
        logger.warning(f"PaddleOCR-VL-1.6 parse warning for {image_path}: {e}")
    return None


def parse_with_native_idp(document_code: str, input_path: str) -> dict:
    """IDP 3.0 native document parser supporting PDF, images, Word, Excel, and text."""
    path = Path(input_path)
    suffix = path.suffix.lower()
    out_dir = PARSED_DIR / document_code
    out_dir.mkdir(parents=True, exist_ok=True)

    content_list = []
    md_lines = []

    if suffix == ".pdf":
        try:
            import fitz
            doc = fitz.open(str(path))
            for page_idx, page in enumerate(doc):
                page_text = page.get_text("text").strip()
                # Native text quality check: if page has >=15 usable characters, use native text
                if len(page_text) >= 15:
                    md_lines.append(f"## 第 {page_idx + 1} 页\n\n" + page_text)
                    content_list.append({
                        "type": "text",
                        "text": page_text,
                        "text_level": 0,
                        "page_idx": page_idx,
                        "status": "PROCESSED",
                    })
                else:
                    # Scanned PDF or low-text page fallback to PaddleOCR-VL-1.6
                    tmp_page_img = out_dir / f"page_{page_idx}.png"
                    pix = page.get_pixmap(dpi=150)
                    pix.save(str(tmp_page_img))
                    vl_res = parse_image_with_paddleocr_vl(str(tmp_page_img))
                    if vl_res and vl_res.get("text") and vl_res.get("structured_json"):
                        text = vl_res["text"]
                        md_lines.append(f"## 第 {page_idx + 1} 页 (扫描页 OCR)\n\n" + text)
                        content_list.append({
                            "type": "text",
                            "text": text,
                            "text_level": 0,
                            "page_idx": page_idx,
                            "schema_data": vl_res.get("structured_json"),
                            "status": "PROCESSED",
                        })
                    else:
                        # Fail-closed for scanned/low-quality page: mark REVIEW
                        fallback_text = page_text if page_text else f"【第 {page_idx + 1} 页扫描图像识别失败/待审核】"
                        md_lines.append(f"## 第 {page_idx + 1} 页 (低质量/待人工审核)\n\n" + fallback_text)
                        content_list.append({
                            "type": "text",
                            "text": fallback_text,
                            "text_level": 0,
                            "page_idx": page_idx,
                            "status": "REVIEW",
                            "warning": "OCR_VL_FAIL_CLOSED_LOW_QUALITY",
                        })
                    if tmp_page_img.exists():
                        tmp_page_img.unlink(missing_ok=True)
            doc.close()
        except Exception as pdf_err:
            logger.error(f"Native PDF parse error: {pdf_err}")
            raise NativeParserError(f"PDF parsing error: {pdf_err}")

    elif suffix in (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"):
        logger.info(f"Image document {path.name} routing to PaddleOCR-VL-1.6 pipeline.")
        vl_result = parse_image_with_paddleocr_vl(str(path))
        if vl_result and vl_result.get("text") and vl_result.get("structured_json"):
            text = vl_result["text"]
            md_lines.append(f"# {path.stem}\n\n" + text)
            content_list.append({
                "type": "text",
                "text": text,
                "text_level": 0,
                "page_idx": 0,
                "schema_data": vl_result.get("structured_json"),
                "status": "PROCESSED",
            })
        else:
            # Fail-closed handling for image parsing: mark REVIEW
            err_msg = f"【图像原文件识别失败/待审核】: {path.name}"
            md_lines.append(f"# {path.stem}\n\n" + err_msg)
            content_list.append({
                "type": "text",
                "text": err_msg,
                "text_level": 0,
                "page_idx": 0,
                "status": "REVIEW",
                "warning": "OCR_VL_FAIL_CLOSED",
            })

    elif suffix in (".docx", ".doc"):
        try:
            import docx
            doc = docx.Document(str(path))
            for p in doc.paragraphs:
                p_text = p.text.strip()
                if p_text:
                    level = 1 if p.style and "heading" in p.style.name.lower() else 0
                    content_list.append({
                        "type": "text",
                        "text": p_text,
                        "text_level": level,
                        "page_idx": 0,
                    })
                    md_lines.append(p_text)
            for t in doc.tables:
                rows_data = []
                for row in t.rows:
                    rows_data.append([cell.text.strip() for cell in row.cells])
                if rows_data:
                    table_md = "\n".join([" | ".join(r) for r in rows_data])
                    content_list.append({
                        "type": "table",
                        "table_body": table_md,
                        "page_idx": 0,
                    })
                    md_lines.append(table_md)
        except Exception as docx_err:
            logger.error(f"Native DOCX error: {docx_err}")
            raise NativeParserError(f"DOCX parsing error: {docx_err}")

    elif suffix in (".xlsx", ".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(path), data_only=True)
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                rows_data = []
                for row in ws.iter_rows(values_only=True):
                    if any(c is not None for c in row):
                        rows_data.append([str(c) if c is not None else "" for c in row])
                if rows_data:
                    table_md = f"### 工作表: {sheet_name}\n" + "\n".join([" | ".join(r) for r in rows_data])
                    content_list.append({
                        "type": "table",
                        "table_body": table_md,
                        "page_idx": 0,
                    })
                    md_lines.append(table_md)
        except Exception as xls_err:
            logger.error(f"Native Excel error: {xls_err}")
            raise NativeParserError(f"Excel parsing error: {xls_err}")

    else:
        # Plain text / Markdown
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if text:
                md_lines.append(text)
                content_list.append({
                    "type": "text",
                    "text": text,
                    "text_level": 0,
                    "page_idx": 0,
                })
        except Exception as txt_err:
            logger.error(f"Native text read error: {txt_err}")
            raise NativeParserError(f"Text read error: {txt_err}")

    if not md_lines and not content_list:
        raise NativeParserError(f"No extractable text or content found in {path.name}")

    md_path = out_dir / f"{document_code}.md"
    cl_path = out_dir / f"{document_code}_content_list.json"

    md_path.write_text("\n\n".join(md_lines), encoding="utf-8")
    cl_path.write_text(json.dumps(content_list, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(f"IDP 3.0 Native Parser completed {document_code}: {len(content_list)} items")

    return {
        "output_dir": str(out_dir),
        "markdown_path": str(md_path),
        "content_list_path": str(cl_path),
        "stdout": "IDP 3.0 Native Parser",
        "attempts": 1,
    }


# ============================================
# V0.3 Parse Quality Gate helpers
# ============================================

def detect_encrypted_pdf(file_path: str) -> bool:
    """Detect whether a PDF file is encrypted by scanning its trailer.

    Uses a lightweight trailer-only scan: reads the last 4 KB of the file
    (or the whole file if smaller than 1 MB) and looks for the /Encrypt
    marker. False positives are unlikely for valid PDFs since /Encrypt only
    appears in the trailer dictionary. False negatives are possible for
    pathological PDFs where the marker is rewritten elsewhere, but those
    are extremely rare in practice.

    Args:
        file_path: Path to the PDF file on disk.

    Returns:
        True if the file appears to be encrypted, False otherwise (including
        on any read error).
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)
            if not header.startswith(b"%PDF-"):
                return False
            f.seek(0, 2)
            size = f.tell()
            if size > 1024 * 1024:  # >1MB only inspect the trailing 4KB
                f.seek(size - 4096)
            else:
                f.seek(0)
            trailer = f.read()
            return b"/Encrypt" in trailer
    except Exception:
        return False


def assess_content_list(content_list_path: str) -> dict:
    """Compute coarse parse-quality metrics from a native content-list file.

    The function is best-effort: any read or parse error yields an empty
    metric dict so callers can still compute a partial score.

    Args:
        content_list_path: Path to a native parser content-list file.

    Returns:
        Dict with keys: page_count, table_count, text_count, char_density,
        empty_page_ratio, ocr_confidence_avg.
    """
    result = {
        "page_count": 0,
        "table_count": 0,
        "text_count": 0,
        "char_density": 0.0,
        "empty_page_ratio": 0.0,
        "ocr_confidence_avg": 0.0,
    }

    if not content_list_path:
        return result

    path = Path(content_list_path)
    if not path.exists() or not path.is_file():
        return result

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"assess_content_list: failed to read {content_list_path}: {e}")
        return result

    if not isinstance(data, list):
        return result

    pages: set[int] = set()
    total_chars = 0
    empty_pages: set[int] = set()
    confidence_values: list[float] = []

    for item in data:
        if not isinstance(item, dict):
            continue

        page_idx_raw = item.get("page_idx")
        page_idx: int | None = None
        if isinstance(page_idx_raw, int):
            page_idx = page_idx_raw
        elif isinstance(page_idx_raw, str):
            try:
                page_idx = int(page_idx_raw)
            except (ValueError, TypeError):
                page_idx = None
        if page_idx is not None:
            pages.add(page_idx)

        item_type = item.get("type", "")
        if item_type == "table":
            result["table_count"] += 1
        elif item_type in ("text", "paragraph", "heading", "list", "equation", "code"):
            result["text_count"] += 1
            text = item.get("text", "")
            if isinstance(text, list):
                text = "\n".join(str(x) for x in text)
            if not isinstance(text, str):
                text = str(text or "")
            total_chars += len(text)
            if page_idx is not None and not text.strip():
                empty_pages.add(page_idx)

        score = item.get("score")
        if isinstance(score, (int, float)):
            confidence_values.append(float(score))

    page_count = len(pages)
    result["page_count"] = page_count
    if page_count > 0:
        result["char_density"] = total_chars / page_count
        result["empty_page_ratio"] = len(empty_pages) / page_count
    if confidence_values:
        result["ocr_confidence_avg"] = sum(confidence_values) / len(confidence_values)

    return result


def assess_parse_quality(parse_result: dict, markdown_text: str = "") -> dict:
    """Compute a parse-quality score and per-check flags for native output.

    Each individual check is independent and graceful: if the required input
    is missing, the check defaults to ``True`` (passed) so the score reflects
    what we *could* evaluate rather than punishing absence of data.

    Checks:
        * ``has_content``: markdown has >100 chars or parse_result exposes one.
        * ``page_count_reasonable``: 1 <= page_count <= 1000 (if known).
        * ``has_tables``: content_list.json contains any ``type=="table"``.
        * ``ocr_confidence_avg``: avg block score > 0.5 (if available).
        * ``char_density``: chars/page > 100 (if computable).
        * ``empty_page_ratio``: < 0.5 (if computable).

    Args:
        parse_result: Dict returned by :func:`parse_with_native_idp`.
        markdown_text: Optional pre-loaded markdown string.

    Returns:
        Dict with keys ``score`` (0-100), ``flags`` (list[str]),
        ``checks`` (dict[str, bool]) and ``details`` (raw metrics).
    """
    checks = {
        "has_content": False,
        "page_count_reasonable": True,
        "has_tables": False,
        "ocr_confidence_avg": True,
        "char_density": True,
        "empty_page_ratio": True,
    }
    details: dict = {
        "markdown_chars": 0,
        "page_count": 0,
        "table_count": 0,
        "text_count": 0,
        "char_density": 0.0,
        "empty_page_ratio": 0.0,
        "ocr_confidence_avg": 0.0,
    }
    flags: list[str] = []

    parse_result = parse_result if isinstance(parse_result, dict) else {}
    content_list_path = str(parse_result.get("content_list_path", "") or "")
    markdown_path = str(parse_result.get("markdown_path", "") or "")

    # 1. has_content
    md_chars = len(markdown_text or "")
    if not md_chars and markdown_path:
        try:
            p = Path(markdown_path)
            if p.exists() and p.is_file():
                md_chars = len(p.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            md_chars = 0
    if not md_chars:
        for k in ("markdown", "text", "content", "full_text"):
            v = parse_result.get(k)
            if isinstance(v, str):
                md_chars = len(v)
                break
    details["markdown_chars"] = md_chars
    if md_chars > 100:
        checks["has_content"] = True
    else:
        flags.append("EMPTY_OR_TRIVIAL_CONTENT")

    # 2-6: content_list-derived checks
    cl_metrics = assess_content_list(content_list_path) if content_list_path else {}
    for k in ("page_count", "table_count", "text_count", "char_density",
              "empty_page_ratio", "ocr_confidence_avg"):
        details[k] = cl_metrics.get(k, 0 if k != "char_density" and k != "empty_page_ratio" and k != "ocr_confidence_avg" else 0.0)

    page_count = details["page_count"]

    # page_count_reasonable (also fall back to parse_result if provided)
    if not page_count:
        for k in ("page_count", "pages", "total_pages"):
            v = parse_result.get(k)
            if isinstance(v, (int, float)) and v > 0:
                page_count = int(v)
                details["page_count"] = page_count
                break
    if page_count > 0:
        if 1 <= page_count <= 1000:
            checks["page_count_reasonable"] = True
        else:
            checks["page_count_reasonable"] = False
            flags.append("PAGE_COUNT_OUT_OF_RANGE")

    # has_tables
    if details["table_count"] > 0:
        checks["has_tables"] = True
    else:
        flags.append("NO_TABLES_DETECTED")

    # ocr_confidence_avg
    ocr_conf = details["ocr_confidence_avg"]
    if ocr_conf > 0:
        if ocr_conf > 0.5:
            checks["ocr_confidence_avg"] = True
        else:
            checks["ocr_confidence_avg"] = False
            flags.append("LOW_OCR_CONFIDENCE")

    # char_density
    cd = details["char_density"]
    if page_count > 0:
        if cd > 100:
            checks["char_density"] = True
        else:
            checks["char_density"] = False
            flags.append("LOW_CHAR_DENSITY")

    # empty_page_ratio
    epr = details["empty_page_ratio"]
    if page_count > 0:
        if epr < 0.5:
            checks["empty_page_ratio"] = True
        else:
            checks["empty_page_ratio"] = False
            flags.append("HIGH_EMPTY_PAGE_RATIO")

    # score
    passed = sum(1 for v in checks.values() if v)
    total = len(checks)
    score = (passed / total * 100.0) if total else 0.0

    if score < PARSE_QUALITY_REVIEW_THRESHOLD and "BELOW_REVIEW_THRESHOLD" not in flags:
        flags.append("BELOW_REVIEW_THRESHOLD")

    return {
        "score": score,
        "flags": flags,
        "checks": checks,
        "details": details,
    }
