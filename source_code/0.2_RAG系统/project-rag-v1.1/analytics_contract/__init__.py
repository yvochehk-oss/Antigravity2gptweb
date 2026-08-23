"""
Analytics Contract Module

统一的 Analytics 契约包：
- 指标定义（metrics）
- 维度定义（dimensions）
- 实体定义（entities）
- 关系定义（relationships）
- 测试用例
"""

from .contract import metrics, dimensions, entities, relationships

__all__ = [
    "metrics",
    "dimensions",
    "entities",
    "relationships",
]

__version__ = "1.0.0"