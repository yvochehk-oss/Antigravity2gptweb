"""Backup and restore services for PostgreSQL and file system.

Provides backup, restore, verify and cleanup operations for disaster recovery.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import (
    BACKUP_DIR,
    BACKUP_KEEP_DAYS,
    DB_URL,
    ORIGINAL_DIR,
    PARSED_DIR,
)
from ..logging_config import get_logger
from ..models import BackupRecord as DBBackupRecord

logger = get_logger(__name__)


# ----------------------------------------------------------------------
# Dataclass
# ----------------------------------------------------------------------

@dataclass
class BackupRecord:
    """In-memory backup record returned by backup operations."""
    backup_type: str       # postgres / files
    tier: str              # local / s3
    path: str
    size_bytes: int
    status: str            # created / failed / verified
    error: str = ""
    metadata: dict = field(default_factory=dict)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_db_url(db_url: str) -> dict[str, str]:
    """Parse a PostgreSQL connection URL into components.

    Supports URLs of the form:
        postgresql+psycopg://user:pass@host:port/dbname
        postgresql://user:pass@host:port/dbname
    """
    pattern = r"(?:postgresql(?:[+]psycopg)?://)?(?P<user>[^:@]+):(?P<password>[^@]+)@(?P<host>[^:]+):(?P<port>\d+)/(?P<dbname>.+)"
    m = re.match(pattern, db_url)
    if not m:
        raise ValueError(f"Cannot parse DB_URL: {db_url}")
    return m.groupdict()


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _db_backup_record_to_dict(record: DBBackupRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "backup_type": record.backup_type,
        "tier": record.tier,
        "path": record.path,
        "size_bytes": record.size_bytes,
        "status": record.status,
        "error": record.error,
        "metadata": _try_json.loads(record.metadata_json),
        "created_at": record.created_at,
    }


# ----------------------------------------------------------------------
# pg_dump / pg_restore wrappers
# ----------------------------------------------------------------------

def _run_pg_command(
    cmd: list[str],
    env: dict[str, str] | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    """Run a PostgreSQL CLI command with optional env overlay."""
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        env=merged,
    )


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def backup_postgres(
    output_dir: str | Path | None = None,
    db_url: str | None = None,
) -> BackupRecord:
    """Backup the PostgreSQL database using pg_dump.

    Args:
        output_dir: Directory to write the dump file.
                    Defaults to BACKUP_DIR.
        db_url:     Database URL. Defaults to config.DB_URL.

    Returns:
        BackupRecord describing the result.
    """
    output_dir = Path(output_dir) if output_dir else BACKUP_DIR
    _ensure_dir(output_dir)

    url = db_url or DB_URL
    try:
        parsed = _parse_db_url(url)
    except ValueError as e:
        return BackupRecord(
            backup_type="postgres",
            tier="local",
            path="",
            size_bytes=0,
            status="failed",
            error=str(e),
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dump_path = output_dir / f"postgres_{timestamp}.dump"

    env = {
        "PGPASSWORD": parsed["password"],
    }

    cmd = [
        "pg_dump",
        "--no-owner",
        "--format=custom",
        f"--host={parsed['host']}",
        f"--port={parsed['port']}",
        f"--username={parsed['user']}",
        f"--dbname={parsed['dbname']}",
        "--file=" + str(dump_path),
    ]

    result = _run_pg_command(cmd, env=env)

    if result.returncode != 0:
        err = result.stderr.strip() or "pg_dump failed with no error message"
        logger.error(f"pg_dump failed: {err}")
        return BackupRecord(
            backup_type="postgres",
            tier="local",
            path=str(dump_path),
            size_bytes=0,
            status="failed",
            error=err,
        )

    size = dump_path.stat().st_size if dump_path.exists() else 0
    logger.info(f"Postgres backup saved to {dump_path} ({size} bytes)")

    return BackupRecord(
        backup_type="postgres",
        tier="local",
        path=str(dump_path),
        size_bytes=size,
        status="created",
        metadata={"timestamp": timestamp, "db_url_host": parsed["host"]},
    )


def backup_files(
    src_dirs: list[str] | None = None,
    output_dir: str | Path | None = None,
) -> BackupRecord:
    """Backup file directories into a compressed tar.gz archive.

    Args:
        src_dirs:   Source directories to include.
                    Defaults to [ORIGINAL_DIR, PARSED_DIR].
        output_dir: Directory to write the archive.
                    Defaults to BACKUP_DIR.

    Returns:
        BackupRecord describing the result.
    """
    output_dir = Path(output_dir) if output_dir else BACKUP_DIR
    _ensure_dir(output_dir)

    if src_dirs is None:
        src_dirs = [str(ORIGINAL_DIR), str(PARSED_DIR)]

    # Filter to existing directories
    existing = [d for d in src_dirs if Path(d).exists()]
    if not existing:
        return BackupRecord(
            backup_type="files",
            tier="local",
            path="",
            size_bytes=0,
            status="failed",
            error=f"None of the source directories exist: {src_dirs}",
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_path = output_dir / f"files_{timestamp}.tar.gz"

    try:
        with tarfile.open(archive_path, "w:gz") as tf:
            for src in existing:
                src_path = Path(src)
                # Store each directory under its basename so the archive
                # contains e.g.  originals/... and parsed/...
                arcname = src_path.name
                tf.add(src_path, arcname=arcname)

        size = archive_path.stat().st_size
        logger.info(f"Files backup saved to {archive_path} ({size} bytes)")

        return BackupRecord(
            backup_type="files",
            tier="local",
            path=str(archive_path),
            size_bytes=size,
            status="created",
            metadata={
                "timestamp": timestamp,
                "sources": existing,
            },
        )

    except Exception as e:
        logger.error(f"Files backup failed: {e}")
        return BackupRecord(
            backup_type="files",
            tier="local",
            path=str(archive_path),
            size_bytes=0,
            status="failed",
            error=str(e),
        )


def restore_postgres(
    backup_path: str,
    db_url: str | None = None,
) -> bool:
    """Restore the PostgreSQL database from a pg_dump custom-format file.

    RESTORE IS DISABLED BY DEFAULT. Set RAG_ALLOW_RESTORE=1 in the
    environment to enable it.

    Args:
        backup_path: Path to the .dump file produced by backup_postgres().
        db_url:      Target database URL. Defaults to config.DB_URL.

    Returns:
        True if restore succeeded, False otherwise.
    """
    if os.environ.get("RAG_ALLOW_RESTORE") != "1":
        logger.warning(
            "restore_postgres called but RAG_ALLOW_RESTORE != 1 – refusing to proceed"
        )
        return False

    path = Path(backup_path)
    if not path.exists():
        logger.error(f"Backup file not found: {path}")
        return False

    url = db_url or DB_URL
    try:
        parsed = _parse_db_url(url)
    except ValueError as e:
        logger.error(f"Cannot parse DB_URL: {e}")
        return False

    env = {"PGPASSWORD": parsed["password"]}

    cmd = [
        "pg_restore",
        "--clean",
        "--if-exists",
        "--no-owner",
        f"--host={parsed['host']}",
        f"--port={parsed['port']}",
        f"--username={parsed['user']}",
        f"--dbname={parsed['dbname']}",
        str(path),
    ]

    result = _run_pg_command(cmd, env=env)
    if result.returncode != 0:
        logger.error(f"pg_restore failed: {result.stderr.strip()}")
        return False

    logger.info(f"Postgres restore completed: {path}")
    return True


def restore_files(
    backup_path: str,
    target_dir: str | Path,
) -> bool:
    """Restore files from a tar.gz archive.

    RESTORE IS DISABLED BY DEFAULT. Set RAG_ALLOW_RESTORE=1 in the
    environment to enable it.

    Args:
        backup_path: Path to the .tar.gz file produced by backup_files().
        target_dir:  Directory to extract into. Will be created if missing.

    Returns:
        True if extraction succeeded, False otherwise.
    """
    if os.environ.get("RAG_ALLOW_RESTORE") != "1":
        logger.warning(
            "restore_files called but RAG_ALLOW_RESTORE != 1 – refusing to proceed"
        )
        return False

    path = Path(backup_path)
    if not path.exists():
        logger.error(f"Backup archive not found: {path}")
        return False

    target = Path(target_dir)
    _ensure_dir(target)

    try:
        with tarfile.open(path, "r:gz") as tf:
            tf.extractall(target)
        logger.info(f"Files restored from {path} to {target}")
        return True
    except Exception as e:
        logger.error(f"Files restore failed: {e}")
        return False


def verify_backup(backup_path: str, backup_type: str) -> bool:
    """Verify a backup file is valid.

    Checks:
      - File exists and size > 0
      - pg_dump custom format: magic bytes "PGDMP"
      - tar.gz: valid gzip + tar header

    Args:
        backup_path:  Path to the backup file.
        backup_type:  "postgres" or "files".

    Returns:
        True if the backup passes all checks.
    """
    path = Path(backup_path)
    if not path.exists():
        logger.warning(f"verify_backup: file not found: {path}")
        return False

    size = path.stat().st_size
    if size == 0:
        logger.warning(f"verify_backup: file is empty: {path}")
        return False

    try:
        if backup_type == "postgres":
            # pg_dump custom format starts with the 5-byte magic "PGDMP"
            with open(path, "rb") as fh:
                magic = fh.read(5)
            if magic != b"PGDMP":
                logger.warning(
                    f"verify_backup: wrong magic bytes for postgres backup: {magic!r}"
                )
                return False

        elif backup_type == "files":
            # Verify it's a valid gzip + tar file
            if not tarfile.is_tarfile(path):
                logger.warning(
                    f"verify_backup: {path} is not a valid tar.gz file"
                )
                return False
            # Also check gzip magic
            with open(path, "rb") as fh:
                gz_magic = fh.read(2)
            if gz_magic != b"\x1f\x8b":
                logger.warning(
                    f"verify_backup: wrong gzip magic for files backup: {gz_magic!r}"
                )
                return False
        else:
            logger.warning(f"verify_backup: unknown backup_type: {backup_type}")
            return False

    except Exception as e:
        logger.error(f"verify_backup error: {e}")
        return False

    logger.info(f"verify_backup: {path} passed ({size} bytes)")
    return True


def list_backups(db=None) -> list[dict[str, Any]]:
    """Return all backup records from the database.

    Args:
        db: Optional SQLAlchemy session. If not provided, a new session
            is opened and closed.

    Returns:
        List of backup records as dicts, newest first.
    """
    # Import here to avoid circular imports at module level
    from sqlalchemy import select

    if db is None:
        from ..db import SessionLocal
        db = SessionLocal()
        close_after = True
    else:
        close_after = False

    try:
        rows = db.scalars(
            select(DBBackupRecord)
            .order_by(DBBackupRecord.created_at.desc())
        ).all()

        results = []
        for r in rows:
            try:
                import json
                meta = json.loads(r.metadata_json) if r.metadata_json else {}
            except Exception:
                meta = {}
            results.append({
                "id": r.id,
                "backup_type": r.backup_type,
                "tier": r.tier,
                "path": r.path,
                "size_bytes": r.size_bytes,
                "status": r.status,
                "error": r.error,
                "metadata": meta,
                "created_at": r.created_at,
            })
        return results
    finally:
        if close_after:
            db.close()


def cleanup_old_backups(days: int = 30) -> int:
    """Delete backup files older than `days` from the backup directory.

    Only removes files whose names start with "postgres_" or "files_" and
    have the expected timestamp suffix (YYYYMMDD_HHMMSS).

    Args:
        days: Files older than this many days will be deleted.

    Returns:
        Number of files deleted.
    """
    backup_dir = BACKUP_DIR
    if not backup_dir.is_dir():
        logger.warning(f"Backup directory does not exist: {backup_dir}")
        return 0

    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    deleted = 0

    for entry in backup_dir.iterdir():
        if not entry.is_file():
            continue

        name = entry.name
        # Match postgres_YYYYMMDD_HHMMSS.dump or files_YYYYMMDD_HHMMSS.tar.gz
        if not (name.startswith("postgres_") or name.startswith("files_")):
            continue

        if entry.stat().st_mtime < cutoff:
            try:
                entry.unlink()
                logger.info(f"Deleted old backup: {entry}")
                deleted += 1
            except OSError as e:
                logger.warning(f"Failed to delete {entry}: {e}")

    logger.info(f"cleanup_old_backups: removed {deleted} file(s)")
    return deleted
