"""
AI Review Run Service

管理 AI Review 运行记录
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from .models import AIReviewRun, FactsSnapshot


class AIReviewRunService:
    """
    AI Review 运行服务
    
    职责：
    1. 创建 AI Review 运行记录
    2. 更新运行结果
    3. 查询历史记录
    """
    
    def __init__(self, db: Session):
        self._db = db
    
    def create_run(
        self,
        project_id: int,
        project_code: str,
        facts_snapshot_id: Optional[str],
        model: str,
        prompt_version: str = "1.0",
        rag_evidence_pack_id: Optional[str] = None,
        created_by: str = "system"
    ) -> AIReviewRun:
        """
        创建 AI Review 运行记录
        
        Args:
            project_id: 项目 ID
            project_code: 项目编码
            facts_snapshot_id: Facts 快照 ID
            model: 使用的 LLM 模型
            prompt_version: Prompt 版本
            rag_evidence_pack_id: RAG 证据包 ID
            created_by: 创建者
            
        Returns:
            AIReviewRun: 创建的运行记录
        """
        run = AIReviewRun(
            id=str(uuid.uuid4()),
            project_id=project_id,
            project_code=project_code,
            started_at=datetime.now(timezone.utc).isoformat(),
            facts_snapshot_id=facts_snapshot_id,
            rag_evidence_pack_id=rag_evidence_pack_id,
            prompt_version=prompt_version,
            model=model,
            status="RUNNING",
        )
        
        self._db.add(run)
        self._db.commit()
        self._db.refresh(run)
        
        return run
    
    def complete_run(
        self,
        run_id: str,
        result: dict,
        result_summary: str,
        risk_level: str,
        metadata: Optional[dict] = None,
        run_status: str = "COMPLETED",
    ):
        """
        完成 AI Review 运行
        
        Args:
            run_id: 运行 ID
            result: 审查结果
            result_summary: 结果摘要
            risk_level: 风险等级
            metadata: 额外元数据
        """
        run = self._db.query(AIReviewRun).filter(
            AIReviewRun.id == run_id
        ).first()
        
        if not run:
            raise ValueError(f"AI Review Run not found: {run_id}")
        
        if run_status not in {"COMPLETED", "NEEDS_REVIEW"}:
            raise ValueError(f"Unsupported AI Review completion status: {run_status}")

        run.finished_at = datetime.now(timezone.utc).isoformat()
        run.status = run_status
        # ``result`` and ``extra_metadata`` are JSON columns.  Keep them as
        # dictionaries so callers can replay/audit the exact structured
        # result instead of receiving a JSON-encoded string.
        run.result = result
        run.result_summary = result_summary
        run.risk_level = risk_level
        run.extra_metadata = metadata or {}
        
        # 计算运行时长
        if run.started_at:
            start = datetime.fromisoformat(run.started_at.replace("Z", "+00:00"))
            end = datetime.now(timezone.utc)
            run.duration_ms = int((end - start).total_seconds() * 1000)
        
        self._db.commit()
        
        return run
    
    def fail_run(self, run_id: str, error_message: str):
        """
        标记 AI Review 运行失败
        
        Args:
            run_id: 运行 ID
            error_message: 错误信息
        """
        run = self._db.query(AIReviewRun).filter(
            AIReviewRun.id == run_id
        ).first()
        
        if not run:
            raise ValueError(f"AI Review Run not found: {run_id}")
        
        run.finished_at = datetime.now(timezone.utc).isoformat()
        run.status = "FAILED"
        run.error_message = error_message
        
        # 计算运行时长
        if run.started_at:
            start = datetime.fromisoformat(run.started_at.replace("Z", "+00:00"))
            end = datetime.now(timezone.utc)
            run.duration_ms = int((end - start).total_seconds() * 1000)
        
        self._db.commit()
        
        return run
    
    def get_run(self, run_id: str) -> Optional[AIReviewRun]:
        """获取指定运行记录"""
        return self._db.query(AIReviewRun).filter(
            AIReviewRun.id == run_id
        ).first()
    
    def get_runs_for_project(
        self,
        project_code: str,
        limit: int = 10,
        status: Optional[str] = None
    ) -> list[AIReviewRun]:
        """
        获取项目的 AI Review 运行记录
        
        Args:
            project_code: 项目编码
            limit: 返回数量
            status: 筛选状态
            
        Returns:
            运行记录列表
        """
        query = self._db.query(AIReviewRun).filter(
            AIReviewRun.project_code == project_code
        )
        
        if status:
            query = query.filter(AIReviewRun.status == status)
        
        return query.order_by(
            AIReviewRun.started_at.desc()
        ).limit(limit).all()
    
    def get_run_with_snapshot(
        self,
        run_id: str
    ) -> tuple[Optional[AIReviewRun], Optional[FactsSnapshot]]:
        """
        获取运行记录及其关联的 Facts 快照
        
        用于完整复盘
        """
        run = self.get_run(run_id)
        if not run:
            return None, None
        
        snapshot = None
        if run.facts_snapshot_id:
            from .snapshot_service import FactsSnapshotService
            service = FactsSnapshotService(self._db)
            snapshot = service.get_snapshot(run.facts_snapshot_id)
        
        return run, snapshot
    
    def get_latest_run(self, project_code: str) -> Optional[AIReviewRun]:
        """获取项目的最新运行记录"""
        return self._db.query(AIReviewRun).filter(
            AIReviewRun.project_code == project_code,
            AIReviewRun.status == "COMPLETED"
        ).order_by(
            AIReviewRun.started_at.desc()
        ).first()
    
    def get_risk_summary(self, project_code: str) -> dict:
        """
        获取项目的风险汇总
        
        Returns:
            包含风险等级统计的字典
        """
        runs = self._db.query(AIReviewRun).filter(
            AIReviewRun.project_code == project_code,
            AIReviewRun.status == "COMPLETED"
        ).all()
        
        if not runs:
            return {"total": 0, "by_level": {}}
        
        risk_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
        
        for run in runs:
            if run.risk_level in risk_counts:
                risk_counts[run.risk_level] += 1
        
        return {
            "total": len(runs),
            "by_level": risk_counts,
            "latest_risk": runs[0].risk_level if runs else None
        }
