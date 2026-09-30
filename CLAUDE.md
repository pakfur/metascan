# CLAUDE.md — Metascan

## Project Overview

Metascan is an AI-generated media browser with metadata extraction, similarity search, and AI upscaling. It uses a **client-server architecture**: a Python/FastAPI backend handles scanning, database, AI processing, and file serving, while a Vue 3 SPA provides the web UI.

## Quick Reference

```bash
# Run the app (two terminals)
source venv/bin/activate && python run_server.py   # Backend: http://localhost:8700
cd frontend && npm run dev                          # Frontend: http://localhost:5173

# Quality checks (must pass before committing)
make quality test     # flake8 + black --check + mypy + pytest

# Frontend only
cd frontend && npm run build    # Type-check + production build
```

## Key Technical Decisions

- **No UI framework in Python code.** The legacy PyQt6 desktop UI (`metascan/ui/`, `main.py`, `run.bat`, `build_app.py`, `metascan.spec`) was removed; the web stack (Vue 3 + FastAPI) is the only UI. Backend Python must never import `PyQt6` or `qt_material`.
- **Database access is synchronous** — wrapped with `asyncio.to_thread()` in the service layer for FastAPI compatibility. The threading.Lock in DatabaseManager handles concurrency.
- **Background workers use subprocesses** (not threads) for embedding generation and upscaling, communicating via JSON files. This avoids GIL issues with heavy AI workloads.
- **Two separate CLIP subprocesses.** `embedding_worker.py` is one-shot batch (computes embeddings + CLIP tags for unembedded files and exits). `inference_worker.py` is long-running (NDJSON stdio — answers `encode_text` / `encode_image` / `encode_video` for live searches). Both load their own CLIP model; do not share state. The live path is supervised by `InferenceClient` (asyncio) installed on the FastAPI app via `lifespan`.
- **Inference worker stderr is drained** line-by-line into the server logger by `InferenceClient._stderr_loop`. Do not switch to `stderr=PIPE` without a drainer — the pipe buffer fills during model load and the worker hangs silently.
- **FastAPI uses `lifespan`** (not the deprecated `@app.on_event`). The lifespan constructs the `InferenceClient` singleton, installs it into `backend.api.similarity` via `set_inference_client`, injects `HF_TOKEN` env from `config.models.huggingface_token`, and optionally preloads CLIP for the current model if `config.models.preload_at_startup` includes `clip-<key>`.
- **Similarity endpoints call `client.ensure_started`** before `encode_*` so a search request arriving before preload still triggers a spawn rather than blocking on a ready event that will never fire.
- **Dim-mismatch guard.** Before FAISS search, `_assert_dim_matches` returns HTTP 409 `{code:"dim_mismatch", index_dim, model_dim, ...}` when the current CLIP model's embedding dim differs from the on-disk index. The frontend's `ApiError` in `client.ts` preserves `detail` so the UI can render an actionable "Rebuild index" banner.
- **HuggingFace HEAD probe suppression.** `embedding_manager._check_model_needs_download` is authoritative; when weights are cached, the loader sets `HF_HUB_OFFLINE=1` around `open_clip.create_model_and_transforms` to skip the etag revalidation.
- **Core modules use callbacks** for event dispatch: `on_progress`, `on_complete`, `on_error`, `on_status`, `on_task_added`, etc.
- **WebSocket is multiplexed** — a single `/ws` connection carries all channels (`scan`, `upscale`, `embedding`, `watcher`, `models`, `folders`, `comfy`, `storyboard`, `i2v`, `t2i`) with JSON envelope `{channel, event, data}`. The `models` channel broadcasts `inference_status`, `inference_progress`, `download_progress`, `download_complete`, `download_error`. The `folders` channel broadcasts `folder_created` / `folder_updated` / `folder_deleted` / `folder_items_changed` for cross-tab sync. The `storyboard` channel broadcasts `synthesis_progress` (`{storyboard_id, panel_id, beat_id, done, total, prompt_source}`), `synthesis_complete` (`{storyboard_id, synthesized, fallback, skipped_locked}`), `synthesis_error` (`{storyboard_id, error}`), and `beat_images_changed` (`{storyboard_id, panel_id, beat_id, files}`) from `StoryboardRunner`'s own `on_event` callback — the `folder_created` / `folder_items_changed` events it also emits go out on the `folders` channel, not `storyboard`. `POST /api/storyboard/{id}/synthesize` is 202 fire-and-forget (`asyncio.create_task`); `synthesis_complete`/`synthesis_error` are the only signal a client gets that the background run actually finished or died — `StoryboardRunner.synthesize` wraps the real work and always emits exactly one of the two, re-raising after `synthesis_error` so a direct (non-route) caller still sees the exception.
- **Tag inverted index tracks source.** `indices.source` is one of `'prompt'` / `'clip'` / `'both'` for tag rows, NULL for other index types. `_generate_indices` emits `(type, key, source)` triples; `_update_indices` preserves CLIP-sourced tags across rescans by downgrading `'both'` → `'clip'` before rewriting prompt rows. Use `db.add_tag_indices(path, tags, source='clip')` from the embedding worker — it upserts with conflict-merge.
- **Folders persist via `/api/folders`.** Two tables: `folders(id, kind ∈ {manual,smart}, name, icon, rules JSON, sort_order, created_at, updated_at)` and `folder_items(folder_id, file_path, added_at)` with `ON DELETE CASCADE` on both sides. The frontend Pinia store (`stores/folders.ts`) does optimistic local updates with API-backed persistence and rolls back on failure. The `folders` WS channel broadcasts every mutation so other tabs stay in sync. A one-shot localStorage → API import runs on first load when the server returns empty; guarded by a localStorage flag.
- **`modified_at` / `created_at` carry two historical shapes.** New rows write an ISO-8601 (or SQL-timestamp) string; rows back-filled from the pre-existing `Media` JSON blob hold a unix-epoch float stringified (e.g. `"1775691760.0"`). The smart-folder "Modified" / "Added" evaluator tries `Number(raw)` first (epoch seconds × 1000) and falls back to `Date.parse`. Both backend and frontend must tolerate either shape.
- **`save_media` uses a true upsert** — `INSERT … ON CONFLICT(file_path) DO UPDATE SET …`, **not** `INSERT OR REPLACE`. The latter is DELETE+INSERT under the hood, which re-fires `created_at`'s `DEFAULT CURRENT_TIMESTAMP` every rescan and collapsed the "Added" smart-folder rule onto a single date. The ON CONFLICT path preserves the original ingest time across rescans.
- **Covering indexes must include every SELECT column.** `idx_media_summary_added` and `idx_media_summary_modified` back the grid list endpoint and are the reason `/api/media` returns in ~6 ms instead of ~25 s (the `data` JSON blob has 700+ MB of overflow pages on large libraries). When you add a new column to the summary SELECT, extend both indexes. `_init_database` rebuilds any index whose DDL is missing a currently-required column by reading `sqlite_master`.
- **One-shot data migrations are gated on `PRAGMA user_version`.** e.g. `user_version = 1` is the "`created_at` backfilled from `modified_at` on existing rows" migration; `user_version = 2` is the thumbnail-cache wipe for EXIF-orientation handling; `user_version = 3` is the shot/beat model reorganization — drops and recreates `panels`/`beats`/`panel_images→beat_images` (dev data is disposable by decision, no column copying), first unhiding media the old `panel_images` table left hidden and deleting `generation_jobs` rows with a non-NULL `panel_id` so a restart can't re-adopt jobs for panels that no longer exist. Bump the version when adding new backfills; the gate prevents re-running and silently double-writing on every launch.
- **DELETE endpoints return `{status: "deleted"}` (not 204).** The frontend `request<T>` wrapper in `api/client.ts` calls `res.json()` on every response; 204 No Content would fail the parse. If you need a DELETE with a body, use the `del(path, body)` helper added for `/api/folders/{id}/items`.
- **`pillow_heif` encoder segfaults on some ARM macOS builds.** `Image.save(..., "HEIF")` crashes the process via libheif's `_finish_add_image`; decoding (`Image.open`) works fine. `metascan/utils/heic.py` runs a subprocess decode probe (`_heif_decode_probe`) before `register_heif_opener()` so a broken native lib disables HEIC instead of taking down the scanner. Tests must NOT encode HEIF via Pillow — use the embedded `_HEIF_1X1_B64` fixture and write bytes to disk instead. The companion `_heif_encode_probe` exists for any test that genuinely needs encoding (currently none).
- **FAISS test vectors must use dim >= 32** to avoid SIMD alignment crashes on ARM (Apple Silicon). Tests normalize all vectors for IndexFlatIP.
- **`KMP_DUPLICATE_LIB_OK=TRUE`** is set in `tests/conftest.py` to prevent OpenMP duplicate library crash when torch + faiss-cpu both link libomp on macOS.
- **`httpx` / `httpcore` loggers are pinned to WARNING.** Set in
  `backend/main.py` at module load. `VlmClient`'s `/health` probe
  hits the server up to 10×/sec during model load (~30–60 s on CPU),
  and httpx's default INFO-per-request logging dumped hundreds of
  `503 Service Unavailable` lines per spawn. Don't relax this without
  also rate-limiting or quieting the probe.
