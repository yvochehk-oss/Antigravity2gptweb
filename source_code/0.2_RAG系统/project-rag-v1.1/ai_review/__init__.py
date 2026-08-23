"""
AI Review 模块

包含 AI Review 相关的所有组件

V1.1: 接入 ProjectRAG (通过 rag_client)
"""

from .models import FactsSnapshot, AIReviewRun, MetricVersion
from .db import Base
from .snapshot_service import FactsSnapshotService
from .run_service import AIReviewRunService
from .review_service import AIReviewService
from .rag_client import ProjectRAGClient

__all__ = [
    "Base",
    "FactsSnapshot",
    "AIReviewRun",
    "MetricVersion",
    "FactsSnapshotService",
    "AIReviewRunService",
    "AIReviewService",
    "ProjectRAGClient",
]
