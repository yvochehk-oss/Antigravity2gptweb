import hashlib
from pathlib import Path
import re
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models import Document, MountConfig, Project
from .documents import register_local_path
from .jobs import enqueue_parse
from ..security import read_file_limited
from .storage import SAFE_EXTS, PathTraversalError, StorageError, sha256_bytes, validate_safe_directory

logger = get_logger(__name__)


def sync_single_mount(db: Session, mount: MountConfig) -> dict:
    """Scan a single mount point incrementally (only new or changed files)."""
    results = {
        "mount_id": mount.id,
        "mount_path": mount.path,
        "scanned": 0,
        "added": 0,
        "updated": 0,
        "unchanged": 0,
        "errors": 0,
        "projects_created": 0,
    }

    try:
        root_path = validate_safe_directory(mount.path)
    except (StorageError, PathTraversalError) as exc:
        logger.warning("Mount path rejected: %s (%s)", mount.path, exc)
        results["errors"] += 1
        return results

    try:
        project_targets = _resolve_project_targets(root_path)
        for project_name, project_dir in project_targets:
            project, is_created = _find_or_create_project(db, project_name)
            if not project:
                results["errors"] += 1
                continue
            if is_created:
                results["projects_created"] += 1

            # Scan all files recursively inside this project directory
            for filepath in project_dir.rglob("*"):
                if filepath.is_symlink() or not filepath.is_file():
                    continue
                if filepath.suffix.lower() not in SAFE_EXTS or filepath.name.startswith("."):
                    continue

                results["scanned"] += 1

                try:
                    resolved_file = filepath.resolve()
                    validate_safe_directory(resolved_file.parent)
                    current_size = resolved_file.stat().st_size
                except (StorageError, PathTraversalError, OSError) as exc:
                    logger.warning("Skipping unsafe local file %s: %s", filepath, exc)
                    results["errors"] += 1
                    continue

                abs_path_str = str(resolved_file)
                existing_doc = db.scalar(
                    select(Document).where(Document.original_path == abs_path_str).limit(1)
                )

                if existing_doc:
                    # 检查文件是否发生变更 (基于 size 和 hash)
                    if existing_doc.size_bytes == current_size:
                        # 大小一致，读取哈希验证
                        try:
                            data = read_file_limited(resolved_file)
                            digest = sha256_bytes(data)
                        except Exception:
                            digest = None

                        if digest and digest == existing_doc.file_hash:
                            # 之前扫描过且文件内容未改变：增量跳过，不重复扫描
                            results["unchanged"] += 1
                            continue

                    # 文件发生变动（大小改变或内容哈希改变）：更新哈希并重新触发解析
                    try:
                        data = read_file_limited(resolved_file)
                        new_digest = sha256_bytes(data)
                        existing_doc.size_bytes = len(data)
                        existing_doc.file_hash = new_digest
                        existing_doc.parse_status = "QUEUED"
                        existing_doc.updated_at = datetime.now(timezone.utc)
                        db.commit()
                        enqueue_parse(db, existing_doc.id)
                        results["updated"] += 1
                    except Exception as exc:
                        logger.error("Failed to update changed local file %s: %s", filepath, exc)
                        db.rollback()
                        results["errors"] += 1
                else:
                    # 新增文件：入库并触发解析
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


def sync_all_mounts(db: Session) -> dict:
    """Scan all active mount points and sync documents incrementally."""
    mounts = db.execute(select(MountConfig).where(MountConfig.active.is_(True))).scalars().all()

    summary = {
        "scanned": 0,
        "added": 0,
        "updated": 0,
        "unchanged": 0,
        "errors": 0,
        "projects_created": 0,
    }

    for mount in mounts:
        single_res = sync_single_mount(db, mount)
        summary["scanned"] += single_res.get("scanned", 0)
        summary["added"] += single_res.get("added", 0)
        summary["updated"] += single_res.get("updated", 0)
        summary["unchanged"] += single_res.get("unchanged", 0)
        summary["errors"] += single_res.get("errors", 0)
        summary["projects_created"] += single_res.get("projects_created", 0)

    return summary
