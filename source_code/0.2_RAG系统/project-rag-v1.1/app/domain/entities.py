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


def is_canonical_internal_code(value: str | None) -> bool:
    """Return True if value is a canonical system-internal code (A01-A11, B01-B10, C01-C02, D01-D03)."""
    return is_canonical_entity_code(value)


CANONICAL_EXTERNAL_CODE_RE = re.compile(
    r"^(?:E[1-9]\d*|"   # E1 / E100 / E12222 / E999999（无零填充）
    r"[EA-ED][1-9]\d*)"  # EA1 / EB1 / EC1 / ED1 / EA1000000 ...
    r"$",
    re.IGNORECASE,
)


def is_canonical_external_code(value: str | None) -> bool:
    """Return True if value matches the canonical external-party code standard (E01-E99, EA01-EA99, EB01-EB99, etc.)."""
    if not value:
        return False
    code = str(value).strip().upper()
    return bool(code in EXTERNAL_ENTITY_PRESETS or CANONICAL_EXTERNAL_CODE_RE.fullmatch(code))


def is_canonical_party_code(value: str | None) -> bool:
    """Return True if value is either a canonical internal entity or a canonical external party."""
    return is_canonical_internal_code(value) or is_canonical_external_code(value)


def normalize_party_code(value: str | None) -> str | None:
    """Normalize and resolve any entity/counterparty code or alias to its persistent canonical code."""
    if not value:
        return None
    raw = str(value).strip().upper()
    if is_canonical_internal_code(raw):
        return raw
    ext = map_to_standard_external_code(raw)
    if ext:
        return ext
    return None


