"""Align Tax/RAG shared project master data.

Revision ID: 100_project_master_alignment
Revises: 99_phase4_accounting_snapshots
"""
from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision = "100_project_master_alignment"
down_revision = "99_phase4_accounting_snapshots"
branch_labels = None
depends_on = None

LEGACY_GUANGYUAN_CODE = "CY-LZ-003"
CANONICAL_GUANGYUAN_CODE = "GY-LZ-003"

CANONICAL_PROJECTS = (
    {
        "code": "CD-TF-001",
        "name": "成都天府国际金融中心二期大厦工程",
        "city": "成都市",
        "contract_total": Decimal("1450000000"),
        "tax_method": "general",
    },
    {
        "code": "CY-CQ-002",
        "name": "成渝双城经济圈跨江特大桥及连接线工程",
        "city": "重庆市",
        "contract_total": Decimal("880000000"),
        "tax_method": "general",
    },
    {
        "code": "GY-LZ-003",
        "name": "广元利州产城融合与生态河道综合治理工程",
        "city": "广元市",
        "contract_total": Decimal("360000000"),
        "tax_method": "general",
    },
)

PROJECT_TRIGGER = r"""
CREATE OR REPLACE FUNCTION sync_project_aliases() RETURNS trigger AS $$
BEGIN
  IF NEW.code = 'CY-LZ-003' OR NEW.project_code = 'CY-LZ-003' THEN
    NEW.code := 'GY-LZ-003';
    NEW.project_code := 'GY-LZ-003';
  END IF;

  IF NEW.code IS NOT NULL AND NEW.project_code IS NOT NULL AND NEW.code <> NEW.project_code THEN
    RAISE EXCEPTION 'projects code/project_code conflict';
  END IF;
  NEW.code := COALESCE(NEW.code, NEW.project_code);
  NEW.project_code := COALESCE(NEW.project_code, NEW.code);

  IF NEW.contract_total IS NOT NULL AND NEW.contract_amount IS NOT NULL AND NEW.contract_total <> NEW.contract_amount THEN
    RAISE EXCEPTION 'projects contract amount conflict';
  END IF;
  NEW.contract_total := COALESCE(NEW.contract_total, NEW.contract_amount);
  NEW.contract_amount := COALESCE(NEW.contract_amount, NEW.contract_total);

  IF COALESCE(NEW.city, '') <> '' AND COALESCE(NEW.location, '') <> '' AND NEW.city <> NEW.location THEN
    RAISE EXCEPTION 'projects city/location conflict';
  END IF;
  NEW.city := COALESCE(NULLIF(NEW.city, ''), NEW.location, '');
  NEW.location := COALESCE(NULLIF(NEW.location, ''), NEW.city, '');
  NEW.tax_method := COALESCE(NULLIF(NEW.tax_method, ''), 'general');

  IF NEW.status IS NULL OR btrim(NEW.status) = '' OR lower(btrim(NEW.status)) IN ('unknown', '未知') THEN
    NEW.status := 'ACTIVE';
  ELSIF lower(btrim(NEW.status)) = 'active' THEN
    NEW.status := 'ACTIVE';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sync_project_aliases ON projects;
CREATE TRIGGER trg_sync_project_aliases
BEFORE INSERT OR UPDATE ON projects
FOR EACH ROW EXECUTE FUNCTION sync_project_aliases();
"""


def _project_columns(bind) -> set[str]:
    return {
        str(column["name"])
        for column in sa.inspect(bind).get_columns("projects")
    }


def _ensure_status_contract(bind) -> None:
    columns = _project_columns(bind)
    if "status" not in columns:
        op.add_column(
            "projects",
            sa.Column(
                "status",
                sa.String(24),
                nullable=True,
                server_default=sa.text("'ACTIVE'"),
            ),
        )
    else:
        op.execute("ALTER TABLE projects ALTER COLUMN status SET DEFAULT 'ACTIVE'")

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("projects")}
    if "ix_projects_status" not in indexes:
        op.create_index("ix_projects_status", "projects", ["status"], unique=False)


def _guard_guangyuan_collision(bind) -> int | None:
    rows = bind.execute(
        sa.text(
            """
            SELECT id, code, project_code, name
            FROM projects
            WHERE code IN (:legacy, :canonical)
               OR project_code IN (:legacy, :canonical)
            ORDER BY id
            """
        ),
        {
            "legacy": LEGACY_GUANGYUAN_CODE,
            "canonical": CANONICAL_GUANGYUAN_CODE,
        },
    ).mappings().all()

    project_ids = {int(row["id"]) for row in rows}
    if len(project_ids) > 1:
        details = ", ".join(
            f"id={row['id']} code={row['code']!r} project_code={row['project_code']!r}"
            for row in rows
        )
        raise RuntimeError(
            "cannot safely canonicalize Guangyuan project: legacy/canonical codes "
            f"resolve to multiple project rows ({details}). Reconcile foreign-key "
            "relationships manually before retrying this migration."
        )
    return next(iter(project_ids), None)


