"""
regulations_md_ingest.py
========================
将 regulations_data/ 目录下的 Markdown 法规文件解析入库。

V1.1: 路径从环境变量读取（默认 ./regulations_data），不依赖硬编码相对路径。

步骤：
  1. 扫描所有 .md 文件，解析 frontmatter
  2. 按文件状态过滤（跳过全文废止）
  3. 将元数据写入 regulations 表
  4. 将正文按章节 / 段落 chunk 后写入 regulation_chunks 表

用法：
  cd project-rag-v1.1 && python3 -m app.services.regulations_md_ingest --dry
  cd project-rag-v1.1 && python3 -m app.services.regulations_md_ingest --limit 5
"""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

# ── 路径设置 ─────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent          # project-rag-v1.1/app/services/
PROJECT_DIR = SCRIPT_DIR.parent.parent                # project-rag-v1.1/
REG_DATA_DIR = Path(os.environ.get(
    "REGULATIONS_DATA_DIR",
    str(PROJECT_DIR / "regulations_data"),
))

# Make 'app' importable when run as standalone script
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
os.environ.setdefault("PROJECT_RAG_DATA_DIR", str(PROJECT_DIR / "data"))

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ingest")


# ── Frontmatter 解析 ──────────────────────────────────────────────────────────

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """返回 (metadata_dict, body_text)。"""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text.strip()
    raw, body = m.group(1), text[m.end():].strip()
    meta = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta, body


# ── Markdown Chunking ──────────────────────────────────────────────────────────

HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)")


def chunk_markdown(body: str, doc_no: str) -> list[dict]:
    """将 Markdown 正文拆分为语义块。"""
    blocks, current_heading, current_lines = [], "", []

    def flush():
        if not current_lines:
            return
        text = "\n".join(current_lines).strip()
        if len(text) < 30:
            current_lines.clear()
            return
        blocks.append({
            "heading_path": current_heading,
            "content": text,
            "token_estimate": int(len(text) * 0.5),
        })
        current_lines.clear()

    for line in body.splitlines():
        m = HEADING_RE.match(line)
        if m:
            flush()
            current_heading = (current_heading + " / " + m.group(2)) if current_heading else m.group(2)
        else:
            stripped = line.rstrip()
            if stripped:
                current_lines.append(stripped)
            elif current_lines:
                current_lines.append("")

    flush()

    if not blocks:
        blocks.append({
            "heading_path": "",
            "content": body.strip(),
            "token_estimate": int(len(body) * 0.5),
        })

    return blocks


# ── DB 写入 ───────────────────────────────────────────────────────────────────

def _get_db():
    try:
        from app.db import engine, Base, SessionLocal
        from app.config import IS_POSTGRES
        from app.models import Regulation, RegulationChunk
        return engine, Base, SessionLocal, IS_POSTGRES, Regulation, RegulationChunk, True
    except ImportError as e:
        log.warning(f"无法导入 app.db / app.models（{e}），切换为 dry-run 模式")
        return None, None, None, None, None, None, False


def upsert_regulation(session, Regulation, data: dict) -> int:
    existing = session.query(Regulation).filter_by(document_no=data["document_no"]).first()
    if existing:
        for k, v in data.items():
            setattr(existing, k, v)
        session.flush()
        return existing.id
    reg = Regulation(**data)
    session.add(reg)
    session.flush()
    return reg.id


def upsert_chunks(session, RegulationChunk, regulation_id: int, chunks: list[dict]):
    session.query(RegulationChunk).filter_by(regulation_id=regulation_id).delete()
    for i, chunk in enumerate(chunks):
        c = RegulationChunk(
            regulation_id=regulation_id,
            chunk_index=i,
            heading_path=chunk["heading_path"],
            content=chunk["content"],
            token_estimate=chunk["token_estimate"],
            search_text=chunk["content"][:1000],
            embedding_json="[]",
        )
        session.add(c)
    session.flush()


ROLE_DIRECTORIES = {
    "business_roles/construction": "construction",
    "business_roles/trade": "trade",
    "business_roles/labor": "labor",
    "business_roles/equipment": "equipment",
}

# Keep a read-only compatibility path for an older checkout.  These aliases
# are accepted only while migrating an external data directory; they are not
# emitted as the current category or business_role and must not be recreated
# in the current regulations_data tree.
LEGACY_ROLE_DIRECTORIES = {
    "entity_a": "construction",
    "entity_b": "trade",
    "entity_c": "labor",
    "entity_d": "equipment",
}

GENERAL_DIRECTORIES = {
    "national_vat": None,
    "national_other": None,
    "sichuan": None,
    "chengdu": None,
}


