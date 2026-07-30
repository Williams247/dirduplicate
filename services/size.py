"""Folder size and file-count helpers."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def calculate_folder_stats(folder_path: Path | str) -> dict[str, int | str]:
    """Recursively calculate file count and total size for a folder.

    Returns:
        dict with folder_name, file_count, total_size (bytes), and folder_count.
    """
    root = Path(folder_path)
    if not root.exists():
        raise FileNotFoundError(f"Folder not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")

    file_count = 0
    folder_count = 0
    total_size = 0

    for path in root.rglob("*"):
        try:
            if path.is_file():
                file_count += 1
                total_size += path.stat().st_size
            elif path.is_dir():
                folder_count += 1
        except OSError as exc:
            logger.warning("Skipping inaccessible path %s: %s", path, exc)

    return {
        "folder_name": root.name,
        "file_count": file_count,
        "folder_count": folder_count,
        "total_size": total_size,
    }


def estimate_output_size(original_size: int, copies: int) -> int:
    """Estimate total uncompressed size of *copies* folder duplicates."""
    if copies < 1 or original_size < 0:
        return 0
    return original_size * copies


def estimate_zip_size(original_size: int, copies: int, compression_ratio: float) -> int:
    """Estimate ZIP size using a measured compression ratio (compressed/original)."""
    total = estimate_output_size(original_size, copies)
    ratio = max(0.01, min(1.0, compression_ratio))
    return int(total * ratio)
