# CLAUDE.md — Metascan

## Project Overview

Metascan is an AI-generated media browser with metadata extraction, similarity search, and AI upscaling. It uses a **client-server architecture**: a Python/FastAPI backend handles scanning, database, AI processing, and file serving, while a Vue 3 SPA provides the web UI.

## Quick Reference

```bash
# Run the app (two terminals)
source venv/bin/activate && python run_server.py   # Backend: http://localhost:8700
cd frontend && npm run dev                          # Frontend: http://localhost:5173

# Quality checks (must pass before committing)
make quality test     # flake8 + black --check + mypy + pytest (112 tests)

# Frontend only
cd frontend && npm run build    # Type-check + production build
```

## Architecture

```
metascan/
  backend/              # FastAPI server (REST API + WebSocket)
    api/                # Route handlers (media, scan, filters, similarity, upscale, duplicates, config, embeddings, models, websocket)
    services/           # Async wrappers around core modules (asyncio.to_thread)
    ws/                 # WebSocket connection manager with channel multiplexing
    main.py             # App factory, CORS, auth middleware
    config.py           # Server config from env vars, app config from config.json
    dependencies.py     # Singleton DI for DatabaseManager, ThumbnailCache

  metascan/
    core/               # Business logic (no UI dependencies)
      database_sqlite.py    # SQLite with WAL mode, threading.Lock, inverted index
      scanner.py            # Media file discovery, metadata extraction, thumbnail gen
      embedding_manager.py  # CLIP embeddings (open_clip), FAISS index manager
      embedding_queue.py    # One-shot subprocess manager for BATCH embedding (embedding_worker.py)
      inference_client.py   # Asyncio supervisor for LIVE CLIP subprocess (inference_worker.py)
      upscale_queue_process.py  # Subprocess worker management for Real-ESRGAN upscaling
      media.py              # Media dataclass with fast JSON deserialization (orjson)
      duplicate_detection.py # pHash duplicate grouping algorithm (no UI deps)
      phash_utils.py        # Perceptual hash computation
      prompt_tokenizer.py   # NLTK-based prompt keyword extraction
      vocabulary.py         # CLIP tagging vocabulary loader + encoder with .npz cache
      watcher.py            # File system monitoring (watchdog)
    extractors/         # Metadata extractors (ComfyUI, SwarmUI, Fooocus)
    cache/              # Thumbnail cache (Pillow + FFmpeg)
    workers/            # Subprocess entry points
                        #   embedding_worker.py — batch embedding + CLIP tagging
                        #   inference_worker.py — long-running CLIP NDJSON server for live queries
                        #   upscale_worker.py   — Real-ESRGAN / GFPGAN / RIFE
    utils/              # App paths, FFmpeg utils, startup profiler
    ui/                 # Legacy PyQt6 UI (not used by web stack, not type-checked)

  data/
    vocabulary/         # CLIP tagging inputs (oidv7 / imagenet / aesthetics / nsfw / excluded)
                        # plus cached encoded matrix: vocab.<model_key>.npz

  frontend/             # Vue 3 SPA
    src/
      api/              # Fetch wrapper with auth, typed API functions
      stores/           # Pinia stores (media, filters, settings, scan, similarity, upscale, models)
      composables/      # useWebSocket (multiplexed, auto-reconnect), useKeyboard
      components/
        layout/         # AppHeader, ContentSearchBar, ViewMenubar, ThreePanel (resizable splitter)
        filters/        # FilterPanel, FilterSection
        thumbnails/     # ThumbnailGrid (virtual scroll), ThumbnailCard, SimilarityBanner
        metadata/       # MetadataPanel, MetadataField
        viewer/         # MediaViewer, ImageViewer, VideoPlayer, SlideshowViewer
        dialogs/        # ScanDialog, SimilaritySettings, DuplicateFinder,
                        # UpscaleDialog, UpscaleQueue, ConfigDialog (+ ConfigModelsTab)
      types/            # TypeScript interfaces (Media, FilterData, WsMessage)

  tests/                # pytest test suite
```

## Key Technical Decisions

