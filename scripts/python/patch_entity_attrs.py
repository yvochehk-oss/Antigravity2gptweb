with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata.py", "r") as f:
    content = f.read()

old_block = """                    "entity_kind": entity.entity_kind or ("branch" if code == "A04" else "company"),
                    "legal_entity": entity.legal_entity,
                    "parent_entity_code": entity.parent_entity_code,"""
new_block = """                    "entity_kind": getattr(entity, "entity_kind", "external") or ("branch" if code == "A04" else "company"),
                    "legal_entity": getattr(entity, "legal_entity", True),
                    "parent_entity_code": getattr(entity, "parent_entity_code", None),"""

content = content.replace(old_block, new_block)
with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata.py", "w") as f:
    f.write(content)
