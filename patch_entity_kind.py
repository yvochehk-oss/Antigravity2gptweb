with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata.py", "r") as f:
    content = f.read()

old_kind = """                    "entity_kind": entity.entity_kind or ("branch" if code == "A04" else "company"),
                    "legal_entity": entity.legal_entity if entity.legal_entity is not None else True,"""
new_kind = """                    "entity_kind": getattr(entity, "entity_kind", "external") or ("branch" if code == "A04" else "company"),
                    "legal_entity": getattr(entity, "legal_entity", True) if getattr(entity, "legal_entity", True) is not None else True,"""

content = content.replace(old_kind, new_kind)

with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata.py", "w") as f:
    f.write(content)
