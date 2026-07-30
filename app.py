"""Folder Duplicator — FastAPI application entry point."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from services.duplicate import duplicate_folders
from services.rename import preview_names
from services.size import calculate_folder_stats, estimate_output_size, estimate_zip_size
from services.utils import (
    BASE_DIR,
    OUTPUT_DIR,
    TEMP_DIR,
    UPLOADS_DIR,
    ensure_directories,
    format_bytes,
    generate_id,
    get_free_disk_space,
    safe_rmtree,
    safe_unlink,
)
from services.zipper import create_zip, measure_compression_ratio

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

ensure_directories()

LOG_FILE = BASE_DIR / "logs" / "app.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("folder_duplicator")


# ---------------------------------------------------------------------------
# Job state
# ---------------------------------------------------------------------------


@dataclass
class JobState:
    """Tracks progress and metadata for an upload/duplication job."""

    job_id: str
    folder_name: str = ""
    source_path: Path | None = None
    work_dir: Path | None = None
    zip_path: Path | None = None
    file_count: int = 0
    folder_count: int = 0
    original_size: int = 0
    compression_ratio: float = 0.5
    copies: int = 0
    status: str = "idle"
    phase: str = "idle"
    percent: float = 0.0
    completed: int = 0
    total: int = 0
    current: str = ""
    message: str = ""
    error: str | None = None
    cancel_requested: bool = False
    elapsed_seconds: float = 0.0
    speed: float = 0.0
    eta_seconds: float = 0.0
    created_at: float = field(default_factory=time.time)
    preview: list[str] = field(default_factory=list)

    def to_progress(self) -> dict[str, Any]:
        """Serialize progress fields for SSE / polling clients."""
        return {
            "job_id": self.job_id,
            "status": self.status,
            "phase": self.phase,
            "percent": self.percent,
            "completed": self.completed,
            "total": self.total,
            "current": self.current,
            "message": self.message,
            "error": self.error,
            "elapsed_seconds": self.elapsed_seconds,
            "speed": self.speed,
            "eta_seconds": self.eta_seconds,
            "folder_name": self.folder_name,
            "zip_ready": self.zip_path is not None and self.zip_path.exists(),
        }


jobs: dict[str, JobState] = {}


def get_job(job_id: str) -> JobState:
    """Fetch a job or raise 404."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ---------------------------------------------------------------------------
# Lifespan / cleanup
# ---------------------------------------------------------------------------


def _cleanup_stale() -> None:
    """Remove leftover temp/upload directories older than 24 hours."""
    cutoff = time.time() - 86_400
    for base in (UPLOADS_DIR, TEMP_DIR, OUTPUT_DIR):
        if not base.exists():
            continue
        for child in base.iterdir():
            if child.name.startswith("."):
                continue
            try:
                if child.stat().st_mtime < cutoff:
                    if child.is_dir():
                        safe_rmtree(child)
                    else:
                        safe_unlink(child)
            except OSError:
                continue


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_directories()
    _cleanup_stale()
    logger.info("Folder Duplicator started")
    yield
    logger.info("Folder Duplicator shutting down")


