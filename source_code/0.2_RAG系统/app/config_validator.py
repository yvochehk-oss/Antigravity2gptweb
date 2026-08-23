"""Configuration validation and management."""
from pathlib import Path
import os
import re

class ConfigError(Exception):
    """Raised when configuration is invalid."""
    pass


def validate_path(path: str, must_exist: bool = False) -> Path:
    """Validate and resolve a path.

    Args:
        path: Path string to validate
        must_exist: If True, path must exist

    Returns:
        Resolved Path object

    Raises:
        ConfigError: If path is invalid or doesn't exist when required
    """
    if not path:
        raise ConfigError("Path cannot be empty")

    p = Path(path).expanduser().resolve()

    if must_exist and not p.exists():
        raise ConfigError(f"Path does not exist: {path}")

    return p


def validate_port(port: str) -> int:
    """Validate port number.

    Args:
        port: Port string

    Returns:
        Valid port number

    Raises:
        ConfigError: If port is invalid
    """
    try:
        p = int(port)
        if not (1 <= p <= 65535):
            raise ConfigError(f"Port must be between 1 and 65535, got {p}")
        return p
    except ValueError:
        raise ConfigError(f"Invalid port: {port}")


def validate_embedding_dim(dim: int) -> int:
    """Validate embedding dimension.

    Args:
        dim: Dimension value

    Returns:
        Validated dimension

    Raises:
        ConfigError: If dimension is invalid
    """
    valid_dims = [256, 512, 768, 1024, 1536, 1792]
    if dim not in valid_dims:
        raise ConfigError(
            f"Embedding dimension should be one of {valid_dims}, got {dim}"
        )
    return dim


def validate_url(url: str, allow_empty: bool = True) -> str:
    """Validate URL format.

    Args:
        url: URL string
        allow_empty: If True, empty string is valid

    Returns:
        Validated URL (stripped)

    Raises:
        ConfigError: If URL is invalid
    """
    url = url.strip()

    if not url:
        if allow_empty:
            return url
        raise ConfigError("URL cannot be empty")

    # Basic URL pattern
    url_pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
        r'localhost|'  # localhost
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # IP
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)

    if not url_pattern.match(url):
        raise ConfigError(f"Invalid URL format: {url}")

    return url


def validate_config() -> list[str]:
    """Validate all configuration values.

    Returns:
        List of validation errors (empty if all valid)
    """
    from .config import (
        BASE_DIR, DATA_DIR, HOST, PORT,
        EMBEDDING_DIM, EMBEDDING_BACKEND,
        RERANKER_BACKEND, LLM_BASE_URL
    )

    errors = []

    # Validate paths
    try:
        validate_path(str(DATA_DIR))
    except ConfigError as e:
        errors.append(f"DATA_DIR: {e}")

    # Validate port
    try:
        validate_port(str(PORT))
    except ConfigError as e:
        errors.append(f"PORT: {e}")

    # Validate embedding dimension
    if EMBEDDING_BACKEND == "bge_m3":
        try:
            validate_embedding_dim(EMBEDDING_DIM)
        except ConfigError as e:
            errors.append(f"EMBEDDING_DIM: {e}")

    # Validate URLs
    if LLM_BASE_URL:
        try:
            validate_url(LLM_BASE_URL, allow_empty=False)
        except ConfigError as e:
            errors.append(f"LLM_BASE_URL: {e}")

    return errors


def ensure_directories() -> None:
    """Ensure all required directories exist.

    Creates directories if they don't exist.
    """
    from .config import DATA_DIR, ORIGINAL_DIR, PARSED_DIR, CACHE_DIR

    for p in (DATA_DIR, ORIGINAL_DIR, PARSED_DIR, CACHE_DIR):
        p.mkdir(parents=True, exist_ok=True)
