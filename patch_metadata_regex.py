import re

with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata.py", "r") as f:
    content = f.read()

# Replace _CANONICAL_ENTITY_CODE_RE
old_re1 = r'_CANONICAL_ENTITY_CODE_RE = re.compile(r"^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C(?:0[1-2])|D(?:0[1-3]))$")'
new_re1 = r'_CANONICAL_ENTITY_CODE_RE = re.compile(r"^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C(?:0[1-2])|D(?:0[1-3])|EXT-[A-Z0-9]+)$")'
content = content.replace(old_re1, new_re1)

# Replace _ENTITY_CODE_RE
old_re2 = r'_ENTITY_CODE_RE = re.compile(\n    r"(?<![A-Za-z0-9])(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C(?:0[1-2])|D(?:0[1-3]))(?![A-Za-z0-9])",\n    re.IGNORECASE,\n)'
new_re2 = r'_ENTITY_CODE_RE = re.compile(\n    r"(?<![A-Za-z0-9])(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C(?:0[1-2])|D(?:0[1-3])|EXT-[A-Z0-9]+)(?![A-Za-z0-9])",\n    re.IGNORECASE,\n)'
content = content.replace(old_re2, new_re2)

# Replace is_canonical_entity_code
old_func = """def is_canonical_entity_code(value: str | None) -> bool:
    code = normalize_entity_code(value)
    return bool(code and code in CANONICAL_ENTITY_CODES and _CANONICAL_ENTITY_CODE_RE.fullmatch(code))"""
new_func = """def is_canonical_entity_code(value: str | None) -> bool:
    code = normalize_entity_code(value)
    return bool(code and (code in CANONICAL_ENTITY_CODES or code.startswith("EXT-")) and _CANONICAL_ENTITY_CODE_RE.fullmatch(code))"""
content = content.replace(old_func, new_func)

# Replace loading external entities in _load_runtime_entity_cache
# We need to change `Entity` to also fetch from `ExternalParty`
old_load = """        from ..models import Entity

        with SessionLocal() as db:
            entities = db.scalars(
                select(Entity)
                .where(func.lower(Entity.status) == "active")
                .order_by(Entity.entity_code, Entity.id)
            ).all()"""

new_load = """        from ..models import Entity, ExternalParty

        with SessionLocal() as db:
            entities = db.scalars(
                select(Entity)
                .where(func.lower(Entity.status) == "active")
                .order_by(Entity.entity_code, Entity.id)
            ).all()
            
            ext_parties = db.scalars(
                select(ExternalParty)
                .where(ExternalParty.active == True)
            ).all()
            
            entities = list(entities) + list(ext_parties)"""
content = content.replace(old_load, new_load)

# Wait, `entity.entity_code` doesn't exist on ExternalParty, it's `entity.code`!
old_loop = """            rows: list[dict[str, Any]] = []
            for entity in entities:
                code = normalize_entity_code(entity.entity_code)"""
new_loop = """            rows: list[dict[str, Any]] = []
            for entity in entities:
                raw_code = getattr(entity, "entity_code", getattr(entity, "code", ""))
                code = normalize_entity_code(raw_code)"""
content = content.replace(old_loop, new_loop)

# business_role is None for ExternalParty, let's assign "E"
old_role = """                    "short_name": entity.short_name or "",
                    "business_role": entity.business_role or code[0],"""
new_role = """                    "short_name": entity.short_name or "",
                    "business_role": getattr(entity, "business_role", None) or ("E" if code.startswith("EXT-") else code[0]),"""
content = content.replace(old_role, new_role)

with open("source_code/0.2_RAG系统/project-rag-v1.1/app/services/metadata.py", "w") as f:
    f.write(content)

