# CLAUDE.md — Metascan

## Project Overview

Metascan is an AI-generated media browser with metadata extraction, similarity search, and AI upscaling. It uses a **client-server architecture**: a Python/FastAPI backend handles scanning, database, AI processing, and file serving, while a Vue 3 SPA provides the web UI.

## Quick Reference

```bash
# Run the app (two terminals)
source venv/bin/activate && python run_server.py   # Backend: http://localhost:8700
cd frontend && npm run dev                          # Frontend: http://localhost:5173

# Quality checks (must pass before committing)
make quality test     # flake8 + black --check + mypy + pytest (85 tests)

# Frontend only
cd frontend && npm run build    # Type-check + production build
```

## Architecture

```
metascan/
  backend/              # FastAPI server (REST API + WebSocket)
    api/                # Route handlers (media, scan, filters, similarity, upscale, duplicates, config, embeddings, websocket)
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
      embedding_queue.py    # Subprocess worker management for embedding generation
      upscale_queue_process.py  # Subprocess worker management for Real-ESRGAN upscaling
      media.py              # Media dataclass with fast JSON deserialization (orjson)
      duplicate_detection.py # pHash duplicate grouping algorithm (no UI deps)
      phash_utils.py        # Perceptual hash computation
      prompt_tokenizer.py   # NLTK-based prompt keyword extraction
      watcher.py            # File system monitoring (watchdog)
    extractors/         # Metadata extractors (ComfyUI, SwarmUI, Fooocus)
    cache/              # Thumbnail cache (Pillow + FFmpeg)
    workers/            # Subprocess entry points (embedding_worker.py, upscale_worker.py)
    utils/              # App paths, FFmpeg utils, startup profiler
    ui/                 # Legacy PyQt6 UI (not used by web stack, not type-checked)

  frontend/             # Vue 3 SPA
    src/
      api/              # Fetch wrapper with auth, typed API functions
      stores/           # Pinia stores (media, filters, settings, scan, similarity, upscale)
      composables/      # useWebSocket (multiplexed, auto-reconnect), useKeyboard
      components/
        layout/         # AppHeader, ThreePanel (resizable splitter)
        filters/        # FilterPanel, FilterSection, ContentSearch
        thumbnails/     # ThumbnailGrid (virtual scroll), ThumbnailCard, SimilarityBanner
        metadata/       # MetadataPanel, MetadataField
        viewer/         # MediaViewer, ImageViewer, VideoPlayer, SlideshowViewer
        dialogs/        # ScanDialog, SimilaritySettings, DuplicateFinder, UpscaleDialog, UpscaleQueue, ConfigDialog
      types/            # TypeScript interfaces (Media, FilterData, WsMessage)

  tests/                # pytest test suite
```

## Key Technical Decisions

- **Core modules must not import PyQt6 or any UI framework.** All UI deps were removed from core/. The legacy `metascan/ui/` still uses PyQt6 but is not part of the web stack.
- **Database access is synchronous** — wrapped with `asyncio.to_thread()` in the service layer for FastAPI compatibility. The threading.Lock in DatabaseManager handles concurrency.
- **Background workers use subprocesses** (not threads) for embedding generation and upscaling, communicating via JSON files. This avoids GIL issues with heavy AI workloads.
- **Core modules use callbacks** (not PyQt signals) for event dispatch: `on_progress`, `on_complete`, `on_error`, `on_task_added`, etc.
- **WebSocket is multiplexed** — a single `/ws` connection carries all channels (scan, upscale, embedding, watcher) with JSON envelope `{channel, event, data}`.
- **Vite proxy** forwards `/api/*` and `/ws` to the backend during development. The EPIPE error handler silences broken pipe from cancelled browser requests.
- **FAISS test vectors must use dim >= 32** to avoid SIMD alignment crashes on ARM (Apple Silicon). Tests normalize all vectors for IndexFlatIP.
- **`KMP_DUPLICATE_LIB_OK=TRUE`** is set in `tests/conftest.py` to prevent OpenMP duplicate library crash when torch + faiss-cpu both link libomp on macOS.

## Development Rules

### Python
- **Formatter:** `black` (v25.11.0 — must match in both requirements.txt and requirements-dev.txt)
- **Linter:** `flake8` on `metascan/ backend/ tests/` — fatal errors (E9, F63, F7, F82) must be zero; style warnings are non-fatal (`--exit-zero`)
- **Type checker:** `mypy` with `python_version = 3.11`, strict on `metascan/core/*`, `ignore_errors` on `metascan/ui/*`
- **Tests:** `pytest` — 85 tests, all must pass. UI-dependent tests are skipped via `@unittest.skipUnless(_HAS_PYQT_UI)` when PyQt6/qt_material aren't installed
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
| `config.json` | App config (directories, theme, thumbnail size, similarity settings) |
| `mypy.ini` | Type checking config (python 3.11, strict core, ignore UI) |
| `requirements.txt` | Production deps (FastAPI, torch, CLIP, FAISS, etc. — no PyQt6) |
| `requirements-dev.txt` | Dev deps (pytest, black, mypy — black version must match prod) |
| `frontend/vite.config.ts` | Vite config with proxy to backend |
| `frontend/tsconfig.app.json` | TypeScript config (noUnusedLocals/Params disabled for template refs) |

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
