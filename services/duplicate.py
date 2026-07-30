"""Folder duplication with ThreadPoolExecutor and progress reporting."""

from __future__ import annotations

import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable
from pathlib import Path

from services.rename import generate_folder_name

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict], None]
CancelCheck = Callable[[], bool]


def _copy_one(
    source: Path,
    dest: Path,
) -> Path:
    """Copy a single folder tree, preserving metadata where possible."""
    if dest.exists():
        raise FileExistsError(f"Destination already exists: {dest}")
    shutil.copytree(source, dest, copy_function=shutil.copy2, dirs_exist_ok=False)
    return dest


def duplicate_folders(
    source_folder: Path | str,
    output_dir: Path | str,
    original_name: str,
    count: int,
    *,
    max_workers: int = 8,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> list[Path]:
    """Duplicate *source_folder* *count* times into *output_dir* with renamed folders.

    Uses a thread pool for parallel copy operations. Reports progress via callback.

    Returns:
        List of created destination paths (in completion order may vary; sorted by index).
    """
    source = Path(source_folder)
    out = Path(output_dir)

    if not source.is_dir():
        raise NotADirectoryError(f"Source folder not found: {source}")
    if count < 1:
        raise ValueError("count must be a positive integer")

    out.mkdir(parents=True, exist_ok=True)

    planned: list[tuple[int, Path]] = []
    for index in range(1, count + 1):
        name = generate_folder_name(original_name, index)
        dest = out / name
        if dest.exists():
            raise FileExistsError(f"Folder already exists: {dest.name}")
        planned.append((index, dest))

    created: dict[int, Path] = {}
    completed = 0
    start = time.monotonic()
    cancelled = False

    workers = max(1, min(max_workers, count))

    def _emit(index: int, name: str, status: str = "duplicating") -> None:
        if not progress_callback:
            return
        elapsed = time.monotonic() - start
        speed = completed / elapsed if elapsed > 0 else 0.0
        remaining = count - completed
        eta = remaining / speed if speed > 0 else 0.0
        progress_callback(
            {
                "status": status,
                "phase": "duplicating",
                "percent": round((completed / count) * 100, 1),
                "completed": completed,
                "total": count,
                "current": name,
                "elapsed_seconds": round(elapsed, 2),
                "speed": round(speed, 2),
                "eta_seconds": round(eta, 1),
                "message": f"Duplicating {name}",
            }
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(_copy_one, source, dest): (index, dest)
            for index, dest in planned
        }

        for future in as_completed(future_map):
            if cancel_check and cancel_check():
                cancelled = True
                for pending in future_map:
                    pending.cancel()
                break

            index, dest = future_map[future]
            try:
                result = future.result()
                created[index] = result
                completed += 1
                _emit(index, dest.name)
            except Exception as exc:
                logger.exception("Failed to copy folder index %s: %s", index, exc)
                # Best-effort cleanup of partial destination
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                raise

    if cancelled:
        for path in created.values():
            shutil.rmtree(path, ignore_errors=True)
        raise InterruptedError("Duplication cancelled by user")

    ordered = [created[i] for i in sorted(created)]
    logger.info("Duplicated %s folders into %s", len(ordered), out)
    return ordered
