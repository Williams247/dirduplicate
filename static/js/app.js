/**
 * Folder Duplicator — client application
 */

(() => {
  "use strict";

  /** @type {string|null} */
  let jobId = null;
  /** @type {EventSource|null} */
  let eventSource = null;
  /** @type {number|null} */
  let estimateTimer = null;
  let isBusy = false;

  const $ = (id) => document.getElementById(id);

  const els = {
    dropZone: $("dropZone"),
    folderInput: $("folderInput"),
    uploadStatus: $("uploadStatus"),
    statFolderName: $("statFolderName"),
    statFileCount: $("statFileCount"),
    statOriginalSize: $("statOriginalSize"),
    statFreeDisk: $("statFreeDisk"),
    copiesInput: $("copiesInput"),
    copiesError: $("copiesError"),
    outputSubdir: $("outputSubdir"),
    outputDirPath: $("outputDirPath"),
    estFolders: $("estFolders"),
    estSize: $("estSize"),
    estZip: $("estZip"),
    diskWarning: $("diskWarning"),
    namePreview: $("namePreview"),
    dupForm: $("dupForm"),
    duplicateBtn: $("duplicateBtn"),
    cancelBtn: $("cancelBtn"),
    downloadBtn: $("downloadBtn"),
    statusLabel: $("statusLabel"),
    progressBar: $("progressBar"),
    progressFill: $("progressFill"),
    progressPct: $("progressPct"),
    progressCount: $("progressCount"),
    metricCurrent: $("metricCurrent"),
    metricSpeed: $("metricSpeed"),
    metricElapsed: $("metricElapsed"),
    metricEta: $("metricEta"),
    themeToggle: $("themeToggle"),
    themeLabel: $("themeLabel"),
    toastHost: $("toastHost"),
  };

  // ---------------------------------------------------------------------------
  // Theme
  // ---------------------------------------------------------------------------

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("fd-theme", theme);
    els.themeLabel.textContent = theme === "dark" ? "Light" : "Dark";
  }

  const savedTheme =
    localStorage.getItem("fd-theme") ||
    (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  applyTheme(savedTheme);

  els.themeToggle.addEventListener("click", () => {
    const next =
      document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
  });

  // ---------------------------------------------------------------------------
  // Toasts
  // ---------------------------------------------------------------------------

  function toast(message, type = "success") {
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = message;
    els.toastHost.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      setTimeout(() => el.remove(), 280);
    }, 4200);
  }

  // ---------------------------------------------------------------------------
  // Validation
  // ---------------------------------------------------------------------------

  function validateCopies(raw) {
    const value = String(raw ?? "").trim();
    if (value === "") {
      return { ok: false, message: "Enter how many copies you need." };
    }
    if (!/^\d+$/.test(value)) {
      return { ok: false, message: "Only positive whole numbers are allowed (no letters or decimals)." };
    }
    const n = Number(value);
    if (n < 1) {
      return { ok: false, message: "Copies must be at least 1." };
    }
    return { ok: true, value: n };
  }

  function showCopiesError(message) {
    if (message) {
      els.copiesError.hidden = false;
      els.copiesError.textContent = message;
      els.copiesInput.setAttribute("aria-invalid", "true");
    } else {
      els.copiesError.hidden = true;
      els.copiesError.textContent = "";
      els.copiesInput.removeAttribute("aria-invalid");
    }
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function formatDuration(seconds) {
    if (seconds == null || Number.isNaN(seconds) || seconds < 0) return "—";
    const total = Math.round(seconds);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h) return `${h}h ${m}m ${s}s`;
    if (m) return `${m}m ${s}s`;
    return `${s}s`;
  }

  function setProgress(percent, completed, total) {
    const pct = Math.max(0, Math.min(100, Number(percent) || 0));
    els.progressFill.style.width = `${pct}%`;
    els.progressPct.textContent = `${pct.toFixed(pct % 1 ? 1 : 0)}%`;
    els.progressCount.textContent = `${completed ?? 0} / ${total ?? 0} folders`;
    els.progressBar.setAttribute("aria-valuenow", String(Math.round(pct)));
  }

  function setBusy(busy) {
    isBusy = busy;
    els.duplicateBtn.disabled = busy || !jobId;
    els.cancelBtn.disabled = !busy;
    els.copiesInput.disabled = busy || !jobId;
    els.outputSubdir.disabled = busy || !jobId;
    els.folderInput.disabled = busy;
    els.progressFill.classList.toggle("is-active", busy);
  }

  function renderPreview(names) {
    els.namePreview.innerHTML = "";
    if (!names || !names.length) {
      const li = document.createElement("li");
      li.className = "muted";
      li.textContent = "Upload a folder to see renamed previews";
      els.namePreview.appendChild(li);
      return;
    }
    names.forEach((name) => {
      const li = document.createElement("li");
      li.textContent = name;
      els.namePreview.appendChild(li);
    });
  }

  function closeEventSource() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  }

  // ---------------------------------------------------------------------------
  // Upload
  // ---------------------------------------------------------------------------

  async function uploadFolder(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) {
      toast("No files found in the selected folder.", "error");
      return;
    }

    // Infer top-level folder name from webkitRelativePath
    const firstPath = files[0].webkitRelativePath || files[0].name;
    const folderName = firstPath.split("/")[0] || "folder";

    els.dropZone.classList.add("is-uploading");
    els.statusLabel.textContent = `Uploading “${folderName}”…`;
    setProgress(0, 0, 0);

    const form = new FormData();
    files.forEach((file) => {
      // Preserve relative path so the server can rebuild the tree
      const rel = file.webkitRelativePath || `${folderName}/${file.name}`;
      form.append("files", file, rel);
    });

    try {
      const res = await fetch("/upload", { method: "POST", body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || "Upload failed");
      }

      jobId = data.job_id;
      els.uploadStatus.hidden = false;
      els.statFolderName.textContent = data.folder_name;
      els.statFileCount.textContent = Number(data.file_count).toLocaleString();
      els.statOriginalSize.textContent = data.original_size_display;
      els.statFreeDisk.textContent = data.free_disk_display;
      if (data.output_dir) {
        els.outputDirPath.textContent = data.output_dir;
      }

      els.copiesInput.disabled = false;
      els.outputSubdir.disabled = false;
      els.duplicateBtn.disabled = false;
      els.downloadBtn.hidden = true;
      els.downloadBtn.removeAttribute("href");

      renderPreview(data.preview);
      els.statusLabel.textContent = "Folder ready — set copy count and duplicate.";
      toast(`Uploaded “${data.folder_name}” (${data.file_count} files)`);

      if (els.copiesInput.value) {
        scheduleEstimate();
      }
    } catch (err) {
      console.error(err);
      toast(err.message || "Upload failed", "error");
      els.statusLabel.textContent = "Upload failed.";
    } finally {
      els.dropZone.classList.remove("is-uploading");
    }
  }

  // Drag & drop — browsers only expose FileSystemDirectoryEntry via DataTransferItem
  async function handleDrop(event) {
    event.preventDefault();
    els.dropZone.classList.remove("is-dragging");
    if (isBusy) return;

    const items = event.dataTransfer?.items;
    if (items && items.length) {
      const entry = items[0].webkitGetAsEntry?.();
      if (entry && entry.isDirectory) {
        try {
          const files = await readDirectoryRecursive(entry);
          await uploadFolder(files);
          return;
        } catch (err) {
          console.error(err);
          toast(err.message || "Could not read dropped folder", "error");
          return;
        }
      }
    }

    // Fallback: if files were dropped with paths
    const files = event.dataTransfer?.files;
    if (files && files.length) {
      await uploadFolder(files);
      return;
    }

    toast("Please drop a folder (not individual files).", "error");
  }

  /**
   * Recursively read a DirectoryEntry into File objects with webkitRelativePath.
   * @param {FileSystemDirectoryEntry} dirEntry
   * @param {string} pathPrefix
   * @returns {Promise<File[]>}
   */
  function readDirectoryRecursive(dirEntry, pathPrefix = "") {
    return new Promise((resolve, reject) => {
      const reader = dirEntry.createReader();
      const collected = [];
      const prefix = pathPrefix ? `${pathPrefix}/${dirEntry.name}` : dirEntry.name;

      const readBatch = () => {
        reader.readEntries(async (entries) => {
          if (!entries.length) {
            resolve(collected);
            return;
          }
          try {
            for (const entry of entries) {
              if (entry.isFile) {
                const file = await new Promise((res, rej) =>
                  entry.file(res, rej)
                );
                // Attach relative path for the server
                Object.defineProperty(file, "webkitRelativePath", {
                  value: `${prefix}/${entry.name}`,
                  configurable: true,
                });
                collected.push(file);
              } else if (entry.isDirectory) {
                const nested = await readDirectoryRecursive(entry, prefix);
                collected.push(...nested);
              }
            }
            readBatch(); // continue until empty batch
          } catch (err) {
            reject(err);
          }
        }, reject);
      };

      readBatch();
    });
  }

  els.dropZone.addEventListener("dragenter", (e) => {
    e.preventDefault();
    els.dropZone.classList.add("is-dragging");
  });
  els.dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    els.dropZone.classList.add("is-dragging");
  });
  els.dropZone.addEventListener("dragleave", (e) => {
    if (!els.dropZone.contains(e.relatedTarget)) {
      els.dropZone.classList.remove("is-dragging");
    }
  });
  els.dropZone.addEventListener("drop", handleDrop);

  els.dropZone.addEventListener("click", (e) => {
    if (e.target.closest("label") || e.target === els.folderInput) return;
    els.folderInput.click();
  });
  els.dropZone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      els.folderInput.click();
    }
  });

  els.folderInput.addEventListener("change", () => {
    if (els.folderInput.files?.length) {
      uploadFolder(els.folderInput.files);
    }
  });

  // ---------------------------------------------------------------------------
  // Estimate
  // ---------------------------------------------------------------------------

  async function runEstimate() {
    if (!jobId) return;
    const check = validateCopies(els.copiesInput.value);
    if (!check.ok) {
      showCopiesError(check.message);
      els.estFolders.textContent = "0";
      els.estSize.textContent = "0 B";
      els.estZip.textContent = "0 B";
      els.diskWarning.hidden = true;
      return;
    }
    showCopiesError(null);

    const form = new FormData();
    form.append("job_id", jobId);
    form.append("copies", String(check.value));

    try {
      const res = await fetch("/estimate", { method: "POST", body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showCopiesError(data.detail || "Invalid copy count");
        return;
      }
      els.estFolders.textContent = Number(data.folders_to_create).toLocaleString();
      els.estSize.textContent = data.estimated_size_display;
      els.estZip.textContent = data.estimated_zip_size_display;
      els.statFreeDisk.textContent = data.free_disk_display;
      renderPreview(data.preview);

      if (data.warning) {
        els.diskWarning.hidden = false;
        els.diskWarning.textContent = data.warning;
      } else {
        els.diskWarning.hidden = true;
      }
    } catch (err) {
      console.error(err);
    }
  }

  function scheduleEstimate() {
    clearTimeout(estimateTimer);
    estimateTimer = setTimeout(runEstimate, 180);
  }

  els.copiesInput.addEventListener("input", scheduleEstimate);
  els.copiesInput.addEventListener("change", scheduleEstimate);

  // ---------------------------------------------------------------------------
  // Progress (SSE)
  // ---------------------------------------------------------------------------

  function subscribeProgress(id) {
    closeEventSource();
    eventSource = new EventSource(`/progress/${id}`);

    eventSource.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }

      const statusText = {
        queued: "Queued…",
        duplicating: "Duplicating…",
        compressing: "Compressing…",
        completed: "Completed",
        cancelled: "Cancelled",
        error: "Error",
      };

      els.statusLabel.textContent = data.message || statusText[data.status] || data.status;
      setProgress(data.percent, data.completed, data.total);
      els.metricCurrent.textContent = data.current || "—";
      els.metricSpeed.textContent =
        data.speed > 0 ? `${data.speed} folders/s` : "—";
      els.metricElapsed.textContent = formatDuration(data.elapsed_seconds);
      els.metricEta.textContent =
        data.status === "completed" || data.status === "cancelled"
          ? "—"
          : formatDuration(data.eta_seconds);

      if (data.status === "completed") {
        setBusy(false);
        closeEventSource();
        els.downloadBtn.hidden = false;
        els.downloadBtn.href = `/download/${id}`;
        els.downloadBtn.removeAttribute("aria-disabled");
        els.statusLabel.textContent = "ZIP Created Successfully";
        toast("Duplication complete — ZIP is ready to download.");
      } else if (data.status === "error") {
        setBusy(false);
        closeEventSource();
        toast(data.error || data.message || "Duplication failed", "error");
      } else if (data.status === "cancelled") {
        setBusy(false);
        closeEventSource();
        toast("Duplication cancelled.", "error");
      }
    };

    eventSource.onerror = () => {
      // Browser will retry; if job finished we close on terminal states above
    };
  }

  // ---------------------------------------------------------------------------
  // Duplicate / Cancel
  // ---------------------------------------------------------------------------

  els.dupForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!jobId || isBusy) return;

    const check = validateCopies(els.copiesInput.value);
    if (!check.ok) {
      showCopiesError(check.message);
      toast(check.message, "error");
      return;
    }
    showCopiesError(null);

    // Confirm if disk warning visible
    if (!els.diskWarning.hidden) {
      const proceed = window.confirm(
        `${els.diskWarning.textContent}\n\nContinue anyway?`
      );
      if (!proceed) return;
    }

    const form = new FormData();
    form.append("job_id", jobId);
    form.append("copies", String(check.value));
    form.append("output_subdir", els.outputSubdir.value.trim());

    setBusy(true);
    els.downloadBtn.hidden = true;
    setProgress(0, 0, check.value);
    els.statusLabel.textContent = "Starting duplication…";

    try {
      const res = await fetch("/duplicate", { method: "POST", body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          typeof data.detail === "string" ? data.detail : "Could not start duplication"
        );
      }
      toast(`Duplicating ${check.value} folders…`);
      subscribeProgress(jobId);
    } catch (err) {
      setBusy(false);
      toast(err.message || "Failed to start", "error");
      els.statusLabel.textContent = "Failed to start.";
    }
  });

  els.cancelBtn.addEventListener("click", async () => {
    if (!jobId || !isBusy) return;
    try {
      await fetch(`/cancel/${jobId}`, { method: "POST" });
      els.statusLabel.textContent = "Cancellation requested…";
      toast("Cancellation requested…");
    } catch (err) {
      toast("Could not cancel", "error");
    }
  });
})();
