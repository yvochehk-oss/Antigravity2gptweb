with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata.py", "r") as f:
    content = f.read()

old_block = """                    "status": entity.status or "active",
                    "tax_id": entity.tax_id or "",
                    "unified_social_credit_code": entity.unified_social_credit_code or "",
                })"""
new_block = """                    "status": getattr(entity, "status", "active") or "active",
                    "tax_id": getattr(entity, "tax_id", "") or "",
                    "unified_social_credit_code": getattr(entity, "unified_social_credit_code", "") or "",
                })"""
content = content.replace(old_block, new_block)
with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata.py", "w") as f:
    f.write(content)
