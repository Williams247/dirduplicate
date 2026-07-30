"""Shared utility helpers for path handling, formatting, and disk space."""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR = BASE_DIR / "temp"
LOGS_DIR = BASE_DIR / "logs"


def ensure_directories() -> None:
    """Create required application directories if they do not exist."""
    for directory in (UPLOADS_DIR, OUTPUT_DIR, TEMP_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def generate_id() -> str:
    """Return a short unique identifier."""
    return uuid.uuid4().hex[:12]


def format_bytes(num_bytes: int | float) -> str:
    """Format a byte count into a human-readable string."""
    value = float(max(0, num_bytes))
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


def format_duration(seconds: float) -> str:
    """Format seconds into a compact human-readable duration."""
    if seconds < 0 or seconds != seconds:  # NaN guard
        return "--"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def get_free_disk_space(path: Path | str | None = None) -> int:
    """Return free disk space in bytes for the given path (or OUTPUT_DIR)."""
    target = Path(path) if path else OUTPUT_DIR
    try:
        target.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(target).free
    except OSError as exc:
        logger.warning("Unable to read free disk space for %s: %s", target, exc)
        return 0


def safe_rmtree(path: Path | str) -> None:
    """Remove a directory tree, ignoring errors."""
    target = Path(path)
    if target.exists():
        try:
            shutil.rmtree(target, ignore_errors=True)
        except OSError as exc:
            logger.warning("Failed to remove %s: %s", target, exc)


def safe_unlink(path: Path | str) -> None:
    """Remove a file if it exists."""
    target = Path(path)
    try:
        if target.is_file():
            target.unlink()
    except OSError as exc:
        logger.warning("Failed to unlink %s: %s", target, exc)


def resolve_under(base: Path, relative: str) -> Path:
    """Resolve a relative path under *base*, rejecting path traversal."""
    candidate = (base / relative).resolve()
    base_resolved = base.resolve()
    if not str(candidate).startswith(str(base_resolved)):
        raise ValueError("Path escapes the allowed base directory")
    return candidate