def _align_guangyuan_code(bind) -> None:
    project_id = _guard_guangyuan_collision(bind)
    if project_id is None:
        return

    bind.execute(
        sa.text(
            """
            UPDATE projects
            SET code = :canonical,
                project_code = :canonical
            WHERE id = :project_id
            """
        ),
        {
            "canonical": CANONICAL_GUANGYUAN_CODE,
            "project_id": project_id,
        },
    )


def _insert_missing_project(bind, project: dict[str, object]) -> None:
    project_id = bind.execute(
        sa.text(
            """
            SELECT id
            FROM projects
            WHERE code = :code OR project_code = :code
            """
        ),
        {"code": project["code"]},
    ).scalar_one_or_none()
    if project_id is not None:
        return

    columns = _project_columns(bind)
    candidate_values = {
        "code": project["code"],
        "project_code": project["code"],
        "name": project["name"],
        "city": project["city"],
        "location": project["city"],
        "contract_total": project["contract_total"],
        "contract_amount": project["contract_total"],
        "tax_method": project["tax_method"],
        "status": "ACTIVE",
        "external_system": "construction-tax",
        "external_project_id": project["code"],
        "start_date": "",
        "expected_end_date": "",
        "project_type": "",
        "note": "",
    }
    values = {
        name: value
        for name, value in candidate_values.items()
        if name in columns
    }
    column_sql = ", ".join(values)
    value_sql = ", ".join(f":{name}" for name in values)
    bind.execute(
        sa.text(f"INSERT INTO projects ({column_sql}) VALUES ({value_sql})"),
        values,
    )


def _repair_canonical_statuses(bind) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE projects
            SET status = 'ACTIVE'
            WHERE (
                code IN ('CD-TF-001', 'CY-CQ-002', 'GY-LZ-003')
                OR project_code IN ('CD-TF-001', 'CY-CQ-002', 'GY-LZ-003')
            )
            AND (
                status IS NULL
                OR btrim(status) = ''
                OR lower(btrim(status)) IN ('unknown', '未知', 'active')
            )
            """
        )
    )


def _assert_converged(bind) -> None:
    rows = bind.execute(
        sa.text(
            """
            SELECT id, code, project_code, status
            FROM projects
            WHERE code IN (
                'CD-TF-001', 'CY-CQ-002', 'GY-LZ-003', 'CY-LZ-003'
            )
            OR project_code IN (
                'CD-TF-001', 'CY-CQ-002', 'GY-LZ-003', 'CY-LZ-003'
            )
            """
        )
    ).mappings().all()

    by_code = {
        row["code"]: row
        for row in rows
        if row["code"] in {"CD-TF-001", "CY-CQ-002", "GY-LZ-003"}
    }
    missing = sorted({"CD-TF-001", "CY-CQ-002", "GY-LZ-003"} - set(by_code))
    if missing:
        raise RuntimeError(
            "shared project master convergence incomplete; missing canonical projects: "
            + ", ".join(missing)
        )

    legacy_rows = [
        row
        for row in rows
        if row["code"] == LEGACY_GUANGYUAN_CODE
        or row["project_code"] == LEGACY_GUANGYUAN_CODE
    ]
    if legacy_rows:
        raise RuntimeError(
            "shared project master convergence incomplete; legacy code CY-LZ-003 remains"
        )

    bad_status = [
        row["code"]
        for row in by_code.values()
        if row["status"] is None
        or not str(row["status"]).strip()
        or str(row["status"]).strip().lower() in {"unknown", "未知"}
    ]
    if bad_status:
        raise RuntimeError(
            "shared project master convergence incomplete; invalid status for: "
            + ", ".join(sorted(bad_status))
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")

    _ensure_status_contract(bind)
    op.execute(PROJECT_TRIGGER)
    _align_guangyuan_code(bind)

    for project in CANONICAL_PROJECTS:
        _insert_missing_project(bind, project)

    _repair_canonical_statuses(bind)
    _assert_converged(bind)


def downgrade() -> None:
    raise RuntimeError(
        "shared project master alignment is intentionally irreversible"
    )
