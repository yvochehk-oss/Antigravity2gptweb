with open("source_code/0.2_RAG系统/project-rag-v1.1/alembic/versions/012_document_storage_paths.py", "r") as f:
    content = f.read()

content = content.replace("    connection.execute(\n        text(", "    if plan:\n        connection.execute(\n            text(")
content = content.replace("            )\n        ),\n        plan\n    )", "            )\n            ),\n            plan\n        )")

with open("source_code/0.2_RAG系统/project-rag-v1.1/alembic/versions/012_document_storage_paths.py", "w") as f:
    f.write(content)
