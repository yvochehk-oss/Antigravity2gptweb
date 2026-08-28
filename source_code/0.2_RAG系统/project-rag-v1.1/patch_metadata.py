import re

with open("app/services/metadata.py", "r") as f:
    content = f.read()

# We need to replace the section from "code_candidates = {" to "result[\"confidence\"] += 0.08" inside infer_from_filename.
old_block = """    code_candidates = {m.group(0).upper() for m in _ENTITY_CODE_RE.finditer(name)}
    if len(code_candidates) == 1:
        candidate = next(iter(code_candidates))
        result["entity_code_candidate"] = candidate
        resolved = resolve_entity_reference(candidate, rows)
        result["entity_resolution_status"] = resolved["status"]
        if resolved["status"] == "RESOLVED":
            result["entity_code"] = resolved["entity_code"]
            result["entity_name"] = resolved.get("name", "")
            result["entity_tax_id"] = resolved.get("tax_id", "")
            result["entity_match_source"] = "code"
            result["business_role"] = result["business_role"] or resolved.get("business_role", "")
            result["confidence"] += 0.08
    elif len(code_candidates) > 1:
        result["entity_code_candidate"] = ",".join(sorted(code_candidates))
        result["entity_resolution_status"] = "CONFLICT"

    # A tax id or exact canonical company name may resolve the entity when no
    # code was printed.  Tax id/name matches are unique-only by design.
    references: list[tuple[str, str]] = [(tax_id, "tax_id") for tax_id in dict.fromkeys(_TAX_ID_RE.findall(name))]
    if not references:
        name_matches = {
            candidate_name
            for row in rows
            for candidate_name in (
                str(row.get("name") or "").strip(),
                str(row.get("short_name") or "").strip(),
            )
            if candidate_name and candidate_name in name
        }
        references = [(item, "name") for item in sorted(name_matches, key=len, reverse=True)]

    for reference, source in references:
        resolved = resolve_entity_reference(reference, rows)
        if resolved["status"] != "RESOLVED":
            if result["entity_resolution_status"] == "UNRESOLVED":
                result["entity_resolution_status"] = resolved["status"]
            continue
        if result["entity_code"] and result["entity_code"] != resolved["entity_code"]:
            result["entity_resolution_status"] = "CONFLICT"
            result["entity_code"] = ""
            result["entity_match_source"] = ""
            break
        result["entity_code"] = resolved["entity_code"]
        result["entity_name"] = resolved.get("name", "")
        result["entity_tax_id"] = resolved.get("tax_id", "")
        result["entity_match_source"] = source
        result["entity_resolution_status"] = "RESOLVED"
        result["business_role"] = result["business_role"] or resolved.get("business_role", "")
        result["confidence"] += 0.08"""

new_block = """    # 收集所有的实体引用（包括代码、税号、名称）并统一解析
    code_candidates = {m.group(0).upper() for m in _ENTITY_CODE_RE.finditer(name)}
    if code_candidates:
        result["entity_code_candidate"] = ",".join(sorted(code_candidates))
    
    tax_ids = list(dict.fromkeys(_TAX_ID_RE.findall(name)))
    
    name_matches = {
        candidate_name
        for row in rows
        for candidate_name in (
            str(row.get("name") or "").strip(),
            str(row.get("short_name") or "").strip(),
        )
        if candidate_name and candidate_name in name
    }
    
    # 统一把所有匹配到的候选合并解析
    # reference, source
    all_refs = [(c, "code") for c in code_candidates] + [(t, "tax_id") for t in tax_ids] + [(n, "name") for n in sorted(name_matches, key=len, reverse=True)]
    
    resolved_entities = {}
    for ref, source in all_refs:
        r = resolve_entity_reference(ref, rows)
        if r["status"] == "RESOLVED":
            resolved_entities[r["entity_code"]] = (r, source)
    
    if len(resolved_entities) == 1:
        # 只解析出一个确定的实体，那它就是主实体
        r, source = list(resolved_entities.values())[0]
        result["entity_code"] = r["entity_code"]
        result["entity_name"] = r.get("name", "")
        result["entity_tax_id"] = r.get("tax_id", "")
        result["entity_match_source"] = source
        result["entity_resolution_status"] = "RESOLVED"
        result["business_role"] = r.get("business_role", "")
        result["confidence"] += 0.08
    elif len(resolved_entities) > 1:
        # 解析出了多个实体！尝试根据 business_role 拆分主副
        owners = []
        counterparties = []
        for r, source in resolved_entities.values():
            role = r.get("business_role", "")
            if role in ("A", "B"):
                owners.append((r, source))
            else:
                counterparties.append((r, source))
        
        # 如果恰好有一个 owner 和一个（或多个）counterparty，我们就把 owner 赋予主实体
        if len(owners) == 1:
            owner_r, owner_source = owners[0]
            result["entity_code"] = owner_r["entity_code"]
            result["entity_name"] = owner_r.get("name", "")
            result["entity_tax_id"] = owner_r.get("tax_id", "")
            result["entity_match_source"] = owner_source
            result["entity_resolution_status"] = "RESOLVED"
            result["business_role"] = owner_r.get("business_role", "")
            result["confidence"] += 0.08
            
            # 顺便填上对方信息 (如果刚好只有一个)
            if len(counterparties) == 1:
                cp_r, cp_source = counterparties[0]
                result["counterparty_code"] = cp_r["entity_code"]
                result["counterparty_name"] = cp_r.get("name", "")
                result["counterparty_tax_id"] = cp_r.get("tax_id", "")
                result["counterparty_resolution_status"] = "RESOLVED"
        else:
            # 无法明确区分，或者有多个 owner
            result["entity_resolution_status"] = "CONFLICT"
    elif all_refs:
        # 有提取到，但都没能 resolved
        result["entity_resolution_status"] = "UNRESOLVED"
"""

content = content.replace(old_block, new_block)

with open("app/services/metadata.py", "w") as f:
    f.write(content)
