# Architecture

[← Back to README](../README.md)

## Client–Server Architecture

```
Browser (Vue 3 SPA)              Backend (FastAPI)
  ├── Pinia stores       ←REST→  ├── API routes
  ├── WebSocket          ←WS───→ ├── WebSocket manager (multiplexed channels)
  └── Components                 ├── Services (async wrappers via asyncio.to_thread)
                                 ├── Core modules (scanner, DB, embeddings, hardware)
                                 ├── Workers (subprocesses: upscale, embedding, inference)
                                 └── SQLite database
```

- **Frontend** is a thin client handling display and user interaction.
- **Backend** handles all heavy processing: scanning, AI embeddings, upscaling, duplicate detection.
- **WebSocket** provides real-time progress for scans, upscaling, file watcher events, model load/download status, and folder cross-tab sync.
- **Subprocess workers** isolate long-running AI tasks from the main server process — embedding generation (`embedding_worker.py`), live CLIP inference (`inference_worker.py`), upscaling (`upscale_worker.py`).
- **Two CLIP processes** by design: a one-shot batch worker computes embeddings + tags for unembedded files and exits; a long-running inference worker speaks NDJSON over stdio for live text/image queries. They never share state.

## Database Structure

SQLite with WAL mode and a `threading.Lock` over a single connection.

- **`media`** — serialized Media object as JSON, plus favorite status and playback speed.
- **`indices`** — inverted index for fast filtering (`source`, `model`, `extension`, `path`, `tag`, `prompt`, `lora`). Tag rows carry a `source` column (`'prompt'` / `'clip'` / `'both'`) so CLIP-derived tags survive rescans.
- **`media_hashes`** — perceptual hashes and CLIP embedding status.
- **`folders`** — `(id, kind ∈ {manual,smart}, name, icon, rules JSON, sort_order, created_at, updated_at)`.
- **`folder_items`** — `(folder_id, file_path, added_at)` with `ON DELETE CASCADE` on both sides.

Covering indexes (`idx_media_summary_added`, `idx_media_summary_modified`) include every column read by the grid list endpoint, which is the reason `/api/media` returns in ~6 ms instead of ~25 s on large libraries. One-shot data migrations are gated on `PRAGMA user_version`.

## Backend Layout

```
backend/
  api/         # Route handlers (media, scan, filters, folders, similarity, upscale,
               #   duplicates, config, embeddings, models, websocket)
  services/    # Async wrappers (asyncio.to_thread) — folders_service, media_service
  ws/          # WebSocket connection manager with channel multiplexing
  main.py      # App factory, CORS, auth middleware, lifespan (CLIP preload, inference client)
```

DB access is synchronous; async wrappers in `backend/services/` bridge to FastAPI via `asyncio.to_thread()`.

## Frontend Layout

```
frontend/src/
  api/          # Fetch wrapper with auth, typed API functions
  stores/       # Pinia stores (media, filters, folders, settings, scan,
                #   similarity, upscale, models)
  composables/  # useWebSocket (multiplexed), useKeyboard, useFoldersUi, useToast
  components/
    layout/     # AppHeader, ContentSearchBar, ViewMenubar, ThreePanel, ScopeBreadcrumb, ToastHost
    filters/    # FilterPanel, FilterSection, FoldersSection, FolderRow, FolderKebabMenu
    thumbnails/ # ThumbnailGrid (virtual scroll), ThumbnailCard, SimilarityBanner
    metadata/   # MetadataPanel, MetadataField
    viewer/     # MediaViewer, ImageViewer, VideoPlayer, SlideshowViewer
    dialogs/    # ScanDialog, SimilaritySettings, DuplicateFinder,
                #   UpscaleDialog, UpscaleQueue, ConfigDialog (+ ConfigModelsTab),
                #   NewFolderDialog, SmartFolderEditor
  types/        # TypeScript interfaces (Media, FilterData, WsMessage, folders, hardware)
```

The Vite proxy forwards `/api/*` and `/ws` to the backend during development.

## Key Decisions

- **No UI framework in Python.** The legacy PyQt6 desktop UI was removed; the web stack is the only UI. Backend Python must never import `PyQt6` or `qt_material`.
- **Background workers use subprocesses, not threads** — avoids GIL contention with heavy AI workloads, and isolates crashes (a segfault in pillow_heif or libheif takes down the worker, not the server).
- **FastAPI uses `lifespan`** (not the deprecated `@app.on_event`). The lifespan constructs the `InferenceClient` singleton, injects `HF_TOKEN`, and optionally preloads CLIP for the current model.
- **Smart-folder evaluator is synchronous and client-side.** Rules are a JSON blob evaluated per Media in the Pinia store. Tag conditions fetch only the referenced tag keys via `POST /api/filters/tag_paths` — never bulk-GET the entire inverted index.
- **DELETE endpoints return `{status: "deleted"}` (not 204).** The frontend `request<T>` wrapper calls `res.json()` on every response.