- **Core modules must not import PyQt6 or any UI framework.** All UI deps were removed from core/. The legacy `metascan/ui/` still uses PyQt6 but is not part of the web stack.
- **Database access is synchronous** — wrapped with `asyncio.to_thread()` in the service layer for FastAPI compatibility. The threading.Lock in DatabaseManager handles concurrency.
- **Background workers use subprocesses** (not threads) for embedding generation and upscaling, communicating via JSON files. This avoids GIL issues with heavy AI workloads.
- **Two separate CLIP subprocesses.** `embedding_worker.py` is one-shot batch (computes embeddings + CLIP tags for unembedded files and exits). `inference_worker.py` is long-running (NDJSON stdio — answers `encode_text` / `encode_image` / `encode_video` for live searches). Both load their own CLIP model; do not share state. The live path is supervised by `InferenceClient` (asyncio) installed on the FastAPI app via `lifespan`.
- **Inference worker stderr is drained** line-by-line into the server logger by `InferenceClient._stderr_loop`. Do not switch to `stderr=PIPE` without a drainer — the pipe buffer fills during model load and the worker hangs silently.
- **FastAPI uses `lifespan`** (not the deprecated `@app.on_event`). The lifespan constructs the `InferenceClient` singleton, installs it into `backend.api.similarity` via `set_inference_client`, injects `HF_TOKEN` env from `config.models.huggingface_token`, and optionally preloads CLIP for the current model if `config.models.preload_at_startup` includes `clip-<key>`.
- **Similarity endpoints call `client.ensure_started`** before `encode_*` so a search request arriving before preload still triggers a spawn rather than blocking on a ready event that will never fire.
- **Dim-mismatch guard.** Before FAISS search, `_assert_dim_matches` returns HTTP 409 `{code:"dim_mismatch", index_dim, model_dim, ...}` when the current CLIP model's embedding dim differs from the on-disk index. The frontend's `ApiError` in `client.ts` preserves `detail` so the UI can render an actionable "Rebuild index" banner.
- **HuggingFace HEAD probe suppression.** `embedding_manager._check_model_needs_download` is authoritative; when weights are cached, the loader sets `HF_HUB_OFFLINE=1` around `open_clip.create_model_and_transforms` to skip the etag revalidation.
- **Core modules use callbacks** (not PyQt signals) for event dispatch: `on_progress`, `on_complete`, `on_error`, `on_status`, `on_task_added`, etc.
- **WebSocket is multiplexed** — a single `/ws` connection carries all channels (`scan`, `upscale`, `embedding`, `watcher`, `models`) with JSON envelope `{channel, event, data}`. The `models` channel broadcasts `inference_status`, `inference_progress`, `download_progress`, `download_complete`, `download_error`.
- **Tag inverted index tracks source.** `indices.source` is one of `'prompt'` / `'clip'` / `'both'` for tag rows, NULL for other index types. `_generate_indices` emits `(type, key, source)` triples; `_update_indices` preserves CLIP-sourced tags across rescans by downgrading `'both'` → `'clip'` before rewriting prompt rows. Use `db.add_tag_indices(path, tags, source='clip')` from the embedding worker — it upserts with conflict-merge.
- **Similarity threshold is bimodal.** Image↔image uses `similarityStore.threshold` (default 0.7, slider 0-1). Text↔image uses `similarityStore.contentThreshold` (default 0.0, slider 0-0.45 with step 0.01) because CLIP text/image cosine scores live on a much lower scale. `SimilarityBanner.vue` switches range + formatting based on `isContentSearch`.
- **Vite proxy** forwards `/api/*` and `/ws` to the backend during development. The EPIPE error handler silences broken pipe from cancelled browser requests.
- **FAISS test vectors must use dim >= 32** to avoid SIMD alignment crashes on ARM (Apple Silicon). Tests normalize all vectors for IndexFlatIP.
- **`KMP_DUPLICATE_LIB_OK=TRUE`** is set in `tests/conftest.py` to prevent OpenMP duplicate library crash when torch + faiss-cpu both link libomp on macOS.

## Development Rules

