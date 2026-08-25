"""Storage write operations - file saving and deletion.

Document deletion is deliberately implemented as a small filesystem
transaction.  A document row is not removed until its referenced files have
been moved into a durable, same-filesystem staging directory.  The database
caller can then either restore that staging area after a failed transaction or
finalize it after a successful commit.  This avoids the old
``commit -> best-effort unlink`` sequence, which could report success while
leaving files behind (or leave a database row pointing at a file that had
already been removed).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import (
    DATA_DIR,
    ORIGINAL_DIR,
    PROJECT_MATERIALS_DIR,
    SAFE_ORIGIN_DIRS,
)
from app.logging_config import get_logger

logger = get_logger(__name__)

SAFE_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".docx", ".pptx", ".xlsx", ".md", ".txt", ".html"}

# Migration 012 moves document originals into ``project_materials``.  Keep
# that bundle-owned root in the deletion allow-list while retaining the
# existing import/data roots for uploaded and parsed files.
_DOCUMENT_STORAGE_ROOTS = tuple(
    dict.fromkeys([*(Path(root).resolve() for root in SAFE_ORIGIN_DIRS), PROJECT_MATERIALS_DIR.resolve()])
)
_DELETE_STAGING_ROOT = DATA_DIR / ".document-delete-staging"


class StorageError(Exception):
    """Raised when storage operation fails."""

    pass


class PathTraversalError(Exception):
    """Raised when path traversal attack is detected."""

    pass


@dataclass(frozen=True)
class StagedStoragePath:
    """One path moved aside as part of a document deletion transaction."""

    original_path: Path
    staged_path: Path
    kind: str
    size_bytes: int | None = None
    sha256: str | None = None


@dataclass
class DocumentStorageDeletion:
    """Filesystem side of a document deletion transaction.

    ``transaction_dir`` is intentionally retained until ``finalize`` has
    removed every staged object.  A manifest in that directory makes a
    post-crash or cleanup failure inspectable and recoverable by an operator.
    """

    transaction_dir: Path | None
    entries: list[StagedStoragePath]
    finalized: bool = False
    rolled_back: bool = False

    @property
    def manifest_path(self) -> Path | None:
        if self.transaction_dir is None:
            return None
        return self.transaction_dir / "manifest.json"


def _has_symlink_component(path: Path) -> bool:
    """Return whether *path* or an existing parent component is a symlink."""
    try:
        absolute = Path(path).expanduser()
        if not absolute.is_absolute():
            absolute = Path.cwd() / absolute
        current = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            current /= part
            if current.is_symlink():
                return True
        return False
    except OSError:
        # An unreadable component must not be treated as a trusted path.
        return True


def _validate_safe_path(
    path: Path,
    operation: str = "access",
    *,
    allowed_roots: list[Path] | tuple[Path, ...] | None = None,
) -> Path:
    """Validate that a path is within allowed directories."""
    candidate = Path(path).expanduser()
    # Check before resolve(): a symlink which points back inside an allowed
    # root is still not a trusted import/mount path.
    if _has_symlink_component(candidate):
        raise PathTraversalError(f"Path '{path}' contains a symlink component")
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as e:
        raise StorageError(f"Cannot resolve path: {e}")

    roots = SAFE_ORIGIN_DIRS if allowed_roots is None else allowed_roots
    for allowed in roots:
        try:
            resolved.relative_to(allowed)
            return resolved
        except ValueError:
            continue

    raise PathTraversalError(
        f"Path '{path}' is outside allowed directories. Allowed: {[str(p) for p in roots]}"
    )


def _validate_document_delete_path(path: str | None, *, kind: str) -> Path | None:
    """Validate a path recorded on a document before staging it for deletion."""
    if not path:
        return None

    candidate = Path(path).expanduser()
    resolved = _validate_safe_path(
        candidate,
        operation="document deletion",
        allowed_roots=_DOCUMENT_STORAGE_ROOTS,
    )
    staging_root = _DELETE_STAGING_ROOT.resolve()
    try:
        resolved.relative_to(staging_root)
    except ValueError:
        pass
    else:
        raise PathTraversalError("document path cannot point into deletion staging")

    if not resolved.exists():
        return resolved
    if kind == "file" and not resolved.is_file():
        raise StorageError(f"document original is not a regular file: {resolved}")
    if kind == "directory" and not resolved.is_dir():
        raise StorageError(f"document parsed path is not a directory: {resolved}")
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ensure_delete_staging_root() -> Path:
    """Create and validate the private same-filesystem staging root."""
    root = _DELETE_STAGING_ROOT
    _validate_safe_path(
        root,
        operation="document deletion staging",
        allowed_roots=(DATA_DIR.resolve(),),
    )
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageError(f"Cannot create deletion staging root {root}: {exc}") from exc
    if _has_symlink_component(root) or not root.is_dir():
        raise PathTraversalError(f"Deletion staging root is not a trusted directory: {root}")
    return root


def _write_deletion_manifest(
    transaction: DocumentStorageDeletion,
    *,
    status: str,
    error: str = "",
) -> None:
    """Write a durable manifest using an atomic replace inside the staging dir."""
    manifest = transaction.manifest_path
    if manifest is None:
        return

    payload = {
        "status": status,
        "entries": [
            {
                "original_path": str(entry.original_path),
                "staged_path": str(entry.staged_path),
                "kind": entry.kind,
                "size_bytes": entry.size_bytes,
                "sha256": entry.sha256,
            }
            for entry in transaction.entries
        ],
        "error": error[:2000],
    }
    temporary = manifest.with_name("manifest.json.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(manifest)
    except OSError as exc:
        raise StorageError(f"Cannot persist deletion manifest {manifest}: {exc}") from exc


def _remove_deletion_transaction_dir(transaction: DocumentStorageDeletion) -> None:
    """Remove an empty transaction directory and its manifest."""
    directory = transaction.transaction_dir
    if directory is None:
        return
    manifest = transaction.manifest_path
    if manifest is not None and manifest.exists():
        manifest.unlink()
    directory.rmdir()


def _restore_staged_entries(transaction: DocumentStorageDeletion) -> None:
    """Restore every moved path, refusing to overwrite a new path."""
    failures: list[str] = []
    for entry in reversed(transaction.entries):
        staged = entry.staged_path
        original = entry.original_path
        if not staged.exists():
            if original.exists():
                continue
            failures.append(f"staged path is missing: {staged}")
            continue
        if original.exists():
            failures.append(f"refusing to overwrite restored path: {original}")
            continue
        if _has_symlink_component(original.parent):
            failures.append(f"original parent contains a symlink: {original.parent}")
            continue
        try:
            staged.rename(original)
            if entry.kind == "file":
                if not original.is_file():
                    raise StorageError(f"restored path is not a file: {original}")
                if entry.sha256 and _sha256_file(original) != entry.sha256:
                    raise StorageError(f"restored file hash mismatch: {original}")
            elif not original.is_dir():
                raise StorageError(f"restored path is not a directory: {original}")
        except (OSError, StorageError) as exc:
            failures.append(str(exc))

    if failures:
        raise StorageError("; ".join(failures))


def stage_document_cleanup(
    original_path: str | None,
    parsed_dir: str | None,
) -> DocumentStorageDeletion:
    """Move document storage into a durable staging area before DB commit.

    The operation is reversible with :func:`restore_document_cleanup` until
    :func:`finalize_document_cleanup` is called.  Paths that are missing are
    treated as already-cleaned idempotently; unsafe paths, type mismatches,
    cross-document shared directories, and filesystem failures raise.
    """
    original = _validate_document_delete_path(original_path, kind="file")
    parsed = _validate_document_delete_path(parsed_dir, kind="directory")

    targets: list[tuple[Path, str]] = []
    if original is not None and parsed is not None and original.exists() and parsed.exists():
        try:
            original.relative_to(parsed)
        except ValueError:
            pass
        else:
            # Plain-text/Markdown imports can use the original file's parent
            # as parsed_dir.  Only move that directory as a unit when it is
            # provably private to this document; otherwise refuse instead of
            # deleting another document's files.
            if original.parent != parsed:
                raise StorageError(
                    "document original is nested below parsed_dir; refusing shared-directory deletion"
                )
            children = list(parsed.iterdir())
            if children != [original] and set(children) != {original}:
                raise StorageError(
                    f"parsed_dir contains files beyond the document original: {parsed}"
                )
            targets.append((parsed, "directory"))

    if not targets:
        if original is not None and original.exists():
            targets.append((original, "file"))
        if parsed is not None and parsed.exists():
            targets.append((parsed, "directory"))

    if not targets:
        return DocumentStorageDeletion(transaction_dir=None, entries=[])

    root = _ensure_delete_staging_root()
    transaction_dir = root / uuid.uuid4().hex
    try:
        transaction_dir.mkdir()
    except OSError as exc:
        raise StorageError(f"Cannot create deletion transaction {transaction_dir}: {exc}") from exc

    transaction = DocumentStorageDeletion(transaction_dir=transaction_dir, entries=[])
    try:
        for index, (target, kind) in enumerate(targets):
            staged = transaction_dir / f"{index:04d}-{kind}"
            entry = StagedStoragePath(
                original_path=target,
                staged_path=staged,
                kind=kind,
                size_bytes=target.stat().st_size if kind == "file" else None,
                sha256=_sha256_file(target) if kind == "file" else None,
            )
            # rename() is intentionally used instead of shutil.move(): a
            # cross-device copy would not give us an atomic, recoverable stage.
            target.rename(staged)
            transaction.entries.append(entry)

        _write_deletion_manifest(transaction, status="staged")
        logger.info("Staged document storage for deletion: %s", transaction_dir)
        return transaction
    except Exception as exc:
        try:
            if transaction.entries:
                _restore_staged_entries(transaction)
            _remove_deletion_transaction_dir(transaction)
        except Exception as restore_exc:
            _write_deletion_manifest(
                transaction,
                status="recovery_required",
                error=f"stage failed: {exc}; restore failed: {restore_exc}",
            )
            raise StorageError(
                f"Cannot stage document deletion: {exc}; restore failed: {restore_exc}"
            ) from exc
        raise StorageError(f"Cannot stage document deletion: {exc}") from exc


def restore_document_cleanup(transaction: DocumentStorageDeletion) -> None:
    """Compensate a staged deletion after a DB failure."""
    if transaction.finalized:
        raise StorageError("cannot restore a finalized document deletion")
    if transaction.rolled_back:
        return
    if transaction.transaction_dir is None:
        transaction.rolled_back = True
        return

    try:
        _restore_staged_entries(transaction)
        _remove_deletion_transaction_dir(transaction)
        transaction.rolled_back = True
        logger.info("Restored staged document storage after DB rollback")
    except Exception as exc:
        try:
            _write_deletion_manifest(transaction, status="recovery_required", error=str(exc))
        except Exception as manifest_exc:
            logger.error("Could not update deletion manifest after restore failure: %s", manifest_exc)
        raise StorageError(f"Document deletion recovery failed: {exc}") from exc


def finalize_document_cleanup(transaction: DocumentStorageDeletion) -> None:
    """Permanently remove staged storage after a successful DB commit.

    Cleanup is deliberately strict.  A failure leaves the staging directory
    and manifest in place for verifiable operator recovery and raises instead
    of returning a false success to the API caller.
    """
    if transaction.rolled_back:
        raise StorageError("cannot finalize a rolled-back document deletion")
    if transaction.finalized:
        return
    if transaction.transaction_dir is None:
        transaction.finalized = True
        return

    failures: list[str] = []
    for entry in transaction.entries:
        staged = entry.staged_path
        if not staged.exists():
            failures.append(f"staged path is missing: {staged}")
            continue
        try:
            if entry.kind == "file":
                staged.unlink()
            else:
                shutil.rmtree(staged)
        except OSError as exc:
            failures.append(f"{staged}: {exc}")

    if failures:
        message = "; ".join(failures)
        try:
            _write_deletion_manifest(transaction, status="cleanup_pending", error=message)
        except Exception as manifest_exc:
            logger.error("Could not update deletion manifest: %s", manifest_exc)
        raise StorageError(f"Document storage cleanup pending: {message}")

    try:
        _remove_deletion_transaction_dir(transaction)
    except OSError as exc:
        try:
            _write_deletion_manifest(transaction, status="cleanup_pending", error=str(exc))
        except Exception as manifest_exc:
            logger.error("Could not update deletion manifest: %s", manifest_exc)
        raise StorageError(f"Document deletion staging cleanup failed: {exc}") from exc

    transaction.finalized = True
    logger.info("Finalized document storage deletion")


def validate_safe_directory(path: Path | str) -> Path:
    """Resolve and validate an existing, non-symlink directory import root."""
    candidate = Path(path).expanduser()
    resolved = _validate_safe_path(candidate, operation="directory access")
    if not resolved.exists() or not resolved.is_dir():
        raise StorageError(f"directory does not exist: {path}")
    return resolved


def _check_symlink(path: Path) -> bool:
    """Check if path or any parent is a symlink."""
    return _has_symlink_component(path)


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
    """Delete a file if it exists and is within safe directories.

    Missing files are an idempotent no-op.  Validation and filesystem errors
    are raised so callers cannot accidentally report a successful deletion.
    """
    p = _validate_safe_path(
        Path(path),
        operation="file deletion",
        allowed_roots=_DOCUMENT_STORAGE_ROOTS,
    )
    if not p.exists():
        return False
    if not p.is_file():
        raise StorageError(f"Cannot delete non-file path: {p}")
    try:
        p.unlink()
    except OSError as exc:
        raise StorageError(f"Cannot delete file {p}: {exc}") from exc
    logger.info("Deleted file: %s", p)
    return True


def delete_directory(path: str) -> bool:
    """Delete a directory if it exists and is within safe directories."""
    p = _validate_safe_path(
        Path(path),
        operation="directory deletion",
        allowed_roots=_DOCUMENT_STORAGE_ROOTS,
    )
    if not p.exists():
        return False
    if not p.is_dir():
        raise StorageError(f"Cannot delete non-directory path: {p}")
    try:
        shutil.rmtree(p)
    except OSError as exc:
        raise StorageError(f"Cannot delete directory {p}: {exc}") from exc
    logger.info("Deleted directory: %s", p)
    return True
