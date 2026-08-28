"""
regulations_batch_importer.py
------------------------------
Batch scanner and importer for regulation documents (.md, .pdf, .docx, .txt)
from any designated local or server directory.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Regulation, RegulationChunk
from .ai_regulation_extractor import ai_extract_regulation_metadata, extract_file_content
from .regulations_md_ingest import (
    chunk_markdown,
    parse_frontmatter,
    upsert_chunks,
)

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".md", ".pdf", ".docx", ".doc", ".txt"}


def batch_import_regulations_from_dir(
    db: Session,
    dir_path: str,
    recursive: bool = True,
    default_role: str = "construction",
) -> dict[str, Any]:
    """Scan directory and batch import all supported regulation documents into database."""
    p = Path(dir_path)
    if not p.exists() or not p.is_dir():
        raise ValueError(f"指定的目录不存在或不是有效文件夹: {dir_path}")

    # Gather files
    files: list[Path] = []
    if recursive:
        for ext in SUPPORTED_EXTENSIONS:
            files.extend(p.rglob(f"*{ext}"))
    else:
        for ext in SUPPORTED_EXTENSIONS:
            files.extend(p.glob(f"*{ext}"))

    files = sorted(set(files))
    if not files:
        return {
            "success": True,
            "total_scanned": 0,
            "created_count": 0,
            "updated_count": 0,
            "failed_count": 0,
            "items": [],
            "errors": [],
            "message": f"目录 '{dir_path}' 下未找到任何支持的法规文件（.md, .pdf, .docx, .txt）",
        }

    imported_items = []
    errors = []
    created_count = 0
    updated_count = 0

    for file_path in files:
        if file_path.name.startswith((".", "~$")) or file_path.name.lower() in {
            "readme.md",
            "index.md",
            "data_sources.md",
            "summary.md",
            "changelog.md",
        }:
            continue

        try:
            suffix = file_path.suffix.lower()
            rel_path = str(file_path.relative_to(p))

            # Deduce role from folder path if applicable
            business_role = default_role
            path_str = str(file_path).lower()
            if any(k in path_str for k in ("labor", "劳务")):
                business_role = "labor"
            elif any(k in path_str for k in ("trade", "商贸", "物资", "采购", "材料")):
                business_role = "trade"
            elif any(k in path_str for k in ("equipment", "租赁", "机械", "设备")):
                business_role = "equipment"
            elif any(k in path_str for k in ("national_vat", "增值税")):
                business_role = "national_vat"
            elif any(k in path_str for k in ("construction", "建筑", "施工")):
                business_role = "construction"

            meta: dict[str, Any] = {}
            body_text = ""

            if suffix == ".md":
                raw_content = file_path.read_text(encoding="utf-8", errors="ignore")
                fm_meta, body_text = parse_frontmatter(raw_content)
                if fm_meta and (fm_meta.get("title") or fm_meta.get("document_no")):
                    meta = {
                        "title": fm_meta.get("title") or file_path.stem,
                        "document_no": fm_meta.get("document_no") or file_path.stem,
                        "issuer": fm_meta.get("issuer") or "财政部 国家税务总局",
                        "legal_level": fm_meta.get("legal_level") or "规范性文件",
                        "jurisdiction": fm_meta.get("jurisdiction") or "全国",
                        "tax_type": fm_meta.get("tax_type") or "增值税",
                        "industry": fm_meta.get("industry") or "建筑业",
                        "publish_date": fm_meta.get("publish_date") or "",
                        "effective_date": fm_meta.get("effective_date") or "",
                        "status": fm_meta.get("status") or "现行有效",
                        "business_role": fm_meta.get("business_role") or business_role,
                    }
                else:
                    # Fallback to AI extraction on markdown text
                    meta = ai_extract_regulation_metadata(raw_content[:4000])
                    body_text = raw_content
            else:
                # PDF / DOCX / TXT
                content_bytes = file_path.read_bytes()
                body_text = extract_file_content(content_bytes, file_path.name)
                if not body_text or len(body_text.strip()) < 10:
                    errors.append(f"{file_path.name}: 提取文本为空或内容过短")
                    continue
                meta = ai_extract_regulation_metadata(body_text[:4000])

            title = meta.get("title") or file_path.stem
            doc_no = meta.get("document_no") or title or file_path.stem
            issuer = meta.get("issuer") or "国家税务总局 / 财政部"
            legal_level = meta.get("legal_level") or "规范性文件"
            jurisdiction = meta.get("jurisdiction") or "全国"
            tax_type = meta.get("tax_type") or "增值税"
            industry = meta.get("industry") or "建筑业"
            status = meta.get("status") or "现行有效"
            reg_role = meta.get("business_role") or business_role

            meta_dict = {
                "title": title,
                "document_no": doc_no,
                "issuer": issuer,
                "legal_level": legal_level,
                "jurisdiction": jurisdiction,
                "tax_types": [tax_type],
                "industries": [industry],
                "publish_date": meta.get("publish_date", ""),
                "effective_date": meta.get("effective_date", ""),
                "status": status,
                "business_role": reg_role,
                "category": f"business_roles/{reg_role}",
                "source": f"本地目录批量导入: {rel_path}",
            }

            reg_dict = {
                "document_no": doc_no,
                "title": title,
                "issuer": issuer,
                "legal_level": legal_level,
                "jurisdiction": jurisdiction,
                "tax_type": tax_type,
                "industry": industry,
                "publish_date": meta.get("publish_date", ""),
                "effective_date": meta.get("effective_date", ""),
                "status": status,
                "full_text": body_text,
                "source": f"本地目录: {rel_path}",
                "version_label": "V1",
                "embedding_json": "[]",
                "search_text": f"{title} {doc_no} {body_text[:500]}",
                "metadata_json": json.dumps(meta_dict, ensure_ascii=False),
            }

            existing = db.scalar(select(Regulation).where(Regulation.document_no == doc_no))
            if existing:
                for k, v in reg_dict.items():
                    setattr(existing, k, v)
                db.flush()
                reg_id = existing.id
                updated_count += 1
            else:
                new_reg = Regulation(**reg_dict)
                db.add(new_reg)
                db.flush()
                reg_id = new_reg.id
                created_count += 1

            chunks = chunk_markdown(body_text, doc_no)
            upsert_chunks(db, RegulationChunk, reg_id, chunks)

            imported_items.append({
                "id": reg_id,
                "title": title,
                "document_no": doc_no,
                "file_path": rel_path,
                "chunk_count": len(chunks),
                "business_role": reg_role,
                "jurisdiction": jurisdiction,
                "action": "updated" if existing else "created",
            })

        except Exception as e:
            logger.error("Failed to import regulation file %s: %s", file_path.name, e)
            errors.append(f"{file_path.name}: {str(e)}")

    db.commit()

    return {
        "success": True,
        "total_scanned": len(files),
        "created_count": created_count,
        "updated_count": updated_count,
        "failed_count": len(errors),
        "items": imported_items,
        "errors": errors,
        "message": f"扫描完成：共扫描 {len(files)} 个文件，成功新增 {created_count} 份法规，更新 {updated_count} 份，失败 {len(errors)} 个。",
    }