- **Every log file goes through `metascan/utils/log_files.py`: 10 MB live
  file, the 3 most recent rollovers kept** (`LOG_MAX_BYTES` /
  `LOG_BACKUP_COUNT`, stdlib `RotatingFileHandler` underneath — 40 MB
  worst case per log). Never open a log with a bare `open(path, "a")` or
  build a `FileHandler` elsewhere; `tests/test_log_files.py` greps
  `metascan/` and `backend/` and fails on one. `logs/
  metadata_extraction_report.txt` was a bare append and reached **4.25 GB
  in one full import** — every successful extraction dumped its whole
  metadata dict, embedded workflow graph included (~170 KB per image
  pretty-printed). Rotation bounds the size, not the write volume, so
  success entries now summarise it: `MetadataParsingLogger._without_graph`
  replaces `raw_metadata` with `<omitted: prompt, workflow>` on a shallow
  copy (the caller's dict is what the scanner stores as
  `generation_data` — never mutate it). Failure entries still carry their
  truncated `raw_data` excerpt. `rotating_file_handler`
  is for ordinary log streams (`server.log`, `embedding_worker.log`,
  `~/.metascan/logs/upscaler.log`); `get_file_logger` is for report files
  written verbatim (the extraction report and its error CSV) and shares
  ONE handler per path — two handlers on one file rotate it out from under
  each other. `BoundedFileHandler` re-stamps a `header` after every
  rollover (the CSV must stay `DictReader`-parseable on its own) and
  reopens a file deleted underneath it, since logs get cleared by hand
  while the server runs. `RotatingFileHandler` is thread-safe but NOT
  process-safe: one file per process. The server's own `logs/server.log`
  is installed from the `lifespan`, not at import, so importing
  `backend.main` in a test creates nothing.
