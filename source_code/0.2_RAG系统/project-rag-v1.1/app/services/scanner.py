import os
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from ..models import MountConfig, Project, Document
from .documents import register_local_path
from .storage import SAFE_EXTS
from ..logging_config import get_logger

logger = get_logger(__name__)

def sync_all_mounts(db: Session) -> dict:
    """Scan all active mount points and sync documents."""
    mounts = db.execute(select(MountConfig).where(MountConfig.active == True)).scalars().all()
    
    results = {"scanned": 0, "added": 0, "errors": 0, "projects_created": 0}
    
    for mount in mounts:
        root_path = Path(mount.path)
        if not root_path.exists() or not root_path.is_dir():
            logger.warning(f"Mount path not found or not a directory: {mount.path}")
            results["errors"] += 1
            continue
            
        try:
            for item in root_path.iterdir():
                if not item.is_dir():
                    continue
                
                # Each subdirectory is considered a project folder
                project_name = item.name
                if project_name.startswith('.'):
                    continue
                
                # 1. Find or create Project
                project = db.scalar(
                    select(Project).where(
                        (Project.name == project_name) | (Project.project_code == project_name)
                    )
                )
                
                if not project:
                    # Create new project
                    project = Project(
                        project_code=f"AUTO-{project_name[:20].upper()}",
                        name=project_name,
                        status="planning",
                        department="自动挂载",
                    )
                    db.add(project)
                    db.commit()
                    db.refresh(project)
                    results["projects_created"] += 1
                    logger.info(f"Auto-created project: {project.name}")
                
                # 2. Scan files in project directory
                for filepath in item.rglob('*'):
                    if filepath.is_file() and filepath.suffix.lower().lstrip('.') in SAFE_EXTS:
                        if filepath.name.startswith('.'):
                            continue
                            
                        results["scanned"] += 1
                        
                        # Check if this exact file path already exists in documents
                        abs_path_str = str(filepath.absolute())
                        exists = db.scalar(
                            select(Document).where(Document.original_path == abs_path_str).limit(1)
                        )
                        
                        if not exists:
                            try:
                                register_local_path(db, project, filepath, auto_parse=True)
                                results["added"] += 1
                            except Exception as e:
                                logger.error(f"Failed to register local file {filepath}: {e}")
                                results["errors"] += 1
                                
        except Exception as e:
            logger.error(f"Error scanning mount {mount.path}: {e}")
            results["errors"] += 1
            
        # Update last scan time
        mount.last_scan_at = datetime.now(timezone.utc)
        db.commit()
        
    return results

