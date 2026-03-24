# Similarity Reindex Process

This document explains how the embedding reindex process works, how to monitor progress, and how to view detailed log messages.

## Overview

The reindex process computes perceptual hashes (pHash) and CLIP neural network embeddings for all media files in your library. These are used for:

- **Duplicate detection** — pHash-based hamming distance comparison
- **Content search** — text-to-image search via CLIP text encoder
- **Similarity search** — find visually similar images via CLIP embeddings

## Architecture

The reindex runs as a **separate subprocess** (not a thread) to isolate GPU memory usage and avoid blocking the UI:

```
┌─────────────────┐           JSON files            ┌─────────────────────┐
│  Metascan GUI   │◄──── progress_embedding.json ───│  embedding_worker   │
│  (main process) │                                  │  (subprocess)       │
│                 │──── embedding_task.json ────────►│                     │
│                 │──── cancel_embedding.signal ────►│                     │
└─────────────────┘                                  └─────────────────────┘
```

Communication is via JSON files in `data/similarity/`:

| File | Direction | Purpose |
|------|-----------|---------|
| `embedding_task.json` | GUI → Worker | Task configuration (model, device, file list) |
| `progress_embedding.json` | Worker → GUI | Progress updates (current/total, status, errors) |
| `cancel_embedding.signal` | GUI → Worker | Cancellation request (file existence = cancel) |

## How to Start a Reindex

1. Open **Tools → Similarity Settings**
2. Select your CLIP model size and device
3. Click **Build Index** (new files only) or **Rebuild All** (full reindex)

### CLIP Model Sizes

| Model | Embedding Dim | VRAM | Speed | Quality |
|-------|--------------|------|-------|---------|
| small (ViT-B-16) | 512 | ~600 MB | ~200 img/s GPU | Good |
| medium (ViT-L-14) | 768 | ~1.8 GB | ~100 img/s GPU | Better |
| large (ViT-H-14) | 1024 | ~4 GB | ~50 img/s GPU | Best |

CPU mode is ~10-20x slower than GPU.

## Monitoring Progress

### In the UI

The Similarity Settings dialog shows:
- **Progress bar** with current/total file count
- **Status text** showing the current phase and any error counts
- Status phases: `Starting → Loading model → Processing → Complete`

### Log Files

The primary log file is:

```
logs/embedding_worker.log
```

This is relative to your project root (development) or `~/.metascan/logs/` (installed).

The worker log contains:
- Startup information (PID, Python version, platform)
- Model loading details (model name, device, download status)
- Progress every 100 files with processing rate and ETA
- Per-file errors (missing files, ffprobe failures, etc.)
- Final summary (processed, embedded, skipped, errors)

**Example log output:**
```
2026-03-22 15:08:19 - embedding_worker - INFO - ============================================================
2026-03-22 15:08:19 - embedding_worker - INFO - Embedding worker starting
2026-03-22 15:08:19 - embedding_worker - INFO - Task: 13775 files, model=large, device=auto, phash=True, keyframes=4
2026-03-22 15:08:57 - metascan.core.embedding_manager - INFO - CLIP model loaded successfully on cuda
2026-03-22 15:09:48 - embedding_worker - INFO - Progress: 100/13775 (100 embedded, 0 skipped, 0 errors) [1.8 files/sec, ETA 126m]
2026-03-22 15:10:29 - embedding_worker - INFO - Progress: 200/13775 (200 embedded, 0 skipped, 0 errors) [2.4 files/sec, ETA 94m]
```

### Viewing Logs in Real Time

To follow the log file as the worker runs:

**Linux/WSL:**
```bash
tail -f logs/embedding_worker.log
```

**Windows PowerShell:**
```powershell
Get-Content logs\embedding_worker.log -Wait -Tail 20
```

## Progress File Format

You can also inspect the raw progress file at `data/similarity/progress_embedding.json`:

```json
{
  "current": 1500,
  "total": 13775,
  "status": "processing",
  "current_file": "image_001.png",
  "error": "",
  "errors_count": 3,
  "last_error": "broken_video.mp4: ffprobe error",
  "timestamp": 1774271437.48
}
```

Status values: `starting`, `loading_model`, `downloading_model`, `processing`, `complete`, `cancelled`, `error`

## Troubleshooting

### "Worker exited unexpectedly"

The subprocess crashed. Check `logs/embedding_worker.log` for the stack trace. Common causes:
- **`No module named 'open_clip'`** — Dependencies not installed. Run `pip install -r requirements.txt`
- **CUDA out of memory** — Switch to a smaller model or use CPU mode
- **Import errors** — The worker runs in a separate Python process; ensure your virtualenv is active

### UI appears frozen / progress bar stuck

This was caused by a Windows file-locking race condition (now fixed with retry logic). If you still see this:

1. Check if the worker is still running: look for recent writes in `logs/embedding_worker.log`
2. Check `data/similarity/progress_embedding.json` — is the timestamp recent?
3. If the worker died, click **Cancel** to reset the UI, then try again

### Per-file errors (missing files, ffprobe failures)

These are non-fatal. The worker logs each error and continues. Common causes:
- **File not found** — File was deleted/moved after scanning but before indexing
- **ffprobe error** — Corrupted or unsupported video format. Requires ffmpeg installed
- **PIL errors** — Corrupted image files

The error count is shown in the progress status text and logged every 100 files.

### Model download takes a long time

On first use, CLIP weights are downloaded from Hugging Face:
- small: ~400 MB
- medium: ~900 MB
- large: ~4 GB

Downloads are cached in `~/.cache/huggingface/`. Subsequent runs load from cache.
Progress is shown as "Downloading model..." in the UI, and detailed HTTP requests appear in the log file.

### Cancellation

Click **Cancel** in the settings dialog or close the application. The worker:
1. Detects the cancel signal file
2. Saves all progress made so far (FAISS index + database)
3. Exits cleanly

On the next **Build Index** run, only unembedded files will be processed (incremental).

## Data Files

All similarity data is stored in `data/similarity/`:

| File | Purpose |
|------|---------|
| `faiss_index.bin` | FAISS vector index (binary) |
| `id_mapping.json` | Maps FAISS integer IDs to file paths |
| `index_meta.json` | Index metadata (model, dimension, count) |
| `progress_embedding.json` | Last progress update from worker |

The database table `media_hashes` in `data/metascan.db` tracks:
- `phash` — perceptual hash hex string
- `clip_model` — which CLIP model was used
- `has_embedding` — whether the file has a CLIP embedding in the FAISS index
