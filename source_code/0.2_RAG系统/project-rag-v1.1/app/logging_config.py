"""Structured logging configuration.

Provides consistent logging format across all modules.
"""
import sys
import logging
from pathlib import Path
from datetime import datetime

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | "
    "%(message)s [request_id=%(request_id)s]"
)

_INSTALLED = False


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure application-wide logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)

    Returns:
        Configured root logger
    """
    global _INSTALLED
    log_file = LOG_DIR / f"projectrag_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=_FORMAT,
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    if not _INSTALLED:
        try:
            from .observability import install_logging_filter
            install_logging_filter()
        except Exception:  # pragma: no cover - logging must never fail
            pass
        _INSTALLED = True

    return logging.getLogger("projectrag")


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for a specific module.

    Args:
        name: Module name (usually __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(f"projectrag.{name}")