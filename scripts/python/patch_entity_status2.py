with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata.py", "r") as f:
    content = f.read()

old_block = """                    "status": entity.status or "active",
                    # ``tax_id`` is canonical; the legacy unified-code
                    # column is retained only as a compatibility read.
                    "tax_id": entity.tax_id or entity.unified_social_credit_code or "",
"""

new_block = """                    "status": getattr(entity, "status", "active") or "active",
                    # ``tax_id`` is canonical; the legacy unified-code
                    # column is retained only as a compatibility read.
                    "tax_id": getattr(entity, "tax_id", "") or getattr(entity, "unified_social_credit_code", "") or "",
"""
content = content.replace(old_block, new_block)
with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata.py", "w") as f:
    f.write(content)
