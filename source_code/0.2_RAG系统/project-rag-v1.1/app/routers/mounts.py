from fastapi.templating import Jinja2Templates
from pathlib import Path

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models import MountConfig

from ..services.scanner import sync_all_mounts
from ..logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/mounts", response_class=HTMLResponse)
def list_mounts(request: Request, db: Session = Depends(get_db)):
    mounts = db.execute(select(MountConfig).order_by(MountConfig.id)).scalars().all()
    return templates.TemplateResponse(
        request, 
        "mounts.html", 
        {"request": request, "mounts": mounts, "active_page": "mounts"}
    )

@router.post("/api/v1/mounts")
def add_mount(path: str = Form(...), db: Session = Depends(get_db)):
    if not path.strip():
        raise HTTPException(400, "Path cannot be empty")
        
    exists = db.scalar(select(MountConfig).where(MountConfig.path == path.strip()))
    if exists:
        raise HTTPException(400, "Mount path already exists")
        
    mount = MountConfig(path=path.strip(), active=True)
    db.add(mount)
    db.commit()
    
    return RedirectResponse(url="/mounts", status_code=303)

@router.post("/api/v1/mounts/{mount_id}/delete")
def delete_mount(mount_id: int, db: Session = Depends(get_db)):
    mount = db.get(MountConfig, mount_id)
    if mount:
        db.delete(mount)
        db.commit()
    return RedirectResponse(url="/mounts", status_code=303)

@router.post("/api/v1/mounts/scan")
def trigger_scan(db: Session = Depends(get_db)):
    results = sync_all_mounts(db)
    return results

import os
from pathlib import Path as _Path

# Common macOS/Linux quick-access paths
_QUICK_ACCESS = [
    ("🏠 主目录 (Home)", str(_Path.home())),
    ("🖥️ 桌面 (Desktop)", str(_Path.home() / "Desktop")),
    ("📄 文稿 (Documents)", str(_Path.home() / "Documents")),
    ("💾 下载 (Downloads)", str(_Path.home() / "Downloads")),
    ("💿 外置磁盘 (/Volumes)", "/Volumes"),
    ("📂 AI开发", str(_Path.home() / "AI开发")),
]

@router.get("/api/v1/mounts/fs")
def browse_fs(path: str = ""):
    # Default to home directory, not root
    if not path:
        path = str(_Path.home())

    if not os.path.exists(path):
        path = "/" if os.name != "nt" else "C:\\"
        
    try:
        items = []
        # Add parent directory if not at root
        parent = os.path.dirname(path)
        if parent != path:
            items.append({"name": "..", "path": parent, "is_dir": True})

        try:
            entries = list(os.scandir(path))
        except PermissionError:
            entries = []

        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=True)
            except OSError:
                continue
            if is_dir and not entry.name.startswith("."):
                items.append({
                    "name": entry.name,
                    "path": os.path.abspath(entry.path),
                    "is_dir": True
                })

        items.sort(key=lambda x: (x["name"] != "..", x["name"].lower()))

        # Build quick_access shortcuts (only existing paths)
        quick = []
        for label, p in _QUICK_ACCESS:
            if os.path.isdir(p):
                quick.append({"label": label, "path": p})

        return {"current": path, "items": items, "quick_access": quick}
    except Exception as e:
        return {"current": path, "items": [], "quick_access": [], "error": str(e)}
