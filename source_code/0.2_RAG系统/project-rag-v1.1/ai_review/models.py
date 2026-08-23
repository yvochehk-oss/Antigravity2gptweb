"""
Facts Snapshot 和 AI Review Runs 数据模型

用于 AI Review 可追溯性
"""

import json

from sqlalchemy import String, Integer, Float, ForeignKey, Text, JSON, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base
import uuid
from datetime import datetime


class FactsSnapshot(Base):
    """
    Facts 快照表
    
    保存每次 AI Review 运行前的 Facts 数据，用于历史追溯
    """
    __tablename__ = "facts_snapshots"
    
    id: Mapped[str] = mapped_column(
        String(36), 
        primary_key=True, 
        default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[int] = mapped_column(
        Integer, 
        ForeignKey("projects.id"), 
        index=True
    )
    project_code: Mapped[str] = mapped_column(
        String(64), 
        index=True
    )
    
    # Facts 数据（JSON 格式）
    facts_data: Mapped[dict] = mapped_column(
        JSON, 
        nullable=False
    )
    
    # 版本信息
    as_of: Mapped[str] = mapped_column(
        String(32),  # ISO 8601
        nullable=False,
        comment="数据截止时间"
    )
    facts_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Facts 版本标识"
    )
    analytics_contract_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="1.0",
        comment="Analytics Contract 版本"
    )
    
    # 快照元数据
    created_at: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=lambda: datetime.utcnow().isoformat()
    )
    created_by: Mapped[str] = mapped_column(
        String(64),
        nullable=True,
        comment="创建者（系统/用户）"
    )
    
    __table_args__ = (
        UniqueConstraint("project_code", "facts_version", name="uq_project_facts_version"),
    )
    
    def __repr__(self):
        return f"<FactsSnapshot {self.id} project={self.project_code} at {self.as_of}>"


class AIReviewRun(Base):
    """
    AI Review 运行记录表
    
    保存每次 AI Review 的完整上下文，用于审计和复盘
    """
    __tablename__ = "ai_review_runs"
    
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4())
    )
    
    # 项目信息
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id"),
        index=True
    )
    project_code: Mapped[str] = mapped_column(
        String(64),
        index=True
    )
    
    # 运行时间
    started_at: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=lambda: datetime.utcnow().isoformat()
    )
    finished_at: Mapped[str] = mapped_column(
        String(32),
        nullable=True
    )
    duration_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=True,
        comment="运行时长（毫秒）"
    )
    
    # Facts 快照引用
    facts_snapshot_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("facts_snapshots.id"),
        nullable=True,
        comment="关联的 Facts 快照"
    )
    
    # RAG 证据包引用
    rag_evidence_pack_id: Mapped[str] = mapped_column(
        String(36),
        nullable=True,
        comment="关联的 RAG 证据包"
    )
    
    # Prompt 版本
    prompt_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="1.0"
    )
    
    # LLM 配置
    model: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="使用的 LLM 模型"
    )
    
    # 审查结果
    result: Mapped[dict] = mapped_column(
        JSON,
        nullable=True,
        comment="AI Review 审查结果"
    )
    result_summary: Mapped[str] = mapped_column(
        Text,
        nullable=True,
        comment="结果摘要"
    )
    
    # 风险等级
    risk_level: Mapped[str] = mapped_column(
        String(16),
        nullable=True,
        comment="风险等级：LOW/MEDIUM/HIGH/CRITICAL"
    )
    
    # 状态
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="RUNNING",
        index=True,
        comment="状态：RUNNING/COMPLETED/FAILED"
    )
    
    # 错误信息
    error_message: Mapped[str] = mapped_column(
        Text,
        nullable=True,
        comment="错误信息"
    )
    
    # 元数据
    extra_metadata: Mapped[dict] = mapped_column(
        JSON,
        nullable=True,
        comment="额外元数据"
    )
    
    def __repr__(self):
        return f"<AIReviewRun {self.id} project={self.project_code} status={self.status}>"
    
    @property
    def health_score(self) -> float | None:
        """Return a canonical health score when one was supplied.

        ``None`` is intentional: AI Review must not turn an unavailable or
        unverified score into a fabricated numeric default.  The string
        decoding keeps history rows written by the pre-V1.1 stub readable.
        """
        result = self.result
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (TypeError, ValueError):
                return None
        if not isinstance(result, dict):
            return None
        value = result.get("health_score")
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


class MetricVersion(Base):
    """
    指标版本表
    
    记录指标版本的变更历史
    """
    __tablename__ = "metric_versions"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    metric_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True
    )
    version: Mapped[str] = mapped_column(
        String(16),
        nullable=False
    )
    effective_from: Mapped[str] = mapped_column(
        String(32),
        nullable=False
    )
    effective_to: Mapped[str] = mapped_column(
        String(32),
        nullable=True
    )
    deprecates: Mapped[str] = mapped_column(
        String(16),
        nullable=True,
        comment="废弃的前一版本"
    )
    change_reason: Mapped[str] = mapped_column(
        Text,
        nullable=True,
        comment="变更原因"
    )
    formula_engine: Mapped[str] = mapped_column(
        String(32),
        nullable=True,
        comment="计算引擎版本"
    )
    owner: Mapped[str] = mapped_column(
        String(64),
        nullable=True
    )
    approved_by: Mapped[str] = mapped_column(
        String(64),
        nullable=True
    )
    
    __table_args__ = (
        UniqueConstraint("metric_id", "version", name="uq_metric_version"),
    )
