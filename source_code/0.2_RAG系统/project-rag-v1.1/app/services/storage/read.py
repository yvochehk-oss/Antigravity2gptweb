"""Storage read operations - file validation and metadata retrieval."""
from pathlib import Path

from app.config import ORIGINAL_DIR, SAFE_ORIGIN_DIRS
from app.logging_config import get_logger

logger = get_logger(__name__)


class PathTraversalError(Exception):
    """Raised when path traversal attack is detected."""
    pass


def _validate_safe_path(path: Path, operation: str = "access") -> Path:
    """Validate that a path is within allowed directories."""
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as e:
        raise StorageError(f"Cannot resolve path: {e}")

    for allowed in SAFE_ORIGIN_DIRS:
        try:
            resolved.relative_to(allowed)
            return resolved
        except ValueError:
            continue

    raise PathTraversalError(
        f"Path '{path}' is outside allowed directories. "
        f"Allowed: {[str(p) for p in SAFE_ORIGIN_DIRS]}"
    )


def _check_symlink(path: Path) -> bool:
    """Check if path or any parent is a symlink."""
    try:
        current = path
        while True:
            if current.is_symlink():
                return True
            if current == current.parent:
                break
            current = current.parent
        return False
    except OSError:
        return True


def validate_stored_file(path: str) -> Path:
    """Return a regular stored file path under ORIGINAL_DIR only."""
    candidate = Path(path)
    if candidate.is_symlink():
        raise PathTraversalError("stored file cannot be a symlink")
    resolved = _validate_safe_path(candidate, operation="download")
    try:
        resolved.relative_to(ORIGINAL_DIR.resolve())
    except ValueError as exc:
        raise PathTraversalError("download path is outside document storage") from exc
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
