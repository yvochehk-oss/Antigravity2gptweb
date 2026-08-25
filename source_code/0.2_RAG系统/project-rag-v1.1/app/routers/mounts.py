import os
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import (
    require_web_auth,
    require_web_or_service_read,
    require_web_or_service_role,
)
from ..config import (
    DATA_DIR,
    IMPORT_ROOT,
    PROJECT_ARCHIVES_DIR,
    PROJECT_MATERIALS_DIR,
    SAFE_ORIGIN_DIRS,
    V2_ROOT,
)
from ..db import SessionLocal
from ..logging_config import get_logger
from ..models import MountConfig
from ..services.scanner import sync_all_mounts
from ..services.storage import (
    PathTraversalError,
    StorageError,
    validate_safe_directory,
)

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
logger = get_logger(__name__)
router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _safe_mount_directory(raw_path: str) -> Path:
    """Return an existing directory under an explicitly trusted root."""
    try:
        return validate_safe_directory(raw_path)
    except (StorageError, PathTraversalError) as exc:
        raise HTTPException(400, f"挂载目录不在允许的导入根目录内: {exc}") from exc


def _safe_browse_directory(raw_path: str | None) -> Path:
    """Resolve a browser path without ever falling back to filesystem root."""
    if not raw_path:
        for root in SAFE_ORIGIN_DIRS:
            try:
                return validate_safe_directory(root)
            except (StorageError, PathTraversalError):
                continue
        raise HTTPException(400, "没有可用的安全导入根目录")
    return _safe_mount_directory(raw_path)

@router.get("/mounts", response_class=HTMLResponse)
def list_mounts(
    request: Request,
    principal=Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    mounts = db.execute(select(MountConfig).order_by(MountConfig.id)).scalars().all()
    return templates.TemplateResponse(
        request,
        "mounts.html",
        {"request": request, "mounts": mounts, "active_page": "mounts"}
    )

@router.post("/api/v1/mounts")
def add_mount(
    path: str = Form(...),
    principal=Depends(require_web_or_service_role("admin")),
    db: Session = Depends(get_db),
):
    if not path.strip():
        raise HTTPException(400, "Path cannot be empty")

    safe_path = _safe_mount_directory(path.strip())
    normalized = str(safe_path)
    exists = db.scalar(select(MountConfig).where(MountConfig.path == normalized))
    if exists:
        raise HTTPException(400, "Mount path already exists")

    mount = MountConfig(path=normalized, active=True)
    db.add(mount)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to create mount %s", normalized)
        raise HTTPException(500, "挂载目录保存失败，事务已回滚") from exc

    return RedirectResponse(url="/mounts", status_code=303)

@router.post("/api/v1/mounts/{mount_id}/delete")
def delete_mount(
    mount_id: int,
    principal=Depends(require_web_or_service_role("admin")),
    db: Session = Depends(get_db),
):
    mount = db.get(MountConfig, mount_id)
    if mount:
        db.delete(mount)
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.exception("Failed to delete mount %s", mount_id)
            raise HTTPException(500, "挂载目录删除失败，事务已回滚") from exc
    return RedirectResponse(url="/mounts", status_code=303)

@router.post("/api/v1/mounts/scan")
def trigger_scan(
    principal=Depends(require_web_or_service_role("admin", "operator")),
    db: Session = Depends(get_db),
):
    results = sync_all_mounts(db)
    return results

@router.get("/api/v1/mounts/fs")
def browse_fs(path: str = "", principal=Depends(require_web_or_service_read)):
    current_path = _safe_browse_directory(path)
    try:
        items = []
        # Add parent directory if not at root
        parent = current_path.parent
        if parent != current_path:
            try:
                parent = _safe_mount_directory(str(parent))
            except HTTPException:
                parent = None
            if parent is not None:
                items.append({"name": "..", "path": str(parent), "is_dir": True})

        try:
            entries = list(os.scandir(current_path))
        except PermissionError:
            entries = []

        for entry in entries:
            try:
                # Symlinked directories are intentionally invisible to the
                # browser; accepting one here would let a later mount escape
                # the configured import roots.
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir and not entry.name.startswith(".") and not entry.is_symlink():
                items.append({
                    "name": entry.name,
                    "path": str(Path(entry.path).absolute()),
                    "is_dir": True
                })

        items.sort(key=lambda x: (x["name"] != "..", x["name"].lower()))

        # Build quick_access shortcuts (only existing paths)
        quick = []
        labels_map = {
            str(PROJECT_ARCHIVES_DIR): "📁 项目存档资料 (6大工程)",
            str(PROJECT_MATERIALS_DIR): "📁 项目材料 (project_materials)",
            str(V2_ROOT.resolve()): "📁 V2.0 根目录",
            str(IMPORT_ROOT): "📁 导入目录 (imports)",
            str(DATA_DIR.resolve()): "📁 系统数据 (data)",
        }
        for root in SAFE_ORIGIN_DIRS:
            try:
                safe_root = validate_safe_directory(root)
            except (StorageError, PathTraversalError):
                continue
            lbl = labels_map.get(str(safe_root), f"📁 {safe_root.name or safe_root}")
            quick.append({"label": lbl, "path": str(safe_root)})

        return {"current": str(current_path), "items": items, "quick_access": quick}
    except Exception as e:
        # Do not reveal arbitrary filesystem errors or path contents to the
        # browser.  A validated path can still disappear between checks.
        logger.warning("Failed to browse safe mount directory %s: %s", current_path, e)
        return {"current": str(current_path), "items": [], "quick_access": [], "error": "无法读取目录"}
