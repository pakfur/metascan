# CLAUDE.md — Metascan

## Project Overview

Metascan is an AI-generated media browser with metadata extraction, similarity search, and AI upscaling. It uses a **client-server architecture**: a Python/FastAPI backend handles scanning, database, AI processing, and file serving, while a Vue 3 SPA provides the web UI.

## Quick Reference

```bash
# Run the app (two terminals)
source venv/bin/activate && python run_server.py   # Backend: http://localhost:8700
cd frontend && npm run dev                          # Frontend: http://localhost:5173

# Quality checks (must pass before committing)
make quality test     # flake8 + black --check + mypy + pytest (234 tests)

# Frontend only
cd frontend && npm run build    # Type-check + production build
```

## Architecture

```
metascan/
  backend/              # FastAPI server (REST API + WebSocket)
    api/                # Route handlers (media, scan, filters, folders, similarity, upscale, duplicates, config, embeddings, models, websocket)
    services/           # Async wrappers around core modules (asyncio.to_thread)
                        #   folders_service.py, media_service.py
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
      photo_exif.py         # Pure EXIF parser: PhotoExif/PhotoExposure dataclasses (no I/O)
      prompt_tokenizer.py   # NLTK-based prompt keyword extraction
      vocabulary.py         # CLIP tagging vocabulary loader + encoder with .npz cache
      watcher.py            # File system monitoring (watchdog)
      hardware.py           # Tier classification, feature gates, device picker (CUDA/MPS/Vulkan/glibc/NLTK probes)
    extractors/         # Metadata extractors (ComfyUI, SwarmUI, Fooocus)
    cache/              # Thumbnail cache (Pillow + FFmpeg)
    workers/            # Subprocess entry points
                        #   embedding_worker.py — batch embedding + CLIP tagging
                        #   inference_worker.py — long-running CLIP NDJSON server for live queries
                        #   upscale_worker.py   — Real-ESRGAN / GFPGAN / RIFE
    utils/              # App paths, FFmpeg utils, startup profiler

  data/
    vocabulary/         # CLIP tagging inputs (oidv7 / imagenet / aesthetics / nsfw / excluded)
                        # plus cached encoded matrix: vocab.<model_key>.npz

  frontend/             # Vue 3 SPA
    src/
      api/              # Fetch wrapper with auth, typed API functions
                        #   client.ts, media.ts, filters.ts, folders.ts, …
      stores/           # Pinia stores (media, filters, folders, settings, scan,
                        #   similarity, upscale, models)
      composables/      # useWebSocket (multiplexed, auto-reconnect), useKeyboard,
                        #   useFoldersUi (shared overlay state), useToast
      components/
        layout/         # AppHeader, ContentSearchBar, ViewMenubar, ThreePanel,
                        #   ScopeBreadcrumb, ToastHost
        filters/        # FilterPanel, FilterSection, FoldersSection, FolderRow,
                        #   FolderKebabMenu
        thumbnails/     # ThumbnailGrid (virtual scroll), ThumbnailCard, SimilarityBanner
        metadata/       # MetadataPanel, MetadataField
        viewer/         # MediaViewer, ImageViewer, VideoPlayer, SlideshowViewer
        dialogs/        # ScanDialog, SimilaritySettings, DuplicateFinder,
                        # UpscaleDialog, UpscaleQueue, ConfigDialog (+ ConfigModelsTab),
                        # NewFolderDialog, SmartFolderEditor
      types/            # TypeScript interfaces (Media, FilterData, WsMessage,
                        #   folders.ts: RuleField, RuleOp, SmartRules, AnyFolder,
                        #   hardware.ts: Tier, Gate, HardwareReport, HardwarePayload)

  tests/                # pytest test suite
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
- **WebSocket is multiplexed** — a single `/ws` connection carries all channels (`scan`, `upscale`, `embedding`, `watcher`, `models`, `folders`) with JSON envelope `{channel, event, data}`. The `models` channel broadcasts `inference_status`, `inference_progress`, `download_progress`, `download_complete`, `download_error`. The `folders` channel broadcasts `folder_created` / `folder_updated` / `folder_deleted` / `folder_items_changed` for cross-tab sync.
- **Tag inverted index tracks source.** `indices.source` is one of `'prompt'` / `'clip'` / `'both'` for tag rows, NULL for other index types. `_generate_indices` emits `(type, key, source)` triples; `_update_indices` preserves CLIP-sourced tags across rescans by downgrading `'both'` → `'clip'` before rewriting prompt rows. Use `db.add_tag_indices(path, tags, source='clip')` from the embedding worker — it upserts with conflict-merge.
- **Folders persist via `/api/folders`.** Two tables: `folders(id, kind ∈ {manual,smart}, name, icon, rules JSON, sort_order, created_at, updated_at)` and `folder_items(folder_id, file_path, added_at)` with `ON DELETE CASCADE` on both sides. The frontend Pinia store (`stores/folders.ts`) does optimistic local updates with API-backed persistence and rolls back on failure. The `folders` WS channel broadcasts every mutation so other tabs stay in sync. A one-shot localStorage → API import runs on first load when the server returns empty; guarded by a localStorage flag.
- **Smart-folder evaluator is synchronous and client-side.** Rules are a JSON blob evaluated per Media in `stores/folders.ts::evaluateCondition`. Tag conditions can't rely on `m.tags` because the summary endpoint omits it — the store fetches only the tag keys referenced by saved smart folders via `POST /api/filters/tag_paths` with `{keys: […]}` and evaluates against those path sets. A previous bulk-GET version fetched the entire inverted index and blocked the media list endpoint for 20+ s; never restore that shape.
- **`modified_at` / `created_at` carry two historical shapes.** New rows write an ISO-8601 (or SQL-timestamp) string; rows back-filled from the pre-existing `Media` JSON blob hold a unix-epoch float stringified (e.g. `"1775691760.0"`). The smart-folder "Modified" / "Added" evaluator tries `Number(raw)` first (epoch seconds × 1000) and falls back to `Date.parse`. Both backend and frontend must tolerate either shape.
- **`save_media` uses a true upsert** — `INSERT … ON CONFLICT(file_path) DO UPDATE SET …`, **not** `INSERT OR REPLACE`. The latter is DELETE+INSERT under the hood, which re-fires `created_at`'s `DEFAULT CURRENT_TIMESTAMP` every rescan and collapsed the "Added" smart-folder rule onto a single date. The ON CONFLICT path preserves the original ingest time across rescans.
- **Covering indexes must include every SELECT column.** `idx_media_summary_added` and `idx_media_summary_modified` back the grid list endpoint and are the reason `/api/media` returns in ~6 ms instead of ~25 s (the `data` JSON blob has 700+ MB of overflow pages on large libraries). When you add a new column to the summary SELECT, extend both indexes. `_init_database` rebuilds any index whose DDL is missing a currently-required column by reading `sqlite_master`.
- **One-shot data migrations are gated on `PRAGMA user_version`.** e.g. `user_version = 1` is the "`created_at` backfilled from `modified_at` on existing rows" migration. Bump the version when adding new backfills; the gate prevents re-running and silently double-writing on every launch.
- **DELETE endpoints return `{status: "deleted"}` (not 204).** The frontend `request<T>` wrapper in `api/client.ts` calls `res.json()` on every response; 204 No Content would fail the parse. If you need a DELETE with a body, use the `del(path, body)` helper added for `/api/folders/{id}/items`.
- **Similarity threshold is bimodal.** Image↔image uses `similarityStore.threshold` (default 0.7, slider 0-1). Text↔image uses `similarityStore.contentThreshold` (default 0.0, slider 0-0.45 with step 0.01) because CLIP text/image cosine scores live on a much lower scale. `SimilarityBanner.vue` switches range + formatting based on `isContentSearch`.
- **The Similarity Settings dialog has no pHash/CLIP threshold controls.** They were removed because nothing consumed the saved values: the duplicate finder hardcodes a Hamming distance of 10 in `backend/api/duplicates.py`, and similarity search uses the in-memory `useSimilarityStore.threshold` / `contentThreshold` per-session, never the persisted `clip_threshold`. If you re-add a threshold UI, wire it through to those consumers — don't just round-trip through `/api/similarity/settings`.
- **`pillow_heif` encoder segfaults on some ARM macOS builds.** `Image.save(..., "HEIF")` crashes the process via libheif's `_finish_add_image`; decoding (`Image.open`) works fine. `metascan/utils/heic.py` runs a subprocess decode probe (`_heif_decode_probe`) before `register_heif_opener()` so a broken native lib disables HEIC instead of taking down the scanner. Tests must NOT encode HEIF via Pillow — use the embedded `_HEIF_1X1_B64` fixture and write bytes to disk instead. The companion `_heif_encode_probe` exists for any test that genuinely needs encoding (currently none).
- **`LocationSection`'s map container must never hit `display:none`.** When the MapLibre canvas is inside a `display:none` element, browsers pause its `requestAnimationFrame` and the render loop wedges — the next `flyTo` updates camera state but no tiles ever fetch, so the panel becomes a permanently blank gray canvas until page refresh. `v-if`/`v-show` (which both end up at `display:none`) on the section wrapper triggered this on every GPS → no-GPS → GPS toggle. The component instead applies a `meta-section--offscreen` class (`position:absolute; visibility:hidden; top:-10000px`) for non-GPS media, keeping the canvas painted offscreen so its rAF loop and WebGL context stay alive across toggles. The watcher waits one `requestAnimationFrame` after the offscreen→onscreen flip before calling `map.resize()` so `clientWidth` reflects the visible size. Because the section now renders for non-GPS media too, GPS-only computeds (`coordsLabel`, `osmUrl`) must early-return when `!hasGps` to avoid `null.toFixed`. `onBeforeUnmount(destroyMap)` still releases the WebGL context when the panel itself unmounts.
- **Hardware tier + per-model gates.** `metascan/core/hardware.py` runs probes once (`@lru_cache(maxsize=1)` on `detect_hardware()`) for CPU/RAM/CUDA/MPS/Vulkan/glibc/NLTK and classifies hosts into 5 tiers: `cpu_only`, `apple_silicon`, `cuda_entry` (<6 GB VRAM), `cuda_mainstream` (6–12 GB), `cuda_workstation` (≥12 GB). CUDA always wins over MPS. `feature_gates(report)` returns `{model_id: Gate(available, recommended, reason)}` per CLIP/Real-ESRGAN/GFPGAN/RIFE/NLTK model. Auto-warnings populate `report.warnings` for WSL2-without-real-Vulkan and Linux glibc < 2.29 (the latter blocks `rife-ncnn-vulkan`). RIFE is gated unavailable when only `llvmpipe` (software Vulkan) is detected. NLTK ≥ 3.8.2 forces `punkt_tab` over legacy `punkt` (CVE-2024-39705). Both `/api/models/hardware` (returns `{tier, report, ...legacy fields}`) and `/api/models/status` (adds `tier` + `gates`) consume the cached report. The frontend `useModelsStore` exposes `tier`, `gates`, `gateFor(id)`; `ConfigModelsTab.vue` renders a tier banner + per-row recommended/unsupported chips with reason tooltips.
- **Shared torch device picker.** `select_torch_device(preference="auto")` in `hardware.py` is the single source of truth for CUDA → MPS (Darwin only) → CPU precedence. `EmbeddingManager._resolve_device` delegates to it; new PyTorch paths (Real-ESRGAN, GFPGAN if/when wired) should do the same. Explicit preferences (`"cpu"`, `"cuda"`, `"mps"`) are returned verbatim — only `"auto"` triggers detection. **Apple Silicon previously fell through to CPU** for CLIP because the old `_resolve_device` only checked `cuda.is_available()`; the shared picker fixes that gap.
- **Vite proxy** forwards `/api/*` and `/ws` to the backend during development. The EPIPE error handler silences broken pipe from cancelled browser requests.
- **FAISS test vectors must use dim >= 32** to avoid SIMD alignment crashes on ARM (Apple Silicon). Tests normalize all vectors for IndexFlatIP.
- **`KMP_DUPLICATE_LIB_OK=TRUE`** is set in `tests/conftest.py` to prevent OpenMP duplicate library crash when torch + faiss-cpu both link libomp on macOS.
- **Qwen3-VL VLM tagging.** A long-running `VlmClient`
  (`metascan/core/vlm_client.py`) supervises a `llama-server` subprocess for
  generative tagging on hardware tiers where it's viable. CLIP tagging
  remains the fallback for `cpu_only` and `cuda_entry`. The DB layer
  arbitrates merging via `_update_indices` and `add_tag_indices` —
  VLM-source tag rows survive CLIP rescans (the demote-on-rescan logic in
  `database_sqlite._update_indices` preserves them). Engine choice rationale
  is in `docs/superpowers/specs/2026-05-02-qwen3vl-tagging-design.md` §11.
- **VLM image-only guard.** `VlmClient.generate_tags` short-circuits with
  `[]` for any path whose suffix isn't in `_SUPPORTED_IMAGE_EXTS`. Both
  the scan-time enqueuer (`embedding_worker.py`) and the retag job
  (`backend/api/vlm.py:_run_retag_job`) filter videos upfront so progress
  totals are honest. Use `VlmClient.is_image_path(path)` from new call
  sites instead of duplicating the extension list.
- **VLM image resize.** `_encode_image_b64` decodes via Pillow and
  resizes to `_IMAGE_MAX_EDGE = 1024` before JPEG-encoding. Skipping the
  resize previously blew past the 8K context budget on 2K SDXL renders
  (`HTTP 400 the request exceeds the available context size`). Tagging
  doesn't need fine-print readability — bumping the cap only makes sense
  alongside a `--ctx-size` bump in `_build_command`.
- **GBNF grammar gotcha.** `\-` is not a valid GBNF escape; bad
  grammars crash `llama-server` with SIGSEGV inside
  `llama_grammar_init_impl`, triggering an infinite respawn loop on every
  tag request. Hyphens must be literal (place at the start or end of a
  character class). The tagging grammar lives in
  `metascan/core/vlm_prompts.py:TAGGING_GRAMMAR`.
- **`llama-server` local override.** `binary_path()` returns
  `data/bin/local/<name>` when present, else the bundled
  `data/bin/<name>`. `scripts/build_llama_server.sh` populates the
  override (Linux + NVIDIA CUDA is the most common reason — upstream
  ships no Linux CUDA prebuilt). The override naturally suppresses the
  bundled-asset download because both `_vlm_status_rows` and the
  downloader check `binary_path().exists()`. See
  `docs/build-llama-server.md`.
- **llama.cpp release zip extraction must flatten `bin/`.** The release
  archives ship the binary plus its sister shared libraries
  (`libllama.so`, `libmtmd.so`, `libggml*.so`, …) under `build/bin/`.
  `llama-server`'s `RUNPATH` is `$ORIGIN`, so every `.so` must land in
  the same directory as the binary or it dies at startup with `error
  while loading shared libraries`. b7400+ archives also include
  symlinked SONAME chains (`libllama.so` → `libllama.so.0` →
  `libllama.so.0.0.7400`) — preserve them via `os.symlink` (zip stores
  the link target as the file content with `S_IFLNK` in
  `external_attr`). Logic lives in `setup_models.py:_ensure_target`.
- **Local llama.cpp builds need explicit RPATH + flat output.**
  `cmake` by default places shared libs alongside their target's
  source dir (`build/tools/mtmd/libmtmd.so`, `build/src/libllama.so`,
  …) and does not bake `$ORIGIN`-relative `RUNPATH` into the build-tree
  binary. Either of those alone is enough to leave the binary unable
  to find its libs after we copy it. The build script forces output
  consolidation (`-DCMAKE_RUNTIME_OUTPUT_DIRECTORY=build/bin
  -DCMAKE_LIBRARY_OUTPUT_DIRECTORY=build/bin`) for both platforms, then
  applies a per-OS rpath strategy. **Linux / ELF:**
  `-DCMAKE_BUILD_RPATH_USE_ORIGIN=ON -DCMAKE_INSTALL_RPATH='$ORIGIN'`.
  **macOS / Mach-O:** `-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON
  -DCMAKE_INSTALL_RPATH='@loader_path'` plus a post-copy
  `install_name_tool -delete_rpath` / `-add_rpath '@loader_path'` /
  `codesign --force --sign -` pass on the binary and every dylib —
  cmake's ELF-only `BUILD_RPATH_USE_ORIGIN` flag is ignored on Mach-O,
  and `CMAKE_INSTALL_RPATH` only fires on `cmake --install` (we just
  `cp`), so without the post-process pass the binary keeps cmake's
  default absolute build-tree rpath and dies the moment the temp dir
  goes away. The verify step (`--version`) now runs **after**
  `rm -rf "${WORK_DIR}"` so any reliance on the build-tree rpath
  surfaces immediately rather than passing verify and failing on first
  user activation.
- **Qwen3-VL pipeline DB writes must run in a worker thread.**
  Both `_run_retag_job` (`backend/api/vlm.py`) and `VlmTagPump.drain_once`
  (`backend/services/vlm_tag_pump.py`) wrap `db.add_tag_indices` with
  `asyncio.to_thread`. WSL2 `/mnt/<drive>` mounts and slow disks
  produce SQLite fsyncs of 50–100 ms+; running that on the event loop
  serializes concurrent tagging tasks and triggers the `heartbeat:
  event loop stalled` warnings.
- **Per-request VLM concurrency must match `parallel_slots`.** Both
  pipelines use a `Semaphore(spec.parallel_slots)` keyed off the
  registry entry for the active model id (`REGISTRY[mid].parallel_slots`).
  Going below it leaves GPU slots idle in `llama-server`'s
  `--parallel`; going above it forces the server to queue requests and
  removes the overlap benefit.
- **VLM status row requires the binary too.** `_vlm_status_rows` in
  `backend/api/models.py` only flips a row to `available` when GGUF +
  mmproj + `binary_path()` are all present. A partial download (weights
  on disk, binary missing) keeps the row at `missing` so the Download
  button stays enabled — `_ensure_target` short-circuits on existing
  files, so retrying only fetches the missing pieces. `size_bytes` is
  reported from whichever weight files exist regardless of binary
  status, so the user still sees partial-download progress.
- **VLM download stage label.** `_download_vlm` broadcasts
  `download_progress` with `stage="downloading (n/3)"` and
  `percent=0.0`. Don't put the GGUF filename in the stage — it's
  several characters longer than the chip can render. The frontend
  `statusLabel` only appends `${pct}%` when `percent > 0`, so keeping
  the percent at 0 avoids the misleading "33%" / "66%" suffix that
  reflects step count, not byte progress.
- **`httpx` / `httpcore` loggers are pinned to WARNING.** Set in
  `backend/main.py` at module load. `VlmClient`'s `/health` probe
  hits the server up to 10×/sec during model load (~30–60 s on CPU),
  and httpx's default INFO-per-request logging dumped hundreds of
  `503 Service Unavailable` lines per spawn. Don't relax this without
  also rate-limiting or quieting the probe.
- **`VlmClient` stderr drainer logs at DEBUG, errors at WARNING.**
  llama-server stderr includes the entire chat-template dump on each
  load (~150 lines) plus per-request slot chatter. Routine lines go to
  DEBUG; lines containing `error`/`failed`/`fatal`/`abort` are
  promoted to WARNING. The 200-line ring buffer (`_stderr_ring`) is
  attached to crash reports by `_wait_exit` so debugging info still
  reaches the user on a real failure.

## Development Rules

### Python
- **Formatter:** `black` (v25.11.0 — must match in both requirements.txt and requirements-dev.txt)
- **Linter:** `flake8` on `metascan/ backend/ tests/` — fatal errors (E9, F63, F7, F82) must be zero; style warnings are non-fatal (`--exit-zero`)
- **Type checker:** `mypy` with `python_version = 3.11`, strict on `metascan/core/*`
- **Tests:** `pytest` — 175 tests, all must pass. `tests/test_inference_client.py` spawns a fake NDJSON worker (no CLIP required) to exercise the live-inference subprocess wiring. `tests/test_folders_{db,api}.py` cover DB CRUD + REST handlers against an isolated temp DB using `fastapi.testclient.TestClient`. `tests/test_hardware.py` (42 tests) covers probes + tier classification + feature gates + the `detect_hardware`/`report_to_dict`/`select_torch_device` aggregator. `tests/test_models_hardware_api.py` patches `detect_hardware` against fake reports to exercise `/api/models/hardware` + the `gates` payload of `/api/models/status` via `TestClient`. `tests/test_embedding_device.py` stubs `_torch` and patches `detect_hardware` to verify `_resolve_device` honours preference + auto-picks CUDA/MPS/CPU correctly.
- **Python version:** 3.11+ required (3.13 not supported)
- **Imports in core/:** Never import any UI/desktop framework (`PyQt6`, `qt_material`, `tkinter`, etc.)

### Frontend
- **Vue 3** with Composition API (`<script setup>` syntax)
- **TypeScript** — strict, checked via `vue-tsc --noEmit`
- **State:** Pinia stores
- **Components:** PrimeVue (Aura theme)
- **Build:** `npm run build` runs type-check then Vite build

### CI (.github/workflows/python-package.yml)
Two parallel jobs:
1. **backend:** Install deps, flake8, black --check, mypy, pytest
2. **frontend:** npm ci, vue-tsc --noEmit, npm run build

`make quality test` locally matches the CI backend job exactly.

## Config Files

| File | Purpose |
|------|---------|
| `config.json` | App config (directories, theme, thumbnail size, similarity settings, `models` section — see below) |
| `mypy.ini` | Type checking config (python 3.11, strict core, ignore UI) |
| `requirements.txt` | Production deps (FastAPI, torch, CLIP, FAISS, etc.) |
| `requirements-dev.txt` | Dev deps (pytest, black, mypy — black version must match prod) |
| `frontend/vite.config.ts` | Vite config with proxy to backend |
| `frontend/tsconfig.app.json` | TypeScript config (noUnusedLocals/Params disabled for template refs) |

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

## Documentation Layout

User-facing documentation is split between a thin `README.md` and per-topic files under `docs/`:

```
README.md                       # Overview (3 paragraphs), Latest Release, Quick Start, links
CONTRIBUTING.md                 # Contributor workflow (links to docs/)
docs/
  features.md                   # Full feature list + keyboard shortcuts
  tech-stack.md                 # Backend / frontend / AI / infra deps
  installation.md               # Prerequisites, setup, env vars
  configuration.md              # config.json reference
  api-reference.md              # Endpoints, WS envelope, error shapes
  architecture.md               # Client–server layout, DB schema, key decisions
  hardware-detection.md         # Probes, tiers, gates, auto-warnings
  developer-guidelines.md       # Build / style / test / CI
```

**README.md is the index, not a kitchen sink.** It carries the overview, screenshots, release notes, Quick Start, and a Documentation section that links to every `docs/*.md` file. New top-level sections that grow past a screen or two should be moved into `docs/` and linked, not appended to README.

**`CLAUDE.md` is the canonical rule set.** When `docs/developer-guidelines.md` would duplicate a project-rule list (commit conventions, project rules, common-task patterns), it links here instead — keep the rules in one place to avoid drift.

When adding new user-facing documentation:
1. Drop a new file under `docs/<topic>.md` (kebab-case, lowercase). Start with a `[← Back to README](../README.md)` link.
2. Add a one-line entry to README.md's **Documentation** section.
3. If the topic has codebase rules engineers must follow, also add them to `CLAUDE.md` and link from the docs page (don't duplicate).

## Common Tasks

### Adding a new API endpoint
1. Create route in `backend/api/<module>.py`.
2. If it needs DB access, add the sync DB method in `metascan/core/database_sqlite.py`, then an async wrapper in `backend/services/<domain>_service.py` via `asyncio.to_thread`. (Example split: `MediaService` for media reads; `FoldersService` for folder CRUD. Don't dump everything into `media_service.py`.)
3. Register router in `backend/main.py`.
4. Add a typed fetcher to `frontend/src/api/<domain>.ts`.
5. If mutations should sync across tabs, broadcast on a new or existing WS channel via `ws_manager.broadcast_sync(<channel>, <event>, payload)` from the handler and subscribe with `useWebSocket(<channel>, …)` on the frontend.

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

### Adding a hardware probe / tier rule / feature gate
1. **New probe:** add a `_<thing>()` helper in `metascan/core/hardware.py` that returns `Optional[<value>]` and never raises (catch-and-log at DEBUG). Add a field to the `HardwareReport` dataclass with a safe default. Wire it into `detect_hardware()`. Frontend `HardwareReport` interface in `frontend/src/types/hardware.ts` gets the matching field.
2. **New auto-warning:** append to `report.warnings` inside `detect_hardware()` after probes run. Match the spec wording exactly — frontend renders the strings verbatim in `ConfigModelsTab.vue`'s warning banner.
3. **New tier:** extend the `Tier` enum **and** the TS `Tier` union in `frontend/src/types/hardware.ts`, plus `TIER_LABEL` / `TIER_COLOR` maps. Update `classify_tier()` precedence carefully — CUDA must still win over MPS.
4. **New gate / new model id:** add a key to `feature_gates()`'s returned dict; the model id must match the row id used by `_clip_status_rows` / `_upscale_status_rows` / `_nltk_status_rows` in `backend/api/models.py`. The frontend `gateChip()` / `gateChipClass()` helpers in `ConfigModelsTab.vue` will pick it up automatically.
5. **Tests:** `tests/test_hardware.py` covers probe + tier + gate logic in isolation; `tests/test_models_hardware_api.py` covers the HTTP envelope. Both patch `detect_hardware` (or `backend.api.models.detect_hardware`) to inject a fake `HardwareReport` — never rely on the host's real hardware in tests. Call `detect_hardware.cache_clear()` in any test that mutates env vars before invoking the real probe.
6. **VLM model gate.** Qwen3-VL gates live alongside CLIP gates; their
   `recommended` decision is what `backend/services/scan_dispatch.py:should_tag_with_vlm`
   reads to choose between VLM and CLIP tagging on a scan. Per-model VRAM
   floors come from `metascan/core/vlm_models.REGISTRY`'s `min_vram_gb` field
   (single source of truth — `feature_gates` reads from there).

### Adding a smart-folder rule field
1. Add the field identifier to `RuleField` in `frontend/src/types/folders.ts`.
2. Add a `FIELD_DEFS[<field>]` entry in `frontend/src/stores/folders.ts` (label, ops, value type, default).
3. Add a `case '<field>':` to `evaluateCondition` — keep it synchronous; async work belongs in a precomputed path-set cache (see the tags pattern).
4. If the rule reads a column not already on the `/api/media` summary, add it to the SELECT in `get_all_media_summaries` **and** to every covering index (`idx_media_summary_added`, `idx_media_summary_modified`) — otherwise `/api/media` falls back to the main-table scan. `Media` frontend type gets the new field too.
5. If conditions carry server-resolved references (e.g. tag keys, later CLIP queries), add an endpoint that takes an explicit key list and cache responses in the store keyed by referenced values. Don't bulk-GET the whole universe.
6. Extend `migrateSmartFolder` in `stores/folders.ts` if you're removing/renaming an existing field so persisted rules don't crash the editor.

### Adding a new VLM caption style
1. Add the style key to `CAPTION_STYLE_PROMPTS` in `metascan/core/vlm_prompts.py`.
2. The style picker in the (future) UI reads keys directly; backend doesn't need a registry change.
3. The style template should be deterministic, single-image, and produce parseable output if the consumer requires structured fields.
4. Wire it into `VlmClient.generate_caption(image_path, style)` once the future captioning feature lands.

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