- **Lifespan shuts the event source down before its consumer.** The
  shutdown block in `backend/main.py`'s `lifespan` calls
  `comfy_client.shutdown()` **before** `storyboard_runner.aclose()`
  (each in its own try/except). `comfy_client.on_job_event` fans `job_outputs`
  out to both `ws_manager.broadcast_sync` and
  `storyboard_runner.handle_job_event`, and the latter schedules a
  fire-and-forget ingest task; closing the runner first would leave a
  window where a collect task completing between the two shutdowns spawns
  an ingest task nobody ever awaits.

## Domain rules

Design rules and gotchas for each subsystem live in `.claude/rules/`. Each file lists the paths it applies to
and loads when you work with a matching file, so read it before changing that area:

- `i2v.md` — image to video (I2V)
- `t2i.md` — text to image (T2I)
- `storyboard-backend.md` — storyboards, the story engine and the H3 video pipeline (backend)
- `storyboard-frontend.md` — storyboard authoring UI (frontend)
- `comfyui-driver.md` — the ComfyUI driver, its protocol edges and workflow presets
- `comfyui-metadata.md` — reading generation metadata out of ComfyUI files
- `vlm-llama.md` — the Qwen-VL tagging pipeline and llama-server
- `hardware.md` — hardware tiers, feature gates and the torch device picker
- `frontend.md` — frontend conventions and gotchas

## Development Rules

### Python
- **Formatter:** `black` (v25.11.0 — must match in both requirements.txt and requirements-dev.txt)
- **Linter:** `flake8` on `metascan/ backend/ tests/` — fatal errors (E9, F63, F7, F82) must be zero; style warnings are non-fatal (`--exit-zero`)
- **Type checker:** `mypy` with `python_version = 3.11`, strict on `metascan/core/*`
- **Tests:** `pytest` — all must pass. `tests/test_inference_client.py` spawns a fake NDJSON worker (no CLIP required) to exercise the live-inference subprocess wiring. `tests/test_folders_{db,api}.py` cover DB CRUD + REST handlers against an isolated temp DB using `fastapi.testclient.TestClient`. `tests/test_hardware.py` (42 tests) covers probes + tier classification + feature gates + the `detect_hardware`/`report_to_dict`/`select_torch_device` aggregator. `tests/test_models_hardware_api.py` patches `detect_hardware` against fake reports to exercise `/api/models/hardware` + the `gates` payload of `/api/models/status` via `TestClient`. `tests/test_embedding_device.py` stubs `_torch` and patches `detect_hardware` to verify `_resolve_device` honours preference + auto-picks CUDA/MPS/CPU correctly.
- **Python version:** 3.11.x only — not 3.12, not 3.13+. `setup.py` declares `python_requires=">=3.11,<3.12"`, CI builds 3.11, and `install.sh` refuses anything else. 3.13+ cannot work (pinned Pillow 10.2.0 has no wheel past cp312 and its sdist fails to build); 3.12 resolves but is untested, so it is not supported.
- **Imports in core/:** Never import any UI/desktop framework (`PyQt6`, `qt_material`, `tkinter`, etc.)

