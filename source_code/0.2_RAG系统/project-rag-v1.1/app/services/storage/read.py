"""Storage read operations - file validation and metadata retrieval."""

from pathlib import Path

from app.config import ORIGINAL_DIR, PROJECT_MATERIALS_DIR, SAFE_ORIGIN_DIRS
from app.logging_config import get_logger

from .write import PathTraversalError, StorageError, _validate_safe_path

logger = get_logger(__name__)

_DOWNLOAD_ROOTS = tuple(
    dict.fromkeys([
        *(Path(root).resolve() for root in SAFE_ORIGIN_DIRS),
        ORIGINAL_DIR.resolve(),
        PROJECT_MATERIALS_DIR.resolve(),
    ]).keys()
)



def validate_stored_file(path: str) -> Path:
    """Return a regular stored file path under ORIGINAL_DIR only."""
    candidate = Path(path)
    if candidate.is_symlink():
        raise PathTraversalError("stored file cannot be a symlink")
    resolved = _validate_safe_path(
        candidate,
        operation="download",
        allowed_roots=_DOWNLOAD_ROOTS,
    )
    if not resolved.is_file():
        raise StorageError("stored file does not exist")
    return resolved


def file_exists(path: str) -> bool:
    """Check if file exists and is within safe directories."""
    try:
        p = Path(path)
        _validate_safe_path(p)
        return p.exists() and p.is_file()
    except (StorageError, PathTraversalError):
        return False


def get_file_size(path: str) -> int:
    """Get file size in bytes."""
    try:
        p = Path(path)
        if p.exists():
            return p.stat().st_size
    except OSError:
        pass
    return 0
