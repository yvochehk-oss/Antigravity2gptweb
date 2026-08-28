from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.models import Document, Chunk
from app.services.metadata import infer_from_filename, refine_from_content, load_canonical_entity_cache
import logging

logging.basicConfig(level=logging.INFO)

db = SessionLocal()
docs = db.query(Document).all()
cache = load_canonical_entity_cache()

updated = 0
for doc in docs:
    # re-infer from filename
    inferred = infer_from_filename(doc.filename, canonical_cache=cache)
    
    # fetch first 500 chars from chunks
    chunks = db.query(Chunk).filter(Chunk.document_id == doc.id).order_by(Chunk.chunk_index).limit(5).all()
    preview = "\n".join((c.heading_path or "") + "\n" + (c.content or "") for c in chunks)[:500]
    
    # create current metadata base
    current = {
        "document_type": inferred.get("document_type", "other"),
        "business_category": inferred.get("business_category", ""),
        "tax_category": inferred.get("tax_category", ""),
    }
    
    refined = refine_from_content(current, preview, canonical_cache=cache)
    
    # update doc fields based on inference rules
    # We will only overwrite fields if they are missing or if we just re-calculated them better.
    # Since we don't know what was manually entered vs inferred, we'll assume we can override inferred fields
    # But wait! If user edited them, `metadata_source` would be `user`.
    if doc.metadata_source == "user":
        continue
        
    doc.document_type = refined.get("document_type", "other")
    doc.entity_code = refined.get("entity_code") or inferred.get("entity_code") or ""
    doc.counterparty_code = refined.get("counterparty_code") or inferred.get("counterparty_code") or ""
    doc.business_category = refined.get("business_category") or inferred.get("business_category") or ""
    doc.tax_category = refined.get("tax_category") or inferred.get("tax_category") or ""
    doc.period = refined.get("period") or inferred.get("period") or ""
    
    updated += 1

db.commit()
print(f"Updated {updated} documents.")