app = FastAPI(title="Folder Duplicator", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _save_uploaded_folder(files: list[UploadFile], dest_root: Path) -> str:
    """Persist a browser folder upload (webkitRelativePath) under *dest_root*.

    Returns the top-level folder name.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    top_name: str | None = None

    for upload in files:
        relative = upload.filename or ""
        # Some browsers send "folder/file"; others may omit the root.
        parts = Path(relative).parts
        if not parts:
            continue
        if top_name is None:
            top_name = parts[0]
        # Strip the leading folder name — we recreate it as dest_root name
        relative_inside = Path(*parts[1:]) if len(parts) > 1 else Path(parts[-1])
        target = dest_root / relative_inside
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            with target.open("wb") as out:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
        except OSError as exc:
            logger.exception("Failed writing uploaded file %s", relative)
            raise HTTPException(status_code=500, detail=f"Failed to save upload: {exc}") from exc
        finally:
            await upload.close()

    if top_name is None:
        raise HTTPException(status_code=400, detail="Invalid folder upload")

    # Ensure dest_root itself represents the folder (files already inside)
    return top_name


def _run_duplication(job: JobState, copies: int, output_subdir: str | None) -> None:
    """Background worker: duplicate folders, zip them, update job state."""
    assert job.source_path is not None

    work_dir = TEMP_DIR / f"work_{job.job_id}"
    if work_dir.exists():
        safe_rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    job.work_dir = work_dir

    out_base = OUTPUT_DIR / (output_subdir.strip() if output_subdir else job.job_id)
    try:
        out_base = out_base.resolve()
        if not str(out_base).startswith(str(OUTPUT_DIR.resolve())):
            raise ValueError("Output path escapes allowed directory")
    except (OSError, ValueError) as exc:
        job.status = "error"
        job.error = str(exc)
        job.message = "Invalid output directory"
        return

    out_base.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()

    def cancel_check() -> bool:
        return job.cancel_requested

    def on_dup_progress(data: dict[str, Any]) -> None:
        job.status = data.get("status", "duplicating")
        job.phase = data.get("phase", "duplicating")
        job.percent = float(data.get("percent", 0))
        job.completed = int(data.get("completed", 0))
        job.total = int(data.get("total", copies))
        job.current = str(data.get("current", ""))
        job.message = str(data.get("message", ""))
        job.elapsed_seconds = float(data.get("elapsed_seconds", 0))
        job.speed = float(data.get("speed", 0))
        job.eta_seconds = float(data.get("eta_seconds", 0))

    try:
        free = get_free_disk_space(out_base)
        needed = estimate_output_size(job.original_size, copies)
        # Leave headroom for ZIP (~ratio of total)
        needed_with_zip = needed + int(needed * job.compression_ratio)
        if free and needed_with_zip > free:
            job.status = "error"
            job.error = (
                f"Insufficient disk space. Need ~{format_bytes(needed_with_zip)}, "
                f"have {format_bytes(free)}."
            )
            job.message = job.error
            return

        job.status = "duplicating"
        job.phase = "duplicating"
        job.total = copies
        job.message = "Starting duplication..."

        duplicate_folders(
            job.source_path,
            work_dir,
            job.folder_name,
            copies,
            max_workers=8,
            progress_callback=on_dup_progress,
            cancel_check=cancel_check,
        )

        if job.cancel_requested:
            raise InterruptedError("Cancelled")

        # Move duplicated folders to output
        for child in work_dir.iterdir():
            target = out_base / child.name
            if target.exists():
                safe_rmtree(target)
            shutil.move(str(child), str(target))

        job.status = "compressing"
        job.phase = "compressing"
        job.percent = 0
        job.message = "Compressing output..."
        job.current = "Output.zip"

        zip_path = out_base / "Output.zip"
        if zip_path.exists():
            zip_path.unlink()

        def on_zip_progress(percent: float, message: str) -> None:
            job.percent = round(percent, 1)
            job.message = message
            job.elapsed_seconds = round(time.monotonic() - start, 2)

        # Zip duplicated folders only (exclude Output.zip itself)
        create_zip(
            out_base,
            zip_path,
            progress_callback=on_zip_progress,
            cancel_check=cancel_check,
        )

        job.zip_path = zip_path
        job.status = "completed"
        job.phase = "completed"
        job.percent = 100
        job.completed = copies
        job.message = "ZIP Created Successfully"
        job.elapsed_seconds = round(time.monotonic() - start, 2)
        job.eta_seconds = 0
        logger.info("Job %s completed", job.job_id)

    except InterruptedError:
        job.status = "cancelled"
        job.phase = "cancelled"
        job.message = "Cancelled by user"
        safe_rmtree(work_dir)
        logger.info("Job %s cancelled", job.job_id)
    except FileExistsError as exc:
        job.status = "error"
        job.error = str(exc)
        job.message = str(exc)
        logger.warning("Job %s file exists: %s", job.job_id, exc)
    except OSError as exc:
        err = str(exc).lower()
        if "no space" in err or exc.errno == 28:
            job.error = "Disk full — duplication stopped."
        elif exc.errno in (13, 1):
            job.error = "Permission denied while writing files."
        else:
            job.error = f"OS error: {exc}"
        job.status = "error"
        job.message = job.error
        logger.exception("Job %s OS error", job.job_id)
    except Exception as exc:  # noqa: BLE001
        job.status = "error"
        job.error = f"Unexpected error: {exc}"
        job.message = job.error
        logger.exception("Job %s failed", job.job_id)
    finally:
        safe_rmtree(work_dir)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Serve the main dashboard."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "free_disk": format_bytes(get_free_disk_space()),
            "output_dir": str(OUTPUT_DIR),
        },
    )


@app.post("/upload")
async def upload_folder(files: list[UploadFile] = File(...)) -> JSONResponse:
    """Accept a single folder upload and return stats + job id."""
    job_id = generate_id()
    dest = UPLOADS_DIR / job_id
    dest.mkdir(parents=True, exist_ok=True)

    try:
        folder_name = await _save_uploaded_folder(files, dest)
        # dest holds the folder contents; rename for clarity on disk
        named = UPLOADS_DIR / f"{job_id}_{folder_name}"
        if named.exists():
            safe_rmtree(named)
        dest.rename(named)
        source = named

        stats = calculate_folder_stats(source)
        ratio_zip = TEMP_DIR / f"ratio_{job_id}.zip"
        ratio = measure_compression_ratio(source, ratio_zip)

        job = JobState(
            job_id=job_id,
            folder_name=folder_name,
            source_path=source,
            file_count=int(stats["file_count"]),
            folder_count=int(stats["folder_count"]),
            original_size=int(stats["total_size"]),
            compression_ratio=ratio,
            status="ready",
            message="Folder uploaded successfully",
            preview=preview_names(folder_name, 5),
        )
        jobs[job_id] = job

        free = get_free_disk_space()
        return JSONResponse(
            {
                "job_id": job_id,
                "folder_name": folder_name,
                "file_count": job.file_count,
                "folder_count": job.folder_count,
                "original_size": job.original_size,
                "original_size_display": format_bytes(job.original_size),
                "compression_ratio": round(ratio, 4),
                "free_disk": free,
                "free_disk_display": format_bytes(free),
                "preview": job.preview,
                "output_dir": str(OUTPUT_DIR),
                "message": "Folder uploaded successfully",
            }
        )
    except HTTPException:
        safe_rmtree(dest)
        raise
    except Exception as exc:  # noqa: BLE001
        safe_rmtree(dest)
        logger.exception("Upload failed")
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc


@app.post("/estimate")
async def estimate(
    job_id: str = Form(...),
    copies: str = Form(...),
) -> JSONResponse:
    """Return live size estimates for the given copy count."""
    job = get_job(job_id)

    copies_stripped = copies.strip()
    if not copies_stripped.isdigit() or int(copies_stripped) < 1:
        raise HTTPException(
            status_code=400,
            detail="Please enter a positive whole number (1 or greater).",
        )

    count = int(copies_stripped)
    total_size = estimate_output_size(job.original_size, count)
    zip_size = estimate_zip_size(job.original_size, count, job.compression_ratio)
    free = get_free_disk_space()
    needed = total_size + zip_size
    warning = None
    if free and needed > free:
        warning = (
            f"Warning: estimated need ({format_bytes(needed)}) may exceed "
            f"available disk space ({format_bytes(free)})."
        )

    names = preview_names(job.folder_name, count, limit=10)

    return JSONResponse(
        {
            "job_id": job_id,
            "copies": count,
            "folders_to_create": count,
            "estimated_size": total_size,
            "estimated_size_display": format_bytes(total_size),
            "estimated_zip_size": zip_size,
            "estimated_zip_size_display": format_bytes(zip_size),
            "free_disk": free,
            "free_disk_display": format_bytes(free),
            "preview": names,
            "warning": warning,
        }
    )


@app.post("/duplicate")
async def start_duplicate(
    background_tasks: BackgroundTasks,
    job_id: str = Form(...),
    copies: str = Form(...),
    output_subdir: str = Form(""),
) -> JSONResponse:
    """Start folder duplication + ZIP creation in the background."""
    job = get_job(job_id)

    if job.status in ("duplicating", "compressing"):
        raise HTTPException(status_code=409, detail="A duplication job is already running.")

    copies_stripped = copies.strip()
    if not copies_stripped.isdigit() or int(copies_stripped) < 1:
        raise HTTPException(
            status_code=400,
            detail="Please enter a positive whole number (1 or greater).",
        )

    count = int(copies_stripped)
    if not job.source_path or not job.source_path.exists():
        raise HTTPException(status_code=400, detail="Uploaded folder is missing. Please re-upload.")

    free = get_free_disk_space()
    needed = estimate_output_size(job.original_size, count) + estimate_zip_size(
        job.original_size, count, job.compression_ratio
    )
    if free and needed > free:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Insufficient disk space. Need ~{format_bytes(needed)}, "
                f"have {format_bytes(free)}."
            ),
        )

    job.copies = count
    job.cancel_requested = False
    job.status = "queued"
    job.phase = "queued"
    job.percent = 0
    job.completed = 0
    job.total = count
    job.error = None
    job.zip_path = None
    job.message = "Queued..."

    async def _run() -> None:
        await asyncio.to_thread(_run_duplication, job, count, output_subdir or None)

    background_tasks.add_task(_run)

    return JSONResponse(
        {
            "job_id": job_id,
            "status": "queued",
            "message": "Duplication started",
            "copies": count,
        }
    )


@app.post("/cancel/{job_id}")
async def cancel_job(job_id: str) -> JSONResponse:
    """Request cancellation of a running job."""
    job = get_job(job_id)
    if job.status not in ("queued", "duplicating", "compressing"):
        return JSONResponse({"job_id": job_id, "message": "Nothing to cancel", "status": job.status})
    job.cancel_requested = True
    job.message = "Cancellation requested..."
    return JSONResponse({"job_id": job_id, "message": "Cancellation requested", "status": job.status})


@app.get("/progress/{job_id}")
async def progress_sse(job_id: str) -> StreamingResponse:
    """Server-Sent Events stream of job progress."""
    job = get_job(job_id)

    async def event_generator():
        last_payload = ""
        idle_ticks = 0
        while True:
            payload = json.dumps(job.to_progress())
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
                idle_ticks = 0
            else:
                idle_ticks += 1
                # Keep-alive comment
                if idle_ticks % 10 == 0:
                    yield ": keepalive\n\n"

            if job.status in ("completed", "error", "cancelled"):
                yield f"data: {json.dumps(job.to_progress())}\n\n"
                break

            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/download/{job_id}")
async def download_zip(job_id: str) -> FileResponse:
    """Download the completed Output.zip for a job."""
    job = get_job(job_id)
    if not job.zip_path or not job.zip_path.exists():
        raise HTTPException(status_code=404, detail="ZIP file not ready yet.")
    return FileResponse(
        path=str(job.zip_path),
        filename="Output.zip",
        media_type="application/zip",
    )


@app.get("/disk")
async def disk_info() -> JSONResponse:
    """Return current free disk space for the output volume."""
    free = get_free_disk_space()
    return JSONResponse({"free_disk": free, "free_disk_display": format_bytes(free), "output_dir": str(OUTPUT_DIR)})


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Never crash the app — return a JSON error for unexpected exceptions."""
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(status_code=500, content={"detail": f"Unexpected server error: {exc}"})
