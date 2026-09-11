with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata_confidence.py", "r") as f:
    content = f.read()

old_re = r'_CANONICAL_CODE_RE = re.compile(\n    r"^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C(?:0[1-2])|D(?:0[1-3]))$"\n)'
new_re = r'_CANONICAL_CODE_RE = re.compile(\n    r"^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C(?:0[1-2])|D(?:0[1-3])|EXT-[A-Z0-9]+)$"\n)'
content = content.replace(old_re, new_re)

old_func = """def is_canonical_entity_code(value: str | None) -> bool:
    code = str(value or "").strip().upper()
    return bool(code and code in _CANONICAL_ENTITY_CODES and _CANONICAL_CODE_RE.fullmatch(code))"""
new_func = """def is_canonical_entity_code(value: str | None) -> bool:
    code = str(value or "").strip().upper()
    return bool(code and (code in _CANONICAL_ENTITY_CODES or code.startswith("EXT-")) and _CANONICAL_CODE_RE.fullmatch(code))"""
content = content.replace(old_func, new_func)

with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata_confidence.py", "w") as f:
    f.write(content)
