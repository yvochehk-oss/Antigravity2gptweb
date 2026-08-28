"""Move document path metadata into the self-contained V2.0 bundle.

The PostgreSQL database was copied from a pre-V2 checkout, so its document
path columns still point at the old sibling directory.  This revision builds
each path from the relational project/document identifiers and the filesystem
roots owned by this V2.0 checkout.  It deliberately does not use a runtime
string replacement: every target file is preflighted for containment,
uniqueness, size, and SHA-256 before any row is updated.

The old and new values are retained in a short-lived rollback table.  A
downgrade restores only rows that still contain the exact values written by
this migration, so a later concurrent edit cannot be silently overwritten.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

revision = "012_document_storage_paths"
down_revision = "011_entity_mapping_facts_gate"
branch_labels = None
depends_on = None

_ROLLBACK_TABLE = "document_path_migrations_012"
_MAX_PATH_LENGTH = 600


def _v2_root() -> Path:
    # .../V2.0/source_code/0.2_RAG系统/project-rag-v1.1/
    return Path(__file__).resolve().parents[5]


def _approved_root(env_name: str, default: Path) -> Path:
    root = Path(os.environ.get(env_name, str(default))).expanduser().resolve()
    try:
        root.relative_to(_v2_root())
    except ValueError as exc:
        raise RuntimeError(
            f"{env_name} must remain under the approved V2.0 root: {root}"
        ) from exc
    if not root.is_dir():
        raise RuntimeError(f"{env_name} does not exist as a directory: {root}")
    return root


def _has_symlink_component(path: Path) -> bool:
    """Reject symlinks in a path used as a trusted stored-file target."""

    absolute = path if path.is_absolute() else Path.cwd() / path
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False


def _safe_component(value: str, label: str) -> str:
    value = value.strip()
    if not value or value in {".", ".."} or Path(value).name != value:
        raise RuntimeError(f"unsafe {label} component in documents metadata: {value!r}")
    return value


def _safe_target(path: Path, root: Path, *, directory: bool = False) -> Path:
    if _has_symlink_component(path):
        raise RuntimeError(f"document storage target contains a symlink: {path}")
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError(f"document storage target is outside V2.0 root: {path}") from exc
    if directory:
        if not resolved.is_dir():
            raise RuntimeError(f"document parsed directory is missing: {resolved}")
    elif not resolved.is_file():
        raise RuntimeError(f"document stored file is missing: {resolved}")
    if len(str(resolved)) > _MAX_PATH_LENGTH:
        raise RuntimeError(f"document path exceeds schema limit {_MAX_PATH_LENGTH}: {resolved}")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _plan_paths(connection) -> list[dict[str, str | int]]:
    v2_root = _v2_root().resolve()
    materials_root = _approved_root(
        "PROJECT_RAG_PROJECT_MATERIALS_ROOT", v2_root / "project_materials"
    )
    parsed_root = _approved_root(
        "PROJECT_RAG_PARSED_ROOT",
        v2_root / "source_code" / "0.2_RAG系统" / "project-rag-v1.1" / "data" / "parsed",
    )

    rows = connection.execute(
        text(
            """
            SELECT d.id, p.code AS project_code, d.document_code, d.filename,
                   d.original_path, d.parsed_dir, d.markdown_path,
                   d.content_list_path, d.file_hash, d.size_bytes
            FROM public.documents AS d
            JOIN public.projects AS p ON p.id = d.project_id
            ORDER BY d.id
            FOR UPDATE OF d
            """
        )
    ).mappings().all()
    if not rows:
        return []

    plan: list[dict[str, str | int]] = []
    for row in rows:
        project_code = _safe_component(str(row["project_code"]), "project code")
        document_code = _safe_component(str(row["document_code"]), "document code")
        filename = str(row["filename"])
        safe_filename = _safe_component(filename, "filename")

        original = _safe_target(
            materials_root / project_code / document_code / safe_filename,
            v2_root,
        )
        parsed_dir = _safe_target(parsed_root / document_code, v2_root, directory=True)
        markdown = _safe_target(
            parsed_dir / (Path(safe_filename).with_suffix(".md").name), v2_root
        )
        content_list = _safe_target(
            parsed_dir / f"{Path(safe_filename).stem}_content_list.json", v2_root
        )

        expected_size = int(row["size_bytes"])
        actual_size = original.stat().st_size
        if actual_size != expected_size:
            raise RuntimeError(
                f"document {row['id']} size mismatch: DB={expected_size}, file={actual_size}"
            )
        expected_hash = str(row["file_hash"]).lower()
        actual_hash = _sha256(original)
        if actual_hash != expected_hash:
            raise RuntimeError(
                f"document {row['id']} SHA-256 mismatch: DB={expected_hash}, file={actual_hash}"
            )

        plan.append(
            {
                "document_id": int(row["id"]),
                "old_original_path": str(row["original_path"]),
                "old_parsed_dir": str(row["parsed_dir"]),
                "old_markdown_path": str(row["markdown_path"]),
                "old_content_list_path": str(row["content_list_path"]),
                "new_original_path": str(original),
                "new_parsed_dir": str(parsed_dir),
                "new_markdown_path": str(markdown),
                "new_content_list_path": str(content_list),
            }
        )
    return plan


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        raise RuntimeError("document storage path migration is PostgreSQL-only")

    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '60s'"))
    plan = _plan_paths(connection)

    op.create_table(
        _ROLLBACK_TABLE,
        sa.Column("document_id", sa.Integer(), primary_key=True),
        sa.Column("old_original_path", sa.Text(), nullable=False),
        sa.Column("old_parsed_dir", sa.Text(), nullable=False),
        sa.Column("old_markdown_path", sa.Text(), nullable=False),
        sa.Column("old_content_list_path", sa.Text(), nullable=False),
        sa.Column("new_original_path", sa.Text(), nullable=False),
        sa.Column("new_parsed_dir", sa.Text(), nullable=False),
        sa.Column("new_markdown_path", sa.Text(), nullable=False),
        sa.Column("new_content_list_path", sa.Text(), nullable=False),
        sa.Column(
            "migrated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
    )
    if plan:
        connection.execute(
            text(
            f"""
            INSERT INTO {_ROLLBACK_TABLE} (
                document_id, old_original_path, old_parsed_dir,
                old_markdown_path, old_content_list_path,
                new_original_path, new_parsed_dir,
                new_markdown_path, new_content_list_path
            ) VALUES (
                :document_id, :old_original_path, :old_parsed_dir,
                :old_markdown_path, :old_content_list_path,
                :new_original_path, :new_parsed_dir,
                :new_markdown_path, :new_content_list_path
            )
            """
        ),
        plan,
    )

    updated = 0
    for item in plan:
        result = connection.execute(
            text(
                """
                UPDATE public.documents
                SET original_path = :new_original_path,
                    parsed_dir = :new_parsed_dir,
                    markdown_path = :new_markdown_path,
                    content_list_path = :new_content_list_path
                WHERE id = :document_id
                  AND original_path = :old_original_path
                  AND parsed_dir = :old_parsed_dir
                  AND markdown_path = :old_markdown_path
                  AND content_list_path = :old_content_list_path
                RETURNING id
                """
            ),
            item,
        )
        if result.first() is None:
            raise RuntimeError(
                f"document {item['document_id']} changed during path migration; transaction aborted"
            )
        updated += 1
    if updated != len(plan):
        raise RuntimeError(f"document path update count mismatch: {updated} != {len(plan)}")


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        raise RuntimeError("document storage path migration is PostgreSQL-only")

    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '60s'"))
    rows = connection.execute(
        text(
            f"""
            SELECT d.id, d.original_path, d.parsed_dir, d.markdown_path,
                   d.content_list_path, m.new_original_path, m.new_parsed_dir,
                   m.new_markdown_path, m.new_content_list_path,
                   m.old_original_path, m.old_parsed_dir,
                   m.old_markdown_path, m.old_content_list_path
            FROM {_ROLLBACK_TABLE} AS m
            JOIN public.documents AS d ON d.id = m.document_id
            ORDER BY d.id
            FOR UPDATE OF d
            """
        )
    ).mappings().all()
    if not rows:
        raise RuntimeError("document path rollback has no recorded rows")

    restored = 0
    for row in rows:
        current = (
            row["original_path"],
            row["parsed_dir"],
            row["markdown_path"],
            row["content_list_path"],
        )
        expected = (
            row["new_original_path"],
            row["new_parsed_dir"],
            row["new_markdown_path"],
            row["new_content_list_path"],
        )
        if current != expected:
            raise RuntimeError(
                f"document {row['id']} changed after migration; refusing rollback"
            )
        result = connection.execute(
            text(
                """
                UPDATE public.documents
                SET original_path = :old_original_path,
                    parsed_dir = :old_parsed_dir,
                    markdown_path = :old_markdown_path,
                    content_list_path = :old_content_list_path
                WHERE id = :id
                  AND original_path = :new_original_path
                  AND parsed_dir = :new_parsed_dir
                  AND markdown_path = :new_markdown_path
                  AND content_list_path = :new_content_list_path
                RETURNING id
                """
            ),
            row,
        )
        if result.first() is None:
            raise RuntimeError(f"document {row['id']} rollback update failed")
        restored += 1
    if restored != len(rows):
        raise RuntimeError(f"document path rollback count mismatch: {restored} != {len(rows)}")
    op.drop_table(_ROLLBACK_TABLE)
