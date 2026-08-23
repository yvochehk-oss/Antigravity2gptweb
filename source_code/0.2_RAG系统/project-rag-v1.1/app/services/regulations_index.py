"""
regulations_index.py
====================
为 regulation_chunks 表生成 embedding 向量并写入数据库。

V1.1: 路径解耦，从环境变量读 PROJECT_RAG_DATA_DIR。

用法：
  cd project-rag-v1.1 && python -m app.services.regulations_index
  cd project-rag-v1.1 && python -m app.services.regulations_index --limit 50 --dry
"""

import json
import logging
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent           # project-rag-v1.1/app/services/
PROJECT_DIR = SCRIPT_DIR.parent.parent                 # project-rag-v1.1/
sys.path.insert(0, str(PROJECT_DIR))

import os
os.environ.setdefault("PROJECT_RAG_DATA_DIR", str(PROJECT_DIR / "data"))
os.environ.setdefault("PROJECT_RAG_EMBEDDING_BACKEND", "bge_m3")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("reg_indexer")


def _load_emb():
    try:
        from app.services.embeddings import EmbeddingService
        return EmbeddingService, True
    except ImportError as e:
        log.warning(f"EmbeddingService 导入失败: {e}，dry-run 模式")
        return None, False


def _get_db():
    try:
        from app.db import engine, SessionLocal
        from app.config import IS_POSTGRES
        return engine, SessionLocal, IS_POSTGRES, True
    except ImportError as e:
        log.warning(f"数据库导入失败: {e}，dry-run 模式")
        return None, None, None, False


def _make_models(is_pg):
    from sqlalchemy import Column, Integer, String, Text
    from sqlalchemy.orm import declarative_base
    Base = declarative_base()

    class Regulation(Base):
        __tablename__ = "regulations"
        id = Column(Integer, primary_key=True)
        document_no = Column(String(120))

    class RegulationChunk(Base):
        __tablename__ = "regulation_chunks"
        id = Column(Integer, primary_key=True)
        regulation_id = Column(Integer)
        content = Column(Text)
        search_text = Column(Text)
        embedding_json = Column(Text)
        if is_pg:
            try:
                from pgvector.sqlalchemy import Vector
                embedding = Column(Vector(1024), nullable=True)
            except Exception:
                embedding = Column(Text, nullable=True)
        else:
            embedding = Column(Text, nullable=True)

    return Regulation, RegulationChunk


def run(batch_size: int = 32, limit: int = 0, dry_run: bool = False):
    log.info("开始向量化 regulation_chunks...")

    EmbeddingService, emb_ok = _load_emb()
    engine, SessionLocal, IS_POSTGRES, db_ok = _get_db()

    if not emb_ok or not db_ok:
        log.warning("依赖不满足，仅运行 dry-run")
        dry_run = True

    if emb_ok:
        emb_svc = EmbeddingService()

    session = SessionLocal() if db_ok else None
    Regulation, RegulationChunk = _make_models(IS_POSTGRES) if db_ok else (None, None)

    query = session.query(RegulationChunk).filter(
        RegulationChunk.embedding_json == "[]"
    ) if session else []
    if limit:
        query = query.limit(limit)

    all_chunks = query.all()
    total = len(all_chunks)
    log.info(f"待向量化的 chunk 数: {total}")

    if dry_run:
        log.info(f"[DRY] 将向量化 {total} 个 chunk")
        if session:
            session.close()
        return {"ok": total, "skip": 0, "err": 0}

    stats = {"ok": 0, "skip": 0, "err": 0}

    texts = [c.search_text or c.content for c in all_chunks]
    chunk_ids = [c.id for c in all_chunks]

    try:
        vecs = emb_svc.embed_batch(texts, batch_size=batch_size)
    except Exception as e:
        log.error(f"Embedding 调用失败: {e}")
        vecs = None

    if vecs:
        for chunk_id, text, vec in zip(chunk_ids, texts, vecs):
            chunk = session.query(RegulationChunk).get(chunk_id)
            if not chunk:
                continue
            chunk.embedding_json = json.dumps(vec, ensure_ascii=False)
            if IS_POSTGRES and vec:
                chunk.embedding = vec
            session.commit()
            stats["ok"] += 1
            if stats["ok"] % 50 == 0:
                log.info(f"  进度: {stats['ok']}/{total}")
    else:
        stats["skip"] = total

    session.close()
    log.info(f"完成：成功 {stats['ok']}，跳过 {stats['skip']}，错误 {stats['err']}")
    return stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    run(batch_size=args.batch, limit=args.limit, dry_run=args.dry)
