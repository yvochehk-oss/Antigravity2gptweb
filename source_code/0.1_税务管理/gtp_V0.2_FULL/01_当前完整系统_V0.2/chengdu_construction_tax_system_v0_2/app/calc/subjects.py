"""V0.2: 计算引擎子包使用统一常量。"""
from ..constants import KIND_TO_CATEGORY


def kind_to_category(kind: str) -> str:
    return KIND_TO_CATEGORY.get(kind, "other")