"""Secure file storage with path traversal protection.

Provides re-exports from sub-modules for backward compatibility:
- read: validate_stored_file, file_exists, get_file_size
- write: save_original, delete_file, delete_directory, sha256_bytes
- migration: backup_file, restore_backup
- health: check_storage_health, get_storage_stats

For new code, import directly from the sub-modules.
"""
from app.config import ORIGINAL_DIR, SAFE_ORIGIN_DIRS
from .read import (
    validate_stored_file,
    file_exists,
    get_file_size,
    PathTraversalError,
)
from .write import (
    save_original,
    delete_file,
    delete_directory,
    sha256_bytes,
    StorageError,
    SAFE_EXTS,
    _validate_safe_path,
)
from .health import (
    check_storage_health,
    get_storage_stats,
)

__all__ = [
    "StorageError",
    "PathTraversalError",
    "validate_stored_file",
    "file_exists",
    "get_file_size",
    "save_original",
    "delete_file",
    "delete_directory",
    "sha256_bytes",
    "check_storage_health",
    "get_storage_stats",
    "SAFE_EXTS",
    "_validate_safe_path",
    "ORIGINAL_DIR",
    "SAFE_ORIGIN_DIRS",
]
