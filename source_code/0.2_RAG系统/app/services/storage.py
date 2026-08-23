"""Secure file storage with path traversal protection.

Ensures files are only saved within allowed directories.
"""
from pathlib import Path
import hashlib
import shutil
from ..config import ORIGINAL_DIR, SAFE_ORIGIN_DIRS
from ..logging_config import get_logger

logger = get_logger(__name__)

SAFE_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".docx", ".pptx", ".xlsx", ".md", ".txt", ".html"}


class StorageError(Exception):
    """Raised when storage operation fails."""
    pass


class PathTraversalError(Exception):
    """Raised when path traversal attack is detected."""
    pass


def _validate_safe_path(path: Path, operation: str = "access") -> Path:
    """Validate that a path is within allowed directories.

    Args:
        path: Path to validate
        operation: Operation name for logging

    Returns:
        Resolved path if safe

    Raises:
        PathTraversalError: If path is outside allowed directories
    """
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as e:
        raise StorageError(f"Cannot resolve path: {e}")

    # Check against all safe origins using try/except ValueError (Python 3.8+ compatible)
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
    """Check if path or any parent is a symlink.

    Args:
        path: Path to check

    Returns:
        True if any component is a symlink
    """
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
        return True  # Treat errors as potential symlink


def sha256_bytes(data: bytes) -> str:
    """Calculate SHA-256 hash of bytes.

    Args:
        data: Bytes to hash

    Returns:
        Hex string of SHA-256 digest
    """
    return hashlib.sha256(data).hexdigest()


def save_original(
    project_code: str,
    document_code: str,
    filename: str,
    data: bytes
) -> Path:
    """Save original file to storage with security checks.

    Args:
        project_code: Project identifier
        document_code: Document identifier
        filename: Original filename
        data: File content

    Returns:
        Path to saved file

    Raises:
        StorageError: If file type is not allowed
        PathTraversalError: If path would escape safe directories
    """
    ext = Path(filename).suffix.lower()

    if ext not in SAFE_EXTS:
        raise StorageError(f"Unsupported file type: {ext or '(none)'}")

    # Create directory path
    folder = ORIGINAL_DIR / project_code / document_code

    # Security check: ensure folder is within allowed directories
    try:
        _validate_safe_path(folder)
    except PathTraversalError:
        raise

    # Create parent directories
    try:
        folder.mkdir(parents=True, exist_ok=True)

        # Check if folder is now a symlink (race condition protection)
        if folder.is_symlink():
            raise PathTraversalError("Directory became a symlink during creation")

    except OSError as e:
        raise StorageError(f"Cannot create directory {folder}: {e}")
    except PathTraversalError:
        raise

    # Target file path
    target = folder / Path(filename).name

    # Additional security: ensure target is within folder
    try:
        target = _validate_safe_path(target)
    except PathTraversalError:
        raise

    # Write file
    try:
        target.write_bytes(data)
    except OSError as e:
        raise StorageError(f"Cannot write file {target}: {e}")

    logger.info(f"Saved original file: {target} ({len(data)} bytes)")

    return target


def delete_file(path: str) -> bool:
    """Delete a file if it exists and is within safe directories.

    Args:
        path: Path to file

    Returns:
        True if deleted, False if not found
    """
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
    """Delete a directory and all contents if within safe directories.

    Args:
        path: Path to directory

    Returns:
        True if deleted, False otherwise
    """
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


def get_file_size(path: str) -> int:
    """Get file size in bytes.

    Args:
        path: Path to file

    Returns:
        File size in bytes, 0 if not found
    """
    try:
        p = Path(path)
        if p.exists():
            return p.stat().st_size
    except OSError:
        pass
    return 0


def file_exists(path: str) -> bool:
    """Check if file exists and is within safe directories.

    Args:
        path: Path to check

    Returns:
        True if file exists
    """
    try:
        p = Path(path)
        _validate_safe_path(p)
        return p.exists() and p.is_file()
    except (StorageError, PathTraversalError):
        return False
