with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata.py", "r") as f:
    content = f.read()

old_block = """    if len(resolved_entities) == 1:
        # 只解析出一个确定的实体，那它就是主实体
        r, source = list(resolved_entities.values())[0]
        result["entity_code"] = r["entity_code"]
        result["entity_name"] = r.get("name", "")
        result["entity_tax_id"] = r.get("tax_id", "")
        result["entity_match_source"] = source
        result["entity_resolution_status"] = "RESOLVED"
        result["business_role"] = r.get("business_role", "")
        result["confidence"] += 0.08"""

new_block = """    if len(resolved_entities) == 1:
        r, source = list(resolved_entities.values())[0]
        role = r.get("business_role", "")
        if role in ("A", "B"):
            result["entity_code"] = r["entity_code"]
            result["entity_name"] = r.get("name", "")
            result["entity_tax_id"] = r.get("tax_id", "")
            result["entity_match_source"] = source
            result["entity_resolution_status"] = "RESOLVED"
            result["business_role"] = role
            result["confidence"] += 0.08
        else:
            result["counterparty_code"] = r["entity_code"]
            result["counterparty_name"] = r.get("name", "")
            result["counterparty_tax_id"] = r.get("tax_id", "")
            result["counterparty_resolution_status"] = "RESOLVED"
            result["entity_resolution_status"] = "UNRESOLVED"
"""

content = content.replace(old_block, new_block)
with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata.py", "w") as f:
    f.write(content)
