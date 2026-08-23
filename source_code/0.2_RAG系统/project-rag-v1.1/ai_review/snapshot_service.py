"""
Facts Snapshot Service

管理 Facts 快照的创建和查询
"""

import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from .models import FactsSnapshot


class FactsSnapshotService:
    """
    Facts 快照服务
    
    职责：
    1. 创建 Facts 快照
    2. 查询历史快照
    3. 关联 AI Review Runs
    """
    
    def __init__(self, db: Session):
        self._db = db
    
    def create_snapshot(
        self,
        project_id: int,
        project_code: str,
        facts_data: dict,
        as_of: str,
        facts_version: str,
        analytics_contract_version: str = "1.0",
        created_by: str = "system"
    ) -> FactsSnapshot:
        """
        创建 Facts 快照
        
        Args:
            project_id: 项目 ID
            project_code: 项目编码
            facts_data: Facts 数据（JSON）
            as_of: 数据截止时间
            facts_version: Facts 版本
            analytics_contract_version: Analytics Contract 版本
            created_by: 创建者
            
        Returns:
            FactsSnapshot: 创建的快照
        """
        snapshot = FactsSnapshot(
            id=str(uuid.uuid4()),
            project_id=project_id,
            project_code=project_code,
            # SQLAlchemy's JSON type expects the Python object.  Serialising
            # here would store a JSON *string*, which makes a later replay
            # indistinguishable from an opaque/untrusted payload and breaks
            # the canonical-facts contract.
            facts_data=facts_data,
            as_of=as_of,
            facts_version=facts_version,
            analytics_contract_version=analytics_contract_version,
            created_at=datetime.now(timezone.utc).isoformat(),
            created_by=created_by
        )
        
        self._db.add(snapshot)
        self._db.commit()
        self._db.refresh(snapshot)
        
        return snapshot
    
    def get_snapshot(self, snapshot_id: str) -> Optional[FactsSnapshot]:
        """获取指定快照"""
        return self._db.query(FactsSnapshot).filter(
            FactsSnapshot.id == snapshot_id
        ).first()
    
    def get_latest_snapshot(self, project_code: str) -> Optional[FactsSnapshot]:
        """获取项目的最新快照"""
        return self._db.query(FactsSnapshot).filter(
            FactsSnapshot.project_code == project_code
        ).order_by(
            FactsSnapshot.created_at.desc()
        ).first()
    
    def get_snapshots_at_time(
        self,
        project_code: str,
        as_of: str
    ) -> Optional[FactsSnapshot]:
        """
        获取指定时间点的快照
        
        用于历史复盘
        """
        # 查找最接近指定时间且不晚于该时间的快照
        return self._db.query(FactsSnapshot).filter(
            FactsSnapshot.project_code == project_code,
            FactsSnapshot.as_of <= as_of
        ).order_by(
            FactsSnapshot.as_of.desc()
        ).first()
    
    def get_snapshots_history(
        self,
        project_code: str,
        limit: int = 10
    ) -> list[FactsSnapshot]:
        """获取项目的快照历史"""
        return self._db.query(FactsSnapshot).filter(
            FactsSnapshot.project_code == project_code
        ).order_by(
            FactsSnapshot.created_at.desc()
        ).limit(limit).all()
    
    def get_snapshot_by_facts_version(
        self,
        project_code: str,
        facts_version: str
    ) -> Optional[FactsSnapshot]:
        """通过 Facts 版本获取快照"""
        return self._db.query(FactsSnapshot).filter(
            FactsSnapshot.project_code == project_code,
            FactsSnapshot.facts_version == facts_version
        ).first()
    
    def delete_old_snapshots(
        self,
        days: int = 90
    ) -> int:
        """
        删除旧快照
        
        Args:
            days: 保留天数
            
        Returns:
            删除的快照数量
        """
        from datetime import timedelta
        
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        
        count = self._db.query(FactsSnapshot).filter(
            FactsSnapshot.created_at < cutoff.isoformat()
        ).delete()
        
        self._db.commit()
        
        return count
