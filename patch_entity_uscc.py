with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata.py", "r") as f:
    content = f.read()

old_block = """                    "unified_social_credit_code": entity.unified_social_credit_code or "",
                })"""
new_block = """                    "unified_social_credit_code": getattr(entity, "unified_social_credit_code", "") or "",
                })"""
content = content.replace(old_block, new_block)
with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata.py", "w") as f:
    f.write(content)
