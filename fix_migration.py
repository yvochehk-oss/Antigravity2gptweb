with open("source_code/0.2_RAG系统/project-rag-v1.1/alembic/versions/012_document_storage_paths.py", "r") as f:
    content = f.read()

content = content.replace(
    'if not rows:\n        raise RuntimeError("documents table is empty; refusing path migration")',
    'if not rows:\n        return []'
)

with open("source_code/0.2_RAG系统/project-rag-v1.1/alembic/versions/012_document_storage_paths.py", "w") as f:
    f.write(content)