### Python
- **Formatter:** `black` (v25.11.0 — must match in both requirements.txt and requirements-dev.txt)
- **Linter:** `flake8` on `metascan/ backend/ tests/` — fatal errors (E9, F63, F7, F82) must be zero; style warnings are non-fatal (`--exit-zero`)
- **Type checker:** `mypy` with `python_version = 3.11`, strict on `metascan/core/*`, `ignore_errors` on `metascan/ui/*`
- **Tests:** `pytest` — 112 tests, all must pass. UI-dependent tests are skipped via `@unittest.skipUnless(_HAS_PYQT_UI)` when PyQt6/qt_material aren't installed. `tests/test_inference_client.py` spawns a fake NDJSON worker (no CLIP required) to exercise the live-inference subprocess wiring.
- **Python version:** 3.11+ required (3.13 not supported)
- **Imports in core/:** Never import from `metascan.ui`, `PyQt6`, or `qt_material`

### Frontend
- **Vue 3** with Composition API (`<script setup>` syntax)
- **TypeScript** — strict, checked via `vue-tsc --noEmit`
- **State:** Pinia stores
- **Components:** PrimeVue (Aura theme)
- **Build:** `npm run build` runs type-check then Vite build

### CI (.github/workflows/python-package.yml)
Two parallel jobs:
1. **backend:** Install deps (including libegl1 for PyQt6 in tests), flake8, black --check, mypy, pytest
2. **frontend:** npm ci, vue-tsc --noEmit, npm run build

`make quality test` locally matches the CI backend job exactly.

## Config Files

| File | Purpose |
|------|---------|
| `config.json` | App config (directories, theme, thumbnail size, similarity settings, `models` section — see below) |
| `mypy.ini` | Type checking config (python 3.11, strict core, ignore UI) |
| `requirements.txt` | Production deps (FastAPI, torch, CLIP, FAISS, etc. — no PyQt6) |
| `requirements-dev.txt` | Dev deps (pytest, black, mypy — black version must match prod) |
| `frontend/vite.config.ts` | Vite config with proxy to backend |
| `frontend/tsconfig.app.json` | TypeScript config (noUnusedLocals/Params disabled for template refs) |

### `config.json` keys managed by the Models tab

```jsonc
{
  "models": {
    "preload_at_startup": ["clip-large"],  // model ids; read by lifespan preload loop
    "huggingface_token": ""                  // masked in UI; injected as HF_TOKEN env for subprocesses
  }
}
```

Model ids surfaced by `GET /api/models/status`: `clip-small|medium|large`, `resr-x2|x4|x4-anime`, `gfpgan-v1.4`, `rife`, `nltk-punkt|stopwords`.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `METASCAN_HOST` | `0.0.0.0` | Backend bind address |
| `METASCAN_PORT` | `8700` | Backend port |
| `METASCAN_API_KEY` | (none) | Bearer token for API auth |
| `METASCAN_CORS_ORIGINS` | `*` | Comma-separated CORS origins |

## Common Tasks

### Adding a new API endpoint
1. Create route in `backend/api/<module>.py`
2. If it needs DB access, add method to `backend/services/media_service.py` using `asyncio.to_thread()`
3. Register router in `backend/main.py`
4. Add TypeScript API function in `frontend/src/api/`

### Adding a new frontend dialog
1. Create `frontend/src/components/dialogs/MyDialog.vue`
2. Add state/open flag in `App.vue`
3. Add button/shortcut trigger in `AppHeader.vue`
4. If it needs a store, create `frontend/src/stores/my.ts`

### Adding a tag axis / extending the CLIP vocabulary
1. Drop a new `<axis>.txt` under `data/vocabulary/` (one term per line, `#` comments).
2. Register the filename in `metascan/core/vocabulary.py` (`<AXIS>_FILENAME`, `_add_all(...)` call with the axis label).
3. The cache fingerprint hashes the source files — next worker run will re-encode automatically.
4. No DB migration needed; tags still flow through `indices(index_type='tag')` with source `'clip'`.

### Adding a live inference request type
1. Extend `inference_worker.py` with a new `_handle_<type>` method + dispatch in `run()`.
2. Add a matching `async <name>(...)` helper on `InferenceClient` that calls `_request(...)`.
3. Call it from `backend/api/similarity.py` (or a new router). Reuse `_ensure_worker_ready` to keep cold starts sane.

### Running after clean checkout
```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install flake8
python setup_models.py          # NLTK data + AI models (optional)
cd frontend && npm install && cd ..
make quality test                # Verify everything works
```