CATEGORY_KEYWORDS = (
    "合同", "发票", "磅单", "流水", "凭证", "报告", "立项", "招投标",
    "施工", "监理", "材料", "结算", "检测", "进度", "计量", "税", "图纸",
    "分包", "验收", "签证", "变更", "预算", "决算", "对账", "支付", "银行", "资料"
)


def _is_category_folder(name: str) -> bool:
    """Return True if a folder name represents a document category/stage rather than a separate project."""
    clean = name.strip()
    if not clean or clean.startswith("."):
        return True
    if re.match(r"^0\d_", clean) or re.match(r"^\d{1,2}_", clean):
        # E.g. "01_项目立项与招投标文件", "02_专业分包与施工合同"
        return any(kw in clean for kw in CATEGORY_KEYWORDS)
    if re.match(r"^DOC-", clean, re.IGNORECASE):
        return True
    if re.match(r"^\d{1,2}$", clean):  # e.g., "1", "2", "3"
        return True
    return any(kw in clean for kw in CATEGORY_KEYWORDS)


def _find_or_create_project(db: Session, project_raw_name: str) -> tuple[Project | None, bool]:
    """Find existing project by code/name or auto-create a clean project entity.
    
    Returns (project, is_created).
    """
    parts = project_raw_name.split("_")
    code_candidate = parts[-1].strip() if len(parts) > 1 else project_raw_name.strip()

    # 1. Match exact project code or name
    project = db.scalar(
        select(Project).where(
            (Project.project_code == project_raw_name)
            | (Project.name == project_raw_name)
            | (Project.project_code == code_candidate)
        )
    )
    if not project:
        all_projects = db.execute(select(Project)).scalars().all()
        for p in all_projects:
            if p.project_code and (p.project_code in project_raw_name or project_raw_name in p.project_code):
                project = p
                break

    if project:
        return project, False

    safe_slug = re.sub(r"[^A-Z0-9_-]+", "-", code_candidate.upper()).strip("-") or "PROJECT"
    suffix = hashlib.sha1(project_raw_name.encode("utf-8")).hexdigest()[:6].upper()
    if re.match(r"^[A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+$", code_candidate, re.IGNORECASE):
        final_code = code_candidate.upper()
    else:
        final_code = f"AUTO-{safe_slug[:40]}-{suffix}"

    # Clean display name: e.g. "01_天府国际金融中心二期_CD-TF-001" -> "天府国际金融中心二期"
    if len(parts) >= 3 and re.match(r"^\d+$", parts[0]):
        display_name = "_".join(parts[1:-1])
    else:
        display_name = project_raw_name

    project = Project(
        project_code=final_code,
        name=display_name,
        status="ACTIVE",
        contract_amount=Decimal("0.00"),
        location="未填写",
        note="由安全挂载扫描自动创建；contract_amount/location 待业务补录",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(project)
    try:
        db.commit()
        db.refresh(project)
        logger.info("Auto-created project: %s (code: %s)", project.name, project.project_code)
        return project, True
    except Exception:
        db.rollback()
        logger.exception("Failed to auto-create project %s", project_raw_name)
        return None, False


def _resolve_project_targets(root_path: Path) -> list[tuple[str, Path]]:
    """Determine whether root_path is a single project directory or a multi-project container."""
    try:
        subdirs = [
            item for item in root_path.iterdir()
            if item.is_dir() and not item.is_symlink() and not item.name.startswith(".")
        ]
        direct_files = [
            item for item in root_path.iterdir()
            if item.is_file() and not item.is_symlink() and not item.name.startswith(".")
            and item.suffix.lower() in SAFE_EXTS
        ]
    except (PermissionError, OSError) as exc:
        logger.warning("Cannot inspect directory %s: %s", root_path, exc)
        return []

    # If there are direct documents in root_path, root_path is definitely a single project
    if direct_files:
        return [(root_path.name, root_path)]

    if not subdirs:
        return [(root_path.name, root_path)]

    # Check if subdirs are category subfolders
    category_subdir_count = sum(1 for d in subdirs if _is_category_folder(d.name))
    
    # If all or most subdirs are category folders, root_path itself is ONE project
    if category_subdir_count > 0 and (category_subdir_count >= len(subdirs) / 2 or category_subdir_count >= 2):
        return [(root_path.name, root_path)]

    # Otherwise, each subdir is considered a separate project
    return [(d.name, d) for d in subdirs]


