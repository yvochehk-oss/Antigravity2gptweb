"""Canonical 26 system-internal entity codes shared by Tax and RAG.

Only numbered A/B/C/D codes identify system-internal units. External
counterparties live in ``external_parties`` and may participate in project tax
planning, but they are not part of internal consolidated profit.

External-party aliases are input-boundary identifiers only.  Persistent
references must always use the canonical code returned by
``map_to_standard_external_code``.
"""
from __future__ import annotations
import re
from typing import Any

CANONICAL_ENTITY_CODES = frozenset(
    {f"A{i:02d}" for i in range(1, 12)}
    | {f"B{i:02d}" for i in range(1, 11)}
    | {f"C{i:02d}" for i in range(1, 3)}
    | {f"D{i:02d}" for i in range(1, 4)}
)
CANONICAL_ENTITY_CODE_RE = re.compile(
    r"^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C0[12]|D0[1-3])$"
)
BUSINESS_ROLE_CODES = frozenset({"A", "B", "C", "D"})
VIRTUAL_ENTITY_CODES = frozenset({"A", "B", "C", "D", "甲", "乙", "丙", "丁"})
CANONICAL_ENTITY_RANGE_TEXT = "A01-A11, B01-B10, C01-C02, D01-D03"


def normalize_entity_code(value: str | None) -> str | None:
    if value is None:
        return None
    code = str(value).strip().upper()
    return code or None


def is_canonical_entity_code(value: str | None) -> bool:
    code = normalize_entity_code(value)
    return bool(code and code in CANONICAL_ENTITY_CODES and CANONICAL_ENTITY_CODE_RE.fullmatch(code))


def validate_entity_code(value: str | None) -> str | None:
    code = normalize_entity_code(value)
    if code is None:
        return None
    if not is_canonical_entity_code(code):
        raise ValueError(f"invalid entity_code {value!r}; expected {CANONICAL_ENTITY_RANGE_TEXT}")
    return code


EXTERNAL_ENTITY_PRESETS: dict[str, dict[str, Any]] = {
    "EA": {
        "code": "EA",
        "name": "四川省建筑科学研究院特种技术服务中心",
        "short_name": "省建科院特种技术中心",
        "kind": "construction",
        "business_role": "construction",
        "role_code": "EA",
        "tax_id": "91510100MA61KKKK33",
        "note": "系统外专业分包（超高层深基坑地质监测与技术咨询）",
        "aliases": ("EXT-EXP", "EA01", "EA1", "省建科院", "深基坑地质监测", "基坑监测"),
    },
    "EB": {
        "code": "EB",
        "name": "攀钢集团攀枝花钢钒物资销售有限公司",
        "short_name": "攀钢钢钒物资",
        "kind": "trade",
        "business_role": "trade",
        "role_code": "EB",
        "tax_id": "91510400MA61EEEE77",
        "note": "系统外材料供应商（特种高强抗震合金钢直采供货）",
        "aliases": ("EXT-PG", "EB01", "EB1", "攀钢", "攀钢集团", "合金钢直采"),
    },
    "EC": {
        "code": "EC",
        "name": "四川中泰建筑劳务分包有限公司",
        "short_name": "中泰劳务",
        "kind": "labor",
        "business_role": "labor",
        "role_code": "EC",
        "tax_id": "91510100MA61LLLL44",
        "note": "系统外建筑劳务分包公司",
        "aliases": ("EXT-LABOR", "EC01", "EC1", "中泰劳务"),
    },
    "ED": {
        "code": "ED",
        "name": "重庆巨力重型起重设备吊装公司",
        "short_name": "重庆巨力吊装",
        "kind": "equipment",
        "business_role": "equipment",
        "role_code": "ED",
        "tax_id": "91500100MA61GGGG99",
        "note": "系统外工程设备/起重吊装单位",
        "aliases": ("EXT-CQ", "EXT-CRANE", "ED01", "ED1", "重庆巨力", "巨力吊装", "重交大件", "履带吊租赁", "超重型履带吊"),
    },
    "E0": {
        "code": "E0",
        "name": "成都市天府新区金融城投公司",
        "short_name": "天府金融城投",
        "kind": "owner",
        "business_role": "owner",
        "role_code": "E0",
        "tax_id": "91510100MA61AAAA11",
        "note": "项目发包方/外部业主单位",
        "aliases": ("EXT-TF", "EXT-OWNER", "E01", "天府城投", "业主单位"),
    },
}

EXTERNAL_ALIAS_TO_CODE: dict[str, str] = {}
for _k, _meta in EXTERNAL_ENTITY_PRESETS.items():
    EXTERNAL_ALIAS_TO_CODE[_k] = _k
    for _al in _meta.get("aliases", ()):
        EXTERNAL_ALIAS_TO_CODE[_al.upper()] = _k


def map_to_standard_external_code(value: str | None) -> str | None:
    """Normalize an external-party code/alias to its persistent canonical code."""
    if not value:
        return None
    val = str(value).strip().upper()
    return EXTERNAL_ALIAS_TO_CODE.get(val, val) or None


def get_external_preset(code_or_alias: str | None) -> dict[str, Any] | None:
    if not code_or_alias:
        return None
    std_code = map_to_standard_external_code(code_or_alias)
    if std_code and std_code in EXTERNAL_ENTITY_PRESETS:
        return EXTERNAL_ENTITY_PRESETS[std_code]
    return None
