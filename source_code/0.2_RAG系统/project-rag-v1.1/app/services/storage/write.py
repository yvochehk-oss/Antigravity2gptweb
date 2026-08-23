"""Storage write operations - file saving and deletion."""
from pathlib import Path
import hashlib
import shutil

from app.config import ORIGINAL_DIR, SAFE_ORIGIN_DIRS
from app.logging_config import get_logger

logger = get_logger(__name__)

SAFE_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".docx", ".pptx", ".xlsx", ".md", ".txt", ".html"}


class StorageError(Exception):
    """Raised when storage operation fails."""
    pass


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


def sha256_bytes(data: bytes) -> str:
    """Calculate SHA-256 hash of bytes."""
    return hashlib.sha256(data).hexdigest()


def save_original(project_code: str, document_code: str, filename: str, data: bytes) -> Path:
    """Save original file to storage with security checks."""
    ext = Path(filename).suffix.lower()

    if ext not in SAFE_EXTS:
        raise StorageError(f"Unsupported file type: {ext or '(none)'}")

    folder = ORIGINAL_DIR / project_code / document_code

    try:
        _validate_safe_path(folder)
    except PathTraversalError:
        raise

    try:
        folder.mkdir(parents=True, exist_ok=True)

        if folder.is_symlink():
            raise PathTraversalError("Directory became a symlink during creation")

    except OSError as e:
        raise StorageError(f"Cannot create directory {folder}: {e}")
    except PathTraversalError:
        raise

    target = folder / Path(filename).name

    try:
        target = _validate_safe_path(target)
    except PathTraversalError:
        raise

    try:
        target.write_bytes(data)
    except OSError as e:
        raise StorageError(f"Cannot write file {target}: {e}")

    logger.info(f"Saved original file: {target} ({len(data)} bytes)")

    return target


def delete_file(path: str) -> bool:
    """Delete a file if it exists and is within safe directories."""
    try:
        p = Path(path)
        _validate_safe_path(p)

        if p.exists():
            p.unlink()
            logger.info(f"Deleted file: {p}")
            return True
        return False
    except (StorageError, PathTraversalError) as e:
        logger.warning(f"Cannot delete file {path}: {e}")
        return False


def delete_directory(path: str) -> bool:
    """Delete a directory and all contents if within safe directories."""
    try:
        p = Path(path)
        _validate_safe_path(p)

        if p.exists() and p.is_dir():
            shutil.rmtree(p)
            logger.info(f"Deleted directory: {p}")
            return True
        return False
    except (StorageError, PathTraversalError) as e:
        logger.warning(f"Cannot delete directory {path}: {e}")
        return False
