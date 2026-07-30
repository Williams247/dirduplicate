"""ZIP archive creation and compression-ratio measurement."""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str], None]


def create_zip(
    source_dir: Path | str,
    zip_path: Path | str,
    *,
    compression: int = zipfile.ZIP_DEFLATED,
    progress_callback: ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> Path:
    """Create a ZIP archive from *source_dir*, streaming files into the archive.

    Args:
        source_dir: Directory whose contents will be archived.
        zip_path: Destination ZIP file path.
        compression: zipfile compression constant.
        progress_callback: Optional (percent, message) callback.
        cancel_check: Optional callable returning True if the job should abort.

    Returns:
        Path to the created ZIP file.
    """
    root = Path(source_dir)
    destination = Path(zip_path)

    if not root.is_dir():
        raise NotADirectoryError(f"Source is not a directory: {root}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    dest_resolved = destination.resolve()
    files = [
        p for p in root.rglob("*")
        if p.is_file() and p.resolve() != dest_resolved
    ]
    total = len(files) or 1

    try:
        with zipfile.ZipFile(destination, "w", compression=compression, compresslevel=6) as zf:
            for index, file_path in enumerate(files, start=1):
                if cancel_check and cancel_check():
                    raise InterruptedError("ZIP creation cancelled")

                arcname = file_path.relative_to(root).as_posix()
                zf.write(file_path, arcname)

                if progress_callback:
                    percent = (index / total) * 100.0
                    progress_callback(percent, f"Compressing {arcname}")
    except Exception:
        if destination.exists():
            destination.unlink(missing_ok=True)
        raise

    logger.info("Created ZIP %s (%s files)", destination, total)
    return destination


def measure_compression_ratio(folder_path: Path | str, temp_zip: Path | str) -> float:
    """Compress one copy of *folder_path* and return compressed/original ratio.

    Falls back to 0.5 if measurement fails or the folder is empty.
    """
    root = Path(folder_path)
    zip_target = Path(temp_zip)

    original_size = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                original_size += path.stat().st_size
            except OSError:
                continue

    if original_size == 0:
        return 0.5

    try:
        create_zip(root, zip_target)
        compressed = zip_target.stat().st_size
        ratio = compressed / original_size
        return max(0.01, min(1.0, ratio))
    except Exception as exc:  # noqa: BLE001 — measurement is best-effort
        logger.warning("Compression ratio measurement failed: %s", exc)
        return 0.5
    finally:
        if zip_target.exists():
            try:
                zip_target.unlink()
            except OSError:
                pass
