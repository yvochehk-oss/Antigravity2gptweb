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


def _normalize_date(date_str: str | None) -> str | None:
    """将 YYYYMMDD 或 YYYY-MM-DD 统一转为 YYYYMMDD 并验证合法性。"""
    if not date_str:
        return None
    d = date_str.strip()
    # 去掉可能的横线
    d = d.replace("-", "").replace("/", "").replace(".", "")
    if len(d) == 8 and d.isdigit():
        try:
            datetime.strptime(d, "%Y%m%d")
            return d  # YYYYMMDD
        except ValueError:
            pass
    return None


def _location_initial(location: str) -> str:
    """取地点字符串的第一个字符（中文首字/英文首字母）。"""
    if not location:
        return "待"
    return location.strip()[0]


def generate_next_project_code(
    db: Session,
    folder_path: str = "",
    name: str = "",
    location: str = "",
    contract_date: str | None = None,
    first_payment_date: str | None = None,
) -> tuple[str, str]:
    """生成项目编号和建议名称。

    编号规则：地点首字母 + YYYYMMDD
    - 优先使用合同签订日期（YYYYMMDD）
    - 找不到合同日期则用第一笔付款日期
    - 均无则用地点首字母 + TBD（建档后由 update_project_code_and_dates 补充）

    名称规则：地点 + 项目名称（系统自动拼接，用户可自行修改）

    Returns (project_code, suggested_name).
    """
    name_from_path, code_from_path = extract_project_meta_from_path(folder_path)

    # 确定地点（优先用调用方传入的）
    loc = location.strip() if location else ""

    # 确定日期
    eff_date = _normalize_date(contract_date) or _normalize_date(first_payment_date)

    # 生成名称：地点 + 项目名称
    raw_name = name or name_from_path or ""
    suggested_name = (loc + raw_name).strip() if loc else raw_name

    # 生成编号
    initial = _location_initial(loc) if loc else "待"
    if eff_date:
        code_candidate = f"{initial}{eff_date}"
    else:
        code_candidate = f"{initial}TBD"

    # 检查编号是否被占用
    existing_codes: set[str] = set(
        str(c).strip().upper() for c in db.execute(select(Project.project_code)).scalars().all() if c
    )

    # 若目录里已有标准格式编号（如 CD-TF-001）且未占用，直接用
    if code_from_path and code_from_path.upper() not in existing_codes:
        return code_from_path.upper(), suggested_name

    # 编号去重：TBD 场景下允许后续更新；若 YYYYMMDD 冲突则加 -1 -2 ...
    base = code_candidate.upper()
    final_code = base
    suffix = 1
    while final_code.upper() in existing_codes:
        final_code = f"{base}-{suffix}"
        suffix += 1
    return final_code, suggested_name


def extract_project_meta_from_path(folder_path: str) -> tuple[str, str]:
    """Extract candidate project name and code from directory path.

    e.g.
    '01_天府国际金融中心二期_CD-TF-001' -> name: '天府国际金融中心二期', code: 'CD-TF-001'
    '03_广元利州产城融合与河道治理_GY-LZ-003' -> name: '广元利州产城融合与河道治理', code: 'GY-LZ-003'
    '06_宜宾示范工业项目_YB-DEMO-001' -> name: '宜宾示范工业项目', code: 'YB-DEMO-001'
    '新都区某商业综合体工程' -> name: '新都区某商业综合体工程', code: ''

    注意：返回的 name 仅用于构建最终项目名称（地点 + name），
    最终名称由 generate_next_project_code 负责拼接。
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
    location: str = "",
    contract_date: str | None = None,
    first_payment_date: str | None = None,
    auto_scan: bool = True,
) -> tuple[Project, list[dict]]:
    """Provision a project with mandatory directory binding, auto-code, mount, and initial scan.

    项目名称：系统按"地点 + 项目名称"格式自动拼接，用户可自行修改 name 参数。
    项目编号：地点首字母 + 合同签订日期（YYYYMMDD），找不到合同日期则用第一笔付款日期；
              均无则用地点首字母 + TBD，建档后由 update_project_code_and_dates 补充。
    """
    clean_path = str(directory_path or "").strip()
    if not clean_path:
        raise ValueError("必须指定项目资料归档目录（支持空目录）")

    target_dir = Path(clean_path).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    # 生成编号和建议名称（地点已在 generate_next_project_code 中拼入名称）
    auto_code, auto_name = generate_next_project_code(
        db, str(target_dir), name,
        location=location,
        contract_date=contract_date,
        first_payment_date=first_payment_date,
    )
    final_code = project_code or auto_code
    final_name = name or auto_name or target_dir.name or "未命名工程项目"

    # 若用户同时传了 name 和 location，名称应为两者拼接（防止重复）
    if location and name and not (auto_name == (location + name).strip()):
        final_name = (location + name).strip()

    # 确保 project_code 唯一
    if db.scalar(select(Project).where(Project.project_code == final_code)):
        conflict_code = final_code
        base_initial = _location_initial(location) if location else "待"
        suffix = 1
        while db.scalar(select(Project).where(Project.project_code == final_code)):
            final_code = f"{conflict_code}-{suffix}"
            suffix += 1
        logger.warning("project_code collision resolved: %s -> %s", conflict_code, final_code)

    now_utc = datetime.now(timezone.utc)
    project = Project(
        project_code=final_code,
        name=final_name,
        external_system=external_system or "construction-tax",
        external_project_id=external_project_id or final_code,
        status="ACTIVE",
        contract_amount=Decimal("0.00"),
        location=location,
        contract_date=contract_date,
        first_payment_date=first_payment_date,
        note=f"归档目录: {target_dir}",
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    # 注册 mount
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

    # 若建档时无日期，尝试从 canonical_facts 补充编号和日期
    if not contract_date and not first_payment_date:
        try:
            from .canonical_facts import update_project_code_and_dates

            updated_code = update_project_code_and_dates(db, project.id)
            if updated_code and updated_code != project.project_code:
                logger.info("Project %s code updated from canonical_facts: %s", project.id, updated_code)
                db.refresh(project)
        except Exception as exc:
            logger.warning("Failed to update project code from canonical_facts: %s", exc)

    return project, scan_results