def scan_md_files() -> list[tuple[Path, dict, str, str]]:
    """Scan current role directories and non-role regulation directories.

    ``category`` is now a storage/category path (for example,
    ``business_roles/construction``); the role itself is carried separately as
    ``business_role``.  A legacy ``entity_*`` directory is read only for
    backwards-compatible imports and is normalized to the same canonical
    values.  No single-letter entity code is inferred from a directory name.
    """
    directories: list[tuple[str, str | None, bool]] = [
        *((directory, role, False) for directory, role in ROLE_DIRECTORIES.items()),
        *((directory, role, True) for directory, role in LEGACY_ROLE_DIRECTORIES.items()),
        *((directory, role, False) for directory, role in GENERAL_DIRECTORIES.items()),
    ]
    results = []
    for sub, role, legacy in directories:
        sub_dir = REG_DATA_DIR / sub
        if not sub_dir.exists():
            continue
        for md in sorted(sub_dir.glob("*.md")):
            text = md.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(text)
            if not meta:
                meta = {"title": md.stem, "document_no": md.stem, "status": "有效"}
            # The path is canonical even when ingesting a legacy directory.
            canonical_category = sub
            if legacy:
                canonical_category = f"business_roles/{role}"
            meta["category"] = canonical_category
            if role:
                meta["business_role"] = role
                if legacy:
                    meta["legacy_category"] = sub
            results.append((md, meta, body, canonical_category))
    return results


def run(limit: int = 0, dry_run: bool = False):
    log.info(f"法规数据目录: {REG_DATA_DIR}")

    engine, Base, SessionLocal, IS_POSTGRES, Regulation, RegulationChunk, ok = _get_db()

    files = scan_md_files()
    if limit:
        files = files[:limit]
    log.info(f"发现 {len(files)} 个 Markdown 文件")

    session = None

    if ok and engine is not None:
        try:
            session = SessionLocal()
        except Exception as e:
            log.warning(f"数据库会话初始化失败: {e}，切换为 dry-run")
            dry_run = True

    stats = {"insert": 0, "skip": 0, "error": 0}

    try:
        for i, (md_path, meta, body, sub_cat) in enumerate(files):
            doc_no = meta.get("document_no", md_path.stem)
            status = meta.get("status", "有效")

            if status == "全文废止":
                log.info(f"  ⏭ 跳过（全文废止）: {doc_no}")
                stats["skip"] += 1
                continue

            tax_types = meta.get("tax_types", [])
            if isinstance(tax_types, str):
                try:
                    tax_types = json.loads(tax_types)
                except Exception:
                    tax_types = [tax_types]
            tax_type_str = ", ".join(tax_types) if isinstance(tax_types, list) else str(tax_types)

            industries = meta.get("industries", [])
            if isinstance(industries, str):
                try:
                    industries = json.loads(industries)
                except Exception:
                    industries = [industries]
            industry_str = ", ".join(industries) if isinstance(industries, list) else str(industries)

            chunks = chunk_markdown(body, doc_no)

            meta_save = dict(meta)
            meta_save["category"] = sub_cat
            meta_save["relative_path"] = str(md_path.relative_to(REG_DATA_DIR))

            reg_data = {
                "document_no": doc_no,
                "title": meta.get("title", doc_no),
                "issuer": meta.get("issuer", ""),
                "legal_level": meta.get("legal_level", "规范性文件"),
                "jurisdiction": meta.get("jurisdiction", "全国"),
                "tax_type": tax_type_str or "全部税种",
                "industry": industry_str or "建筑业",
                "publish_date": meta.get("publish_date", ""),
                "effective_date": meta.get("effective_date", ""),
                "expiry_date": meta.get("expiry_date", ""),
                "status": status,
                "full_text": body,
                "source": meta.get("source", ""),
                "version_label": meta.get("version_label", "V1"),
                "embedding_json": "[]",
                "search_text": f"{meta.get('title', '')} {doc_no} {body[:500]}",
                "metadata_json": json.dumps(meta_save, ensure_ascii=False),
            }

            if dry_run or session is None:
                log.info(f"  [DRY] {doc_no} → {len(chunks)} chunks")
                stats["insert"] += 1
                continue

            try:
                reg_id = upsert_regulation(session, Regulation, reg_data)
                upsert_chunks(session, RegulationChunk, reg_id, chunks)
                session.commit()
                log.info(f"  ✅ [{i+1}/{len(files)}] {doc_no} ({len(chunks)} chunks)")
                stats["insert"] += 1
            except Exception as e:
                session.rollback()
                log.error(f"  ❌ {doc_no}: {e}")
                stats["error"] += 1

            time.sleep(0.05)

    finally:
        if session:
            session.close()

    log.info(f"\n完成：入库 {stats['insert']}，跳过 {stats['skip']}，失败 {stats['error']}")
    return stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="法规文件入库工具")
    parser.add_argument("--dry", action="store_true", help="仅打印，不写库")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 个文件")
    args = parser.parse_args()
    run(limit=args.limit, dry_run=args.dry)
