"""Project provisioning and standard code generation service."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models import MountConfig, Project
from .documents import scan_folder

logger = get_logger(__name__)


def extract_project_meta_from_path(folder_path: str) -> tuple[str, str]:
    """Extract candidate project name and code from directory path.

    e.g.
    '01_天府国际金融中心二期_CD-TF-001' -> name: '天府国际金融中心二期', code: 'CD-TF-001'
    '03_广元利州产城融合与河道治理_GY-LZ-003' -> name: '广元利州产城融合与河道治理', code: 'GY-LZ-003'
    '06_宜宾示范工业项目_YB-DEMO-001' -> name: '宜宾示范工业项目', code: 'YB-DEMO-001'
    '新都区某商业综合体工程' -> name: '新都区某商业综合体工程', code: ''
    """
    if not folder_path:
        return "", ""
    folder_name = Path(folder_path.strip()).name.strip()
    if not folder_name:
        return "", ""

    code_cand = ""
    name_cand = folder_name

    # 1. Search for standard 3-part or 2-part code in folder_name
    m_code = re.search(r"([A-Za-z0-9]+(?:-[A-Za-z0-9]+)+-\d+)", folder_name)
    if not m_code:
        m_code = re.search(r"([A-Za-z0-9]+-\d+)", folder_name)
    if m_code:
        code_cand = m_code.group(1).upper()

    # 2. Extract clean name if folder has format '01_Name_CODE' or '01_Name'
    parts = [p.strip() for p in folder_name.split("_") if p.strip()]
    if len(parts) >= 3 and re.match(r"^\d+$", parts[0]):
        name_cand = parts[1]
    elif len(parts) == 2:
        if re.match(r"^\d+$", parts[0]):
            name_cand = parts[1]
        elif code_cand and parts[1].upper() == code_cand:
            name_cand = parts[0]

    return name_cand, code_cand


def generate_next_project_code(db: Session, folder_path: str = "", name: str = "") -> tuple[str, str]:
    """Generate the next authoritative project code and suggested name.

    Returns (project_code, suggested_name).
    """
    name_from_path, code_from_path = extract_project_meta_from_path(folder_path)
    suggested_name = name or name_from_path

    # Check all existing project codes
    existing_rows = db.execute(select(Project.project_code)).scalars().all()
    existing_codes = {str(c).strip().upper() for c in existing_rows if c}

    # If code from path is valid, standard, and not taken, use it
    if code_from_path and code_from_path not in existing_codes:
        return code_from_path, suggested_name

    # Otherwise calculate max numerical sequence across all codes
    seqs = []
    for c in existing_codes:
        m = re.search(r"(\d+)$", str(c))
        if m:
            try:
                seqs.append(int(m.group(1)))
            except ValueError:
                pass

    next_seq = (max(seqs) if seqs else 0) + 1
    while True:
        code_candidate = f"CD-JG-{next_seq:03d}"
        if code_candidate not in existing_codes:
            return code_candidate, suggested_name
        next_seq += 1


def provision_project_with_directory(
    db: Session,
    directory_path: str,
    name: str = "",
    project_code: str = "",
    external_system: str = "construction-tax",
    external_project_id: str = "",
    auto_scan: bool = True,
) -> tuple[Project, list[dict]]:
    """Provision a project with mandatory directory binding, auto-code, mount, and initial scan."""
    clean_path = str(directory_path or "").strip()
    if not clean_path:
        raise ValueError("必须指定项目资料归档目录（支持空目录）")

    target_dir = Path(clean_path).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    auto_code, auto_name = generate_next_project_code(db, str(target_dir), name)
    final_code = project_code or auto_code
    final_name = name or auto_name or target_dir.name or "未命名工程项目"

    # Ensure project_code uniqueness
    if db.scalar(select(Project).where(Project.project_code == final_code)):
        final_code, _ = generate_next_project_code(db, "", final_name)

    now_utc = datetime.now(timezone.utc)
    project = Project(
        project_code=final_code,
        name=final_name,
        external_system=external_system or "construction-tax",
        external_project_id=external_project_id or final_code,
        status="ACTIVE",
        contract_amount=Decimal("0.00"),
        location="成都市",
        note=f"归档目录: {target_dir}",
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    # Register mount
    try:
        m_path = str(target_dir)
        existing_mount = db.scalar(select(MountConfig).where(MountConfig.path == m_path))
        if not existing_mount:
            db.add(MountConfig(path=m_path, active=True, created_at=now_utc))
            db.commit()
    except Exception:
        db.rollback()

    scan_results = []
    if auto_scan:
        try:
            scan_results = scan_folder(db, project, target_dir, recursive=True, auto_parse=True)
        except Exception as exc:
            logger.warning("Initial scan on %s encountered issue: %s", target_dir, exc)

    return project, scan_results
