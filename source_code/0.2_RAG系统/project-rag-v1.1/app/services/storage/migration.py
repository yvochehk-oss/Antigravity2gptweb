"""Storage migration operations - backup, restore, and integrity checks.

This module is reserved for future use.  No migration operations are currently defined.
"""
from __future__ import annotations


def backup_file(source_path: str, backup_dir: str) -> str:
    """Backup a file to the backup directory.

    Args:
        source_path: Path to the file to backup
        backup_dir: Directory to store the backup

    Returns:
        Path to the backup file

    Raises:
        StorageError: If backup fails
    """
    from .write import StorageError, PathTraversalError, _validate_safe_path
    from pathlib import Path
    import shutil
    from datetime import datetime

    try:
        src = Path(source_path)
        _validate_safe_path(src, operation="backup")

        backup_path = Path(backup_dir)
        _validate_safe_path(backup_path, operation="backup")

        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        backup_name = f"{src.stem}_{timestamp}{src.suffix}"
        dest = backup_path / backup_name

        shutil.copy2(src, dest)
        return str(dest)
    except (StorageError, PathTraversalError) as exc:
        raise StorageError(f"Backup failed: {exc}") from exc


def restore_backup(backup_path: str, target_path: str) -> bool:
    """Restore a file from a backup.

    Args:
        backup_path: Path to the backup file
        target_path: Path to restore to

    Returns:
        True if restore succeeded

    Raises:
        StorageError: If restore fails
    """
    from .write import StorageError, PathTraversalError, _validate_safe_path
    from pathlib import Path
    import shutil

    try:
        backup = Path(backup_path)
        _validate_safe_path(backup, operation="restore")

        target = Path(target_path)
        _validate_safe_path(target, operation="restore")

        shutil.copy2(backup, target)
        return True
    except (StorageError, PathTraversalError) as exc:
        raise StorageError(f"Restore failed: {exc}") from exc
