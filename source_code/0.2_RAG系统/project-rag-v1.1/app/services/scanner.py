import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models import Document, MountConfig, Project
from .documents import register_local_path
from .storage import SAFE_EXTS, PathTraversalError, StorageError, validate_safe_directory

logger = get_logger(__name__)

def sync_all_mounts(db: Session) -> dict:
    """Scan all active mount points and sync documents."""
    mounts = db.execute(select(MountConfig).where(MountConfig.active.is_(True))).scalars().all()

    results = {"scanned": 0, "added": 0, "errors": 0, "projects_created": 0}

    for mount in mounts:
        try:
            root_path = validate_safe_directory(mount.path)
        except (StorageError, PathTraversalError) as exc:
            logger.warning("Mount path rejected: %s (%s)", mount.path, exc)
            results["errors"] += 1
            continue

        try:
            for item in root_path.iterdir():
                if not item.is_dir():
                    continue

                # Each subdirectory is considered a project folder
                if item.is_symlink() or not item.is_dir():
                    continue
                project_name = item.name
                if project_name.startswith('.'):
                    continue

                # 1. Find or create Project (support folder names like 01_天府国际金融中心二期_CD-TF-001)
                parts = project_name.split('_')
                code_candidate = parts[-1] if len(parts) > 1 else project_name
                project = db.scalar(
                    select(Project).where(
                        (Project.project_code == project_name)
                        | (Project.name == project_name)
                        | (Project.project_code == code_candidate)
                    )
                )
                if not project:
                    all_projects = db.execute(select(Project)).scalars().all()
                    for p in all_projects:
                        if p.project_code and p.project_code in project_name:
                            project = p
                            break

                if not project:
                    # Create new project
                    safe_slug = re.sub(r"[^A-Z0-9_-]+", "-", project_name.upper()).strip("-") or "PROJECT"
                    suffix = hashlib.sha1(project_name.encode("utf-8")).hexdigest()[:8].upper()
                    project = Project(
                        project_code=f"AUTO-{safe_slug[:45]}-{suffix}",
                        name=project_name,
                        status="planning",
                        # A scanner cannot invent a contract value or site.
                        # Keep the row importable with explicit placeholders;
                        # business users must fill these facts before using
                        # deterministic analytics.
                        contract_amount=Decimal("0.00"),
                        location="未填写",
                        note="由安全挂载扫描自动创建；contract_amount/location 待业务补录",
                    )
                    db.add(project)
                    try:
                        db.commit()
                        db.refresh(project)
                    except Exception:
                        db.rollback()
                        logger.exception("Failed to auto-create project %s", project_name)
                        results["errors"] += 1
                        continue
                    results["projects_created"] += 1
                    logger.info(f"Auto-created project: {project.name}")

                # 2. Scan files in project directory
                for filepath in item.rglob("*"):
                    if filepath.is_symlink() or not filepath.is_file():
                        continue
                    if filepath.suffix.lower() not in SAFE_EXTS or filepath.name.startswith("."):
                        continue

                    results["scanned"] += 1

                    # Check if this exact canonical path already exists in documents.
                    try:
                        resolved_file = filepath.resolve()
                        validate_safe_directory(resolved_file.parent)
                    except (StorageError, PathTraversalError, OSError) as exc:
                        logger.warning("Skipping unsafe local file %s: %s", filepath, exc)
                        results["errors"] += 1
                        continue
                    abs_path_str = str(resolved_file)
                    exists = db.scalar(
                        select(Document).where(Document.original_path == abs_path_str).limit(1)
                    )

                    if not exists:
                        try:
                            register_local_path(db, project, resolved_file, auto_parse=True)
                            results["added"] += 1
                        except Exception as exc:
                            logger.error("Failed to register local file %s: %s", filepath, exc)
                            db.rollback()
                            results["errors"] += 1

        except Exception as e:
            logger.error(f"Error scanning mount {mount.path}: {e}")
            db.rollback()
            results["errors"] += 1

        # Update last scan time
        mount.last_scan_at = datetime.now(timezone.utc)
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to persist last scan time for mount %s", mount.id)
            results["errors"] += 1

    return results