### Frontend
- **Vue 3** with Composition API (`<script setup>` syntax)
- **TypeScript** — strict, checked via `vue-tsc --noEmit`
- **State:** Pinia stores
- **Components:** PrimeVue (Aura theme)
- **Build:** `npm run build` runs type-check then Vite build

## Config Files

### `config.json` keys for the location panel

```jsonc
{
  "ui": {
    "map_tile_url": "https://tiles.openfreemap.org/styles/liberty"
  }
}
```

Defaults to OpenFreeMap liberty if absent. Override to point MapLibre GL at any compatible style URL, including a self-hosted one.

### `config.json` keys managed by the Models tab

```jsonc
{
  "models": {
    "preload_at_startup": ["clip-large"],  // model ids; read by lifespan preload loop
    "huggingface_token": ""                  // masked in UI; injected as HF_TOKEN env for subprocesses
  }
}
```

Model ids surfaced by `GET /api/models/status`: `clip-small|medium|large`, `resr-x2|x4|x4-anime`, `gfpgan-v1.4`, `rife`, `nltk-punkt|punkt-tab|stopwords`. The same ids are keys in the `gates` map returned alongside the model rows; `nltk-punkt` vs `nltk-punkt-tab` are mutually exclusive — `feature_gates` marks exactly one available based on the installed NLTK version.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `METASCAN_HOST` | `0.0.0.0` | Backend bind address |
| `METASCAN_PORT` | `8700` | Backend port |
| `METASCAN_API_KEY` | (none) | Bearer token for API auth |
| `METASCAN_CORS_ORIGINS` | `*` | Comma-separated CORS origins |
| `METASCAN_LOG_FILE` | `1` | `0`/`false`/`off` keeps server logging console-only (no `logs/server.log`); `tests/conftest.py` sets it |

## Documentation Layout

User-facing documentation is split between a thin `README.md` and per-topic files under `docs/` (one per topic, kebab-case; `ls docs` lists them).

**README.md is the index, not a kitchen sink.** It carries the overview, screenshots, release notes, Quick Start, and a Documentation section that links to every `docs/*.md` file. New top-level sections that grow past a screen or two should be moved into `docs/` and linked, not appended to README.

**`CLAUDE.md` plus the path-scoped files in `.claude/rules/` are the canonical rule set.** When `docs/developer-guidelines.md` would duplicate a project-rule list (commit conventions, project rules, common-task patterns), it links here instead — keep the rules in one place to avoid drift.

When adding new user-facing documentation:
1. Drop a new file under `docs/<topic>.md` (kebab-case, lowercase). Start with a `[← Back to README](../README.md)` link.
2. Add a one-line entry to README.md's **Documentation** section.
3. If the topic has codebase rules engineers must follow, also add them to `CLAUDE.md` (or the matching `.claude/rules/` file) and link from the docs page (don't duplicate).

## Common Tasks

### Adding a new API endpoint
1. Create route in `backend/api/<module>.py`.
2. If it needs DB access, add the sync DB method in `metascan/core/database_sqlite.py`, then an async wrapper in `backend/services/<domain>_service.py` via `asyncio.to_thread`. (Example split: `MediaService` for media reads; `FoldersService` for folder CRUD. Don't dump everything into `media_service.py`.)
3. Register router in `backend/main.py`.
4. Add a typed fetcher to `frontend/src/api/<domain>.ts`.
5. If mutations should sync across tabs, broadcast on a new or existing WS channel via `ws_manager.broadcast_sync(<channel>, <event>, payload)` from the handler and subscribe with `useWebSocket(<channel>, …)` on the frontend.

### Adding a tag axis / extending the CLIP vocabulary
1. Drop a new `<axis>.txt` under `data/vocabulary/` (one term per line, `#` comments).
2. Register the filename in `metascan/core/vocabulary.py` (`<AXIS>_FILENAME`, `_add_all(...)` call with the axis label).
3. The cache fingerprint hashes the source files — next worker run will re-encode automatically.
4. No DB migration needed; tags still flow through `indices(index_type='tag')` with source `'clip'`.

### Adding a live inference request type
1. Extend `inference_worker.py` with a new `_handle_<type>` method + dispatch in `run()`.
2. Add a matching `async <name>(...)` helper on `InferenceClient` that calls `_request(...)`.
3. Call it from `backend/api/similarity.py` (or a new router). Reuse `_ensure_worker_ready` to keep cold starts sane.

