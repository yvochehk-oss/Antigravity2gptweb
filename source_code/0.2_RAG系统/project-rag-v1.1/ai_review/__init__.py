"""
AI Review 模块

包含 AI Review 相关的所有组件

V1.1: 接入 ProjectRAG (通过 rag_client)
"""

from .db import Base
from .evidence_pack_service import RAGEvidencePackService
from .models import AIReviewRun, FactsSnapshot, MetricVersion, RAGEvidencePack
from .rag_client import ProjectRAGClient
from .review_service import AIReviewService
from .run_service import AIReviewRunService
from .snapshot_service import FactsSnapshotService

__all__ = [
    "Base",
    "FactsSnapshot",
    "AIReviewRun",
    "MetricVersion",
    "RAGEvidencePack",
    "RAGEvidencePackService",
    "FactsSnapshotService",
    "AIReviewRunService",
    "AIReviewService",
    "ProjectRAGClient",
]