EXTERNAL_ENTITY_PRESETS: dict[str, dict[str, Any]] = {
    # 外部专业分包 / 特种施工 (Construction)
    "EA01": {
        "code": "EA01",
        "name": "四川省建筑科学研究院特种技术服务中心",
        "short_name": "省建科院特种技术中心",
        "kind": "construction",
        "business_role": "construction",
        "role_code": "EA01",
        "tax_id": "91510100MA61KKKK33",
        "note": "系统外专业分包（超高层深基坑地质监测与技术咨询）",
        "aliases": ("EA", "EA1", "EXT-EXP", "省建科院", "深基坑地质监测", "基坑监测", "中建西南地勘院技术专家组"),
    },
    "EA02": {
        "code": "EA02",
        "name": "长航特种工程潜水与打捞公司",
        "short_name": "长航潜水",
        "kind": "construction",
        "business_role": "construction",
        "role_code": "EA02",
        "tax_id": "91500100MA61HHHH00",
        "note": "系统外特种专业分包（大桥深水基础潜水作业与水下切割）",
        "aliases": ("EXT-SHIP", "长航潜水", "长航打捞", "水下潜水工程"),
    },
    # 外部材料供应商 / 物资贸易 (Trade / Supplier)
    "EB01": {
        "code": "EB01",
        "name": "攀钢集团攀枝花钢钒物资销售有限公司",
        "short_name": "攀钢钢钒物资",
        "kind": "trade",
        "business_role": "trade",
        "role_code": "EB01",
        "tax_id": "91510400MA61EEEE77",
        "note": "系统外材料供应商（特种高强抗震合金钢直采供货）",
        "aliases": ("EB", "EB1", "EXT-PG", "攀钢", "攀钢集团", "合金钢直采", "攀钢集团攀枝花钢铁钒物资销售有限公司", "EXT-PG-STEEL", "攀钢集团特种钢材直销部"),
    },
    "EB02": {
        "code": "EB02",
        "name": "西南特种混凝土骨料直供站",
        "short_name": "特种商砼",
        "kind": "supplier",
        "business_role": "trade",
        "role_code": "EB02",
        "tax_id": "91500100MA61FFFF88",
        "note": "系统外商品混凝土与骨料直供站",
        "aliases": ("EXT-CONC", "EXT-XN-CONCRETE", "特种商砼", "西南商砼", "西南特种商品混凝土直供配送有限公司"),
    },
    "EB03": {
        "code": "EB03",
        "name": "四川省生态林业苗木繁育中心",
        "short_name": "生态林业",
        "kind": "supplier",
        "business_role": "trade",
        "role_code": "EB03",
        "tax_id": "91510800MA61JJJJ55",
        "note": "系统外生态林业苗木供货商",
        "aliases": ("EXT-TREE", "生态林业", "国家天然林", "EXT-GY-FOREST"),
    },
    # 外部建筑劳务分包 (Labor)
    "EC01": {
        "code": "EC01",
        "name": "四川中泰建筑劳务分包有限公司",
        "short_name": "中泰劳务",
        "kind": "labor",
        "business_role": "labor",
        "role_code": "EC01",
        "tax_id": "91510100MA61LLLL44",
        "note": "系统外建筑劳务分包公司",
        "aliases": ("EC", "EC1", "EXT-LABOR", "中泰劳务"),
    },
    # 外部机械租赁 / 设备吊装 (Equipment)
    "ED01": {
        "code": "ED01",
        "name": "重庆巨力重型起重设备吊装公司",
        "short_name": "重庆重交起重",
        "kind": "equipment",
        "business_role": "equipment",
        "role_code": "ED01",
        "tax_id": "91500100MA61GGGG99",
        "note": "系统外工程设备/起重吊装单位",
        "aliases": ("ED", "ED1", "EXT-CQ", "EXT-CQ-HEAVY-CRANE", "EXT-CRANE", "重庆巨力", "巨力吊装", "重交大件", "重庆重交大件起重吊装工程有限公司", "履带吊租赁", "超重型履带吊"),
    },
    # 外部业主单位 / 发包方 (Owner)
    "E01": {
        "code": "E01",
        "name": "成都市天府新区金融城投公司",
        "short_name": "天府金融城投",
        "kind": "owner",
        "business_role": "owner",
        "role_code": "E01",
        "tax_id": "91510100MA61AAAA11",
        "note": "01项目发包方/外部业主单位",
        "aliases": ("E0", "EXT-TF", "EXT-OWNER", "天府城投", "业主单位"),
    },
    "E02": {
        "code": "E02",
        "name": "成渝高速公路开发投资集团有限公司",
        "short_name": "成渝高速投资",
        "kind": "owner",
        "business_role": "owner",
        "role_code": "E02",
        "tax_id": "91510100MA61BBBB22",
        "note": "02项目发包方/外部业主单位",
        "aliases": ("EXT-CY", "成渝高速", "成渝高速开发投资集团", "成渝投资"),
    },
    "E03": {
        "code": "E03",
        "name": "广元市利州区水务发展投资集团有限公司",
        "short_name": "广元利州水务",
        "kind": "owner",
        "business_role": "owner",
        "role_code": "E03",
        "tax_id": "91510800MA61CCCC33",
        "note": "03项目发包方/外部业主单位",
        "aliases": ("EXT-GY", "广元水务", "广元利州水务局城投平台"),
    },
}

EXTERNAL_ALIAS_TO_CODE: dict[str, str] = {}
for _k, _meta in EXTERNAL_ENTITY_PRESETS.items():
    for _identity in (
        _k,
        _meta.get("name"),
        _meta.get("short_name"),
        *_meta.get("aliases", ()),
    ):
        if _identity:
            EXTERNAL_ALIAS_TO_CODE[str(_identity).strip().upper()] = _k


def map_to_standard_external_code(value: str | None) -> str | None:
    """Normalize an external-party code/alias to its persistent canonical code.
    Fail-closed: Returns canonical code if resolved or already canonical, else None.
    """
    if not value:
        return None
    val = str(value).strip().upper()
    if val in EXTERNAL_ALIAS_TO_CODE:
        return EXTERNAL_ALIAS_TO_CODE[val]
    if is_canonical_external_code(val):
        return val
    return None


def get_external_preset(code_or_alias: str | None) -> dict[str, Any] | None:
    if not code_or_alias:
        return None
    std_code = map_to_standard_external_code(code_or_alias)
    if std_code and std_code in EXTERNAL_ENTITY_PRESETS:
        return EXTERNAL_ENTITY_PRESETS[std_code]
    return None
