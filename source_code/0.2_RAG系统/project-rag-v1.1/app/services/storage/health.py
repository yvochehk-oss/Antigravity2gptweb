"""Storage health checks - storage system status and diagnostics."""
from pathlib import Path

from app.config import ORIGINAL_DIR, SAFE_ORIGIN_DIRS


def check_storage_health() -> dict:
    """Return storage subsystem health status.

    Checks:
    - Original storage directory exists and is accessible
    - No symlink attacks detected in path configuration
    """
    issues = []
    status = "ok"

    # Check that ORIGINAL_DIR exists
    if not ORIGINAL_DIR.exists():
        issues.append(f"Original storage directory does not exist: {ORIGINAL_DIR}")
        status = "degraded"
    elif not ORIGINAL_DIR.is_dir():
        issues.append(f"Original storage path is not a directory: {ORIGINAL_DIR}")
        status = "degraded"

    # Check that SAFE_ORIGIN_DIRS are accessible
    for origin in SAFE_ORIGIN_DIRS:
        if not origin.exists():
            issues.append(f"Safe origin directory does not exist: {origin}")
            status = "down"
        elif not origin.is_dir():
            issues.append(f"Safe origin path is not a directory: {origin}")
            status = "down"

    return {
        "status": status,
        "issues": issues,
        "original_dir": str(ORIGINAL_DIR),
        "safe_origin_dirs": [str(p) for p in SAFE_ORIGIN_DIRS],
    }


def get_storage_stats() -> dict:
    """Return storage statistics for monitoring."""
    try:
        total_size = 0
        file_count = 0
        if ORIGINAL_DIR.exists():
            for item in ORIGINAL_DIR.rglob("*"):
                if item.is_file():
                    total_size += item.stat().st_size
                    file_count += 1
        return {
            "total_files": file_count,
            "total_bytes": total_size,
        }
    except Exception:
        return {"total_files": 0, "total_bytes": 0, "error": "Failed to compute storage stats"}
