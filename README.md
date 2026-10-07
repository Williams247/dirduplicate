# Folder Duplicator

Upload a folder, generate N renamed copies in parallel, preview disk/ZIP size in real time, then download everything as a ZIP.

Folder Duplicator is a FastAPI web app for multiplying a folder tree. Upload once, choose how many copies you need, see estimated size and ZIP size update live, then duplicate with underscore-aware renaming (e.g. `joe_mead_100` → `joe1_mead1_101`). Progress streams over SSE; output is packed into `Output.zip` for download.

## Features

- Drag-and-drop folder upload
- Live storage and ZIP size estimates
- Smart folder renaming with underscore rules
- Parallel duplication via thread pool
- Real-time progress (SSE), cancel, and ETA
- Automatic ZIP creation and download
- Dark mode and responsive UI

## Requirements

- Python 3.12+
- pip

## Setup

```bash
cd file_duplicate
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Output directory limitation

Browsers cannot write to arbitrary paths on your machine. Generated folders and `Output.zip` are stored under the server’s `output/` directory (optionally in a subfolder you name in the UI). Download the ZIP from the app when the job completes.

## Renaming examples

| Original         | Copy 1             | Copy 2             |
|------------------|--------------------|--------------------|
| `pascal_babel_100`   | `pascal_babel1_101`   | `pascal_babel2_102`   |
| `project_backup` | `project1_backup1` | `project2_backup2` |

## API

| Method | Path                  | Description            |
|--------|-----------------------|------------------------|
| GET    | `/`                   | Dashboard              |
| POST   | `/upload`             | Upload folder          |
| POST   | `/estimate`           | Live size estimates    |
| POST   | `/duplicate`          | Start duplication      |
| GET    | `/progress/{job_id}`  | SSE progress stream    |
| GET    | `/download/{job_id}`  | Download Output.zip    |
| POST   | `/cancel/{job_id}`    | Cancel running job     |
