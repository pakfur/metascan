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
                        #   client.ts, media.ts, filters.ts, folders.ts, storyboard.ts, …
      router/           # index.ts — hash-history vue-router: `/` (LibraryView),
                        #   `/storyboard/:id?` (StoryboardView)
      views/            # LibraryView (desktop/mobile shell + grid), StoryboardView
                        #   (desktop-only authoring canvas)
      stores/           # Pinia stores (media, filters, folders, settings, scan,
                        #   search, upscale, models, storyboard)
      composables/      # useWebSocket (multiplexed, auto-reconnect), useKeyboard,
                        #   useFoldersUi (shared overlay state), useToast
      components/
        layout/         # ContentSearchBar (header action row — scan, refresh,
                        #   upscale queue, duplicates, similarity settings, config,
                        #   Storyboards nav), ViewMenubar, ThreePanel,
                        #   ScopeBreadcrumb, ToastHost
        filters/        # FilterPanel, FilterSection, FoldersSection, FolderRow,
                        #   FolderKebabMenu, SearchSection
        thumbnails/     # ThumbnailGrid (virtual scroll), ThumbnailCard
        metadata/       # MetadataPanel, MetadataField
        viewer/         # MediaViewer (allowDestructive prop, default true, gates
                        #   delete/favorite/library-selectMedia for non-library
                        #   callers like the storyboard takes viewer),
                        #   ImageViewer, VideoPlayer, SlideshowViewer
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
- **WebSocket is multiplexed** — a single `/ws` connection carries all channels (`scan`, `upscale`, `embedding`, `watcher`, `models`, `folders`, `comfy`, `storyboard`) with JSON envelope `{channel, event, data}`. The `models` channel broadcasts `inference_status`, `inference_progress`, `download_progress`, `download_complete`, `download_error`. The `folders` channel broadcasts `folder_created` / `folder_updated` / `folder_deleted` / `folder_items_changed` for cross-tab sync. The `storyboard` channel broadcasts `synthesis_progress` (`{storyboard_id, panel_id, beat_id, done, total, prompt_source}`), `synthesis_complete` (`{storyboard_id, synthesized, fallback, skipped_locked}`), `synthesis_error` (`{storyboard_id, error}`), and `beat_images_changed` (`{storyboard_id, panel_id, beat_id, files}`) from `StoryboardRunner`'s own `on_event` callback — the `folder_created` / `folder_items_changed` events it also emits go out on the `folders` channel, not `storyboard`. `POST /api/storyboard/{id}/synthesize` is 202 fire-and-forget (`asyncio.create_task`); `synthesis_complete`/`synthesis_error` are the only signal a client gets that the background run actually finished or died — `StoryboardRunner.synthesize` wraps the real work and always emits exactly one of the two, re-raising after `synthesis_error` so a direct (non-route) caller still sees the exception.
- **Tag inverted index tracks source.** `indices.source` is one of `'prompt'` / `'clip'` / `'both'` for tag rows, NULL for other index types. `_generate_indices` emits `(type, key, source)` triples; `_update_indices` preserves CLIP-sourced tags across rescans by downgrading `'both'` → `'clip'` before rewriting prompt rows. Use `db.add_tag_indices(path, tags, source='clip')` from the embedding worker — it upserts with conflict-merge.
- **Folders persist via `/api/folders`.** Two tables: `folders(id, kind ∈ {manual,smart}, name, icon, rules JSON, sort_order, created_at, updated_at)` and `folder_items(folder_id, file_path, added_at)` with `ON DELETE CASCADE` on both sides. The frontend Pinia store (`stores/folders.ts`) does optimistic local updates with API-backed persistence and rolls back on failure. The `folders` WS channel broadcasts every mutation so other tabs stay in sync. A one-shot localStorage → API import runs on first load when the server returns empty; guarded by a localStorage flag.
- **Smart-folder evaluator is synchronous and client-side.** Rules are a JSON blob evaluated per Media in `stores/folders.ts::evaluateCondition`. Tag conditions can't rely on `m.tags` because the summary endpoint omits it — the store fetches only the tag keys referenced by saved smart folders via `POST /api/filters/tag_paths` with `{keys: […]}` and evaluates against those path sets. A previous bulk-GET version fetched the entire inverted index and blocked the media list endpoint for 20+ s; never restore that shape.
- **`modified_at` / `created_at` carry two historical shapes.** New rows write an ISO-8601 (or SQL-timestamp) string; rows back-filled from the pre-existing `Media` JSON blob hold a unix-epoch float stringified (e.g. `"1775691760.0"`). The smart-folder "Modified" / "Added" evaluator tries `Number(raw)` first (epoch seconds × 1000) and falls back to `Date.parse`. Both backend and frontend must tolerate either shape.
- **`save_media` uses a true upsert** — `INSERT … ON CONFLICT(file_path) DO UPDATE SET …`, **not** `INSERT OR REPLACE`. The latter is DELETE+INSERT under the hood, which re-fires `created_at`'s `DEFAULT CURRENT_TIMESTAMP` every rescan and collapsed the "Added" smart-folder rule onto a single date. The ON CONFLICT path preserves the original ingest time across rescans.
- **Covering indexes must include every SELECT column.** `idx_media_summary_added` and `idx_media_summary_modified` back the grid list endpoint and are the reason `/api/media` returns in ~6 ms instead of ~25 s (the `data` JSON blob has 700+ MB of overflow pages on large libraries). When you add a new column to the summary SELECT, extend both indexes. `_init_database` rebuilds any index whose DDL is missing a currently-required column by reading `sqlite_master`.
- **One-shot data migrations are gated on `PRAGMA user_version`.** e.g. `user_version = 1` is the "`created_at` backfilled from `modified_at` on existing rows" migration; `user_version = 2` is the thumbnail-cache wipe for EXIF-orientation handling; `user_version = 3` is the shot/beat model reorganization — drops and recreates `panels`/`beats`/`panel_images→beat_images` (dev data is disposable by decision, no column copying), first unhiding media the old `panel_images` table left hidden and deleting `generation_jobs` rows with a non-NULL `panel_id` so a restart can't re-adopt jobs for panels that no longer exist. Bump the version when adding new backfills; the gate prevents re-running and silently double-writing on every launch.
- **DELETE endpoints return `{status: "deleted"}` (not 204).** The frontend `request<T>` wrapper in `api/client.ts` calls `res.json()` on every response; 204 No Content would fail the parse. If you need a DELETE with a body, use the `del(path, body)` helper added for `/api/folders/{id}/items`.
- **Search is a filter layer, not a separate results view.** `useSearchStore` (`frontend/src/stores/search.ts`) holds text search, Find-Similar, and tag-AND as independent path-set layers that `mediaStore.displayedMedia` intersects with the folder/preset scope — there is no dedicated search results screen. The backend endpoints are light and unbounded (`[{file_path, similarity_score}]`, threshold applied server-side), and a Relevance sort option orders by `similarity_score` when a search layer is active. `SearchSection.vue` (in `components/filters/`, top of the left panel) is the only UI: it renders the query/chip input, the "Similar to …" chip, and the threshold slider. **Similarity threshold is bimodal.** Text↔image search uses `searchStore.textThreshold` (default 0.2, slider 0-0.45) because CLIP text/image cosine scores live on a much lower scale than image↔image, which uses `searchStore.imageThreshold` (default 0.7, slider 0-1). `SearchSection.vue` switches slider range + formatting based on `isTextSearch`.
- **The Similarity Settings dialog has no pHash/CLIP threshold controls.** They were removed because nothing consumed the saved values: the duplicate finder hardcodes a Hamming distance of 10 in `backend/api/duplicates.py`, and similarity search uses `useSearchStore`'s in-memory `textThreshold` / `imageThreshold` per-session, never the persisted `clip_threshold`. If you re-add a threshold UI, wire it through to those consumers — don't just round-trip through `/api/similarity/settings`.
- **`pillow_heif` encoder segfaults on some ARM macOS builds.** `Image.save(..., "HEIF")` crashes the process via libheif's `_finish_add_image`; decoding (`Image.open`) works fine. `metascan/utils/heic.py` runs a subprocess decode probe (`_heif_decode_probe`) before `register_heif_opener()` so a broken native lib disables HEIC instead of taking down the scanner. Tests must NOT encode HEIF via Pillow — use the embedded `_HEIF_1X1_B64` fixture and write bytes to disk instead. The companion `_heif_encode_probe` exists for any test that genuinely needs encoding (currently none).
- **`LocationSection`'s map container must never hit `display:none`.** When the MapLibre canvas is inside a `display:none` element, browsers pause its `requestAnimationFrame` and the render loop wedges — the next `flyTo` updates camera state but no tiles ever fetch, so the panel becomes a permanently blank gray canvas until page refresh. `v-if`/`v-show` (which both end up at `display:none`) on the section wrapper triggered this on every GPS → no-GPS → GPS toggle. The component instead applies a `meta-section--offscreen` class (`position:absolute; visibility:hidden; top:-10000px`) for non-GPS media, keeping the canvas painted offscreen so its rAF loop and WebGL context stay alive across toggles. The watcher waits one `requestAnimationFrame` after the offscreen→onscreen flip before calling `map.resize()` so `clientWidth` reflects the visible size. Because the section now renders for non-GPS media too, GPS-only computeds (`coordsLabel`, `osmUrl`) must early-return when `!hasGps` to avoid `null.toFixed`. `onBeforeUnmount(destroyMap)` still releases the WebGL context when the panel itself unmounts.
- **Hardware tier + per-model gates.** `metascan/core/hardware.py` runs probes once (`@lru_cache(maxsize=1)` on `detect_hardware()`) for CPU/RAM/CUDA/MPS/Vulkan/glibc/NLTK and classifies hosts into 5 tiers: `cpu_only`, `apple_silicon`, `cuda_entry` (<6 GB VRAM), `cuda_mainstream` (6–12 GB), `cuda_workstation` (≥12 GB). CUDA always wins over MPS. `feature_gates(report)` returns `{model_id: Gate(available, recommended, reason)}` per CLIP/Real-ESRGAN/GFPGAN/RIFE/NLTK model. Auto-warnings populate `report.warnings` for WSL2-without-real-Vulkan and Linux glibc < 2.29 (the latter blocks `rife-ncnn-vulkan`). RIFE is gated unavailable when only `llvmpipe` (software Vulkan) is detected. NLTK ≥ 3.8.2 forces `punkt_tab` over legacy `punkt` (CVE-2024-39705). Both `/api/models/hardware` (returns `{tier, report, ...legacy fields}`) and `/api/models/status` (adds `tier` + `gates`) consume the cached report. The frontend `useModelsStore` exposes `tier`, `gates`, `gateFor(id)`; `ConfigModelsTab.vue` renders a tier banner + per-row recommended/unsupported chips with reason tooltips.
- **Shared torch device picker.** `select_torch_device(preference="auto")` in `hardware.py` is the single source of truth for CUDA → MPS (Darwin only) → CPU precedence. `EmbeddingManager._resolve_device` delegates to it; new PyTorch paths (Real-ESRGAN, GFPGAN if/when wired) should do the same. Explicit preferences (`"cpu"`, `"cuda"`, `"mps"`) are returned verbatim — only `"auto"` triggers detection. **Apple Silicon previously fell through to CPU** for CLIP because the old `_resolve_device` only checked `cuda.is_available()`; the shared picker fixes that gap.
- **Routing is hash-history vue-router (`frontend/src/router/index.ts`), added in Phase C.** `/` is `LibraryView` — the pre-Phase-C `App.vue` moved verbatim, still the desktop/mobile grid shell. `/storyboard/:id?` is `StoryboardView`, a desktop-only authoring canvas (no mobile layout). `App.vue` itself is now a thin router shell: `<router-view>` plus `ToastHost` and the folders WS bridge (`folder_created`/`folder_updated`/`folder_deleted`/`folder_items_changed`) — it holds no dialog or page state, so grep `views/LibraryView.vue` or `views/StoryboardView.vue` for that, not `App.vue`.
- **Mobile UI is a viewport-selected shell, not responsive CSS on the desktop tree.** `composables/useViewport.ts` exposes a reactive `isMobile` (VueUse `useMediaQuery('(max-width: 767px)')`, the only place that query string lives). `LibraryView.vue` renders `ThreePanel` (desktop) or `MobileShell` (mobile), and swaps the full-screen viewer between `MediaViewer` (Galleria, desktop) and the touch-first `MobileMediaViewer` (`components/mobile/`). The mobile experience is deliberately narrow — grid browsing, folder bottom sheet (`MobileFolderMenu`), content search (`composables/useContentSearch.ts`, a port of `ContentSearchBar`'s coalesce logic), sort/size, one-tap slideshow, and the gesture viewer; filters, the metadata panel, and all management dialogs are simply never mounted on mobile. The mobile port itself didn't touch `ContentSearchBar.vue`; Phase C later added the Storyboards nav button there, so the desktop component has moved since — read it directly rather than assuming it's frozen. **Desktop stays behaviorally identical** because every shared-component change is an additive prop defaulting to the old behavior: `ThumbnailGrid`'s `mobile` (tap-to-open, no context menu, no drag) and `SlideshowViewer`'s `autoStart`. Two consistency rules matter: search now narrows `scopedMedia` itself (composed upstream into `displayedMedia`), so `gridList` in `LibraryView.vue` is just `computed(() => mediaStore.scopedMedia)` and mobile and desktop already share one list — `openViewer` indexes against that same `scopedMedia`-backed list regardless of `isMobile`; and `LibraryView.vue` watches `isMobile` to close any open viewer/slideshow on a breakpoint flip (the two viewers bind different lists behind the same index). Touch gestures (`components/mobile/MobileMediaViewer.vue` + pure helpers in `utils/gestures.ts`) use pointer events: swipe prev/next, pinch-zoom + pan, swipe-down-to-close, with the zoom snap-to-1 running before the pinch→single-finger re-arm so a swipe survives pinch jitter. See `docs/superpowers/specs/2026-07-28-mobile-responsive-ui-design.md`.
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
  `metascan/core/vlm_models.REGISTRY` now spans two families — four
  Qwen3-VL Abliterated sizes (`qwen3vl-2b/4b/8b/30b-a3b`) plus
  `qwen38-27b` (Qwen3.8 27B Abliterated, dense hybrid Gated DeltaNet) —
  and `qwen38-27b` is the `cuda_workstation` ≥22 GB VRAM recommendation
  (`qwen3vl-8b` remains the pick below that floor).
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
- **llama.cpp release archive extraction must flatten `bin/`.** The
  release archives ship the binary plus its sister shared libraries
  (`libllama.so`, `libmtmd.so`, `libggml*.so`, …) under `build/bin/`.
  `llama-server`'s `RUNPATH` is `$ORIGIN`, so every `.so` must land in
  the same directory as the binary or it dies at startup with `error
  while loading shared libraries`. From `LLAMA_CPP_RELEASE = "b10456"`
  onward, Linux/macOS assets are `.tar.gz` (Windows stays `.zip`) —
  `setup_models.py` picks `_extract_flat_bin_targz` or
  `_extract_flat_bin_zip` off the URL's suffix in `_ensure_target`.
  Archives also include symlinked SONAME chains (`libllama.so` →
  `libllama.so.0` → `libllama.so.0.0.<build>`) — the zip path preserves
  them via `os.symlink` (zip stores the link target as the file content
  with `S_IFLNK` in `external_attr`), the tar path via
  `TarInfo.issym()`/`.linkname` (tar stores real symlink entries
  natively). Both extractors defer symlink creation until after their
  targets are written.
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
- **Qwen3.8 reasoning must stay disabled.** `qwen38-27b`'s `extra_args`
  disable thinking at server startup because llama.cpp grammar enforcement
  is inactive while thinking is enabled (ggml-org/llama.cpp#20345) — and
  every metascan VLM call is GBNF-constrained. Never remove those flags
  without moving all call sites off grammars. The model also requires
  llama.cpp >= ~b10450: older CUDA builds load it fine and silently emit
  corrupted tokens (Gated DeltaNet kernel bug). `LLAMA_CPP_RELEASE` is
  pinned accordingly; a stale `data/bin/local/llama-server` built from an
  older tag reproduces the garbage-output failure even with a correct pin.
- **Per-model llama-server flags live in `VlmModelSpec.extra_args`**, and
  the context budget in `VlmModelSpec.ctx_size` — never re-introduce
  model-id string matching in `vlm_client._build_command` or
  `startswith("qwen3vl-")` filters in selection code; use `mid in REGISTRY`.
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
- **ComfyUI is driven, not just parsed.** `metascan/core/comfy_client.py`
  submits jobs to a ComfyUI server (the extractors in
  `metascan/extractors/comfyui*.py` remain read-only metadata parsers, a
  separate concern). A workflow is registered as an API-format graph whose
  nodes are titled with the `MS_*` convention (`MS_POSITIVE`, `MS_NEGATIVE`,
  `MS_SEED`, `MS_LATENT`, `MS_SAVE`, optional `MS_LORA` / `MS_LORA_STACK` /
  `MS_REF_IMAGE`);
  `comfy_bindings.resolve_bindings` maps titles to node ids at registration
  time and **fails loudly** on a missing required title. Titles are used
  rather than node ids because ComfyUI renumbers nodes on re-save.
- **Metascan owns the ComfyUI job queue.** `ComfyClient` holds at most
  `comfy.in_flight` jobs inside ComfyUI at a time so a user-requested reroll
  can jump the queue and cancellation stays responsive. One persistent
  WebSocket per app (not per job) consumes ComfyUI's event stream; the
  `execution_error` node type and message go verbatim into
  `generation_jobs.error`.
- **Generated images are fetched over HTTP, never read from disk.**
  `collect_outputs` pulls each image via `/view` and writes it under
  `comfy.output_root`, so a remote or containerized ComfyUI works unchanged
  and there is no watcher race. Ingest goes through the public
  `Scanner.ingest_file`, wrapped in `asyncio.to_thread` — it does SQLite
  writes and Pillow work, and running it on the event loop stalls the
  WebSocket reader.
- **ComfyUI's protocol has four sharp edges; `tests/_fake_comfy_server.py`
  models all four and must keep doing so.** Verified against ComfyUI's
  `server.py` / `execution.py` / `main.py`.
  1. **`/interrupt` must carry `{"prompt_id": ...}`.** A bodyless POST is
     an explicit *global* interrupt that kills whatever prompt is
     currently executing — and since ComfyUI runs one prompt at a time
     while metascan keeps `in_flight` (default 2) queued there, the job a
     user cancels is routinely the *pending* one and the bystander is a
     real generation. Go through `ComfyClient._stop_prompt`.
  2. **An interrupted prompt reports `execution_interrupted`, not
     `execution_error`** (`handle_execution_error` branches on
     `InterruptProcessingException`). It must be handled as terminal or
     the job never leaves `running` and permanently burns an `in_flight`
     slot.
  3. **`/history` is written only at end of prompt**, by
     `PromptQueue.task_done()` — after every `executed` frame and after
     `execution_success` (which is emitted from *inside* `execute()`).
     Collection therefore triggers on `execution_success` /
     `executing {node: null}`, never on `executed`, and
     `_await_history` retries briefly before treating an absent entry as
     a job failure. Reading history on the first `executed` silently
     lost every image whenever `MS_SAVE` wasn't the last node to run.
  4. **`executing` is overloaded**: `{node: <id>}` is a per-node ping,
     `{node: null}` is end-of-prompt. Only the latter is actionable.
- **ComfyUI job rows are reconciled at startup, not just at reconnect.**
  `ComfyClient.start()` calls `_rehydrate_jobs` once: `queued` rows are
  re-enqueued into `_queue` (that's what makes "state survives a restart"
  true), and `running` rows left by a dead process are marked `failed`
  with an "interrupted by a restart" message rather than re-adopted —
  no event will ever arrive for them and re-adopting would
  over-subscribe `in_flight`. `_rehydrate_prompt_map` is the
  *reconnect*-time path and deliberately does neither. `_rehydrate_jobs`
  assumes one process per database — correct for `run_server.py`'s
  single uvicorn worker, but running with `workers > 1` would have each
  worker's startup mark the *other* workers' still-running jobs `failed`.
- **`generation_jobs.preset_id` has no `ON DELETE` clause, on purpose.**
  Deleting a used preset raises `sqlite3.IntegrityError`;
  `ComfyService.delete_preset` translates it into `PresetInUseError` and
  the route answers **409** naming the job count. Do not add `ON DELETE
  CASCADE` (it would destroy job history) and do not make the column
  nullable (it would orphan it).
- **`generation_jobs.panel_id` has no `REFERENCES` clause.** The `panels`
  table arrives in Phase B of the storyboard feature; with
  `PRAGMA foreign_keys = ON`, an INSERT naming a foreign key to a missing
  table fails at runtime, and SQLite cannot add a foreign key to an existing
  table without rebuilding it.
- **Storyboard domain (Phase B, reorganized 2026-08-17): six tables layered
  on top of ComfyUI's `workflow_presets`/`generation_jobs`.**
  `storyboards` (script + render settings, gained a free-form `notes TEXT`
  in the reorg) → `storyboard_subjects` (characters, LoRA + reference
  image) and `scenes` (ON DELETE CASCADE from storyboards) → `panels`
  (ON DELETE CASCADE from scenes) → `beats` (ON DELETE CASCADE from
  panels) → `beat_images` (ON DELETE CASCADE from beats, FK'd to
  `media(file_path)`). Since the shot/beat reorg, `panels` are thin
  H3-scene containers — `id`, `scene_id`, `sort_order`, `action`,
  `duration_s`, `image_loras`/`video_loras` (JSON lists, see the
  per-shot lora-stack bullet below), the `video_*` columns, timestamps —
  and every per-shot
  creative field (`shot_size`/`angle`/`lens`, `subject_ids`, `brief`,
  `prompt`/`prompt_locked`/`prompt_source`, `selected_image_id`) lives on
  `beats` instead: a metascan Shot (panel) maps to one H3 generation unit,
  a Beat maps to one H3 `[Shot n]` section, and keyframes/prompts are a
  per-beat concern. `panels.negative` and `panels.notes` were dropped —
  `storyboards.negative` is now the only negative prompt and `notes` is
  UI-only, consumed by no pipeline stage. `storyboards.folder_id`
  is `TEXT REFERENCES folders(id)` — `folders.id` is a uuid4 string, so this
  must never be declared `INTEGER` (a numeric-looking uuid would silently
  coerce and corrupt `add_folder_items` lookups); `_init_database` detects
  and rebuilds a pre-existing `INTEGER` column via the standard SQLite
  create/copy/drop/rename procedure, gated on reading the live DDL from
  `sqlite_master`. `media.hidden`
  (INTEGER, default 0) keeps generated variants out of the main grid until
  curated: `StoryboardRunner._ingest_outputs` inserts every rendered file
  hidden, `db.select_beat_image` unhides the chosen keeper and re-hides
  whatever was previously selected, and swapping the keeper is symmetric
  (old keeper re-hidden, new keeper unhidden). `GET /api/media` defaults to
  `hidden = 0`; pass `include_hidden=true` to see everything. `hidden` was
  added to both grid covering indexes (`idx_media_summary_added`,
  `idx_media_summary_modified`) alongside the column itself — see the
  covering-index rule above. **Every destructive path that cascades
  `beat_images` away must unhide their media rows first**, or the
  underlying files become permanently hidden with no path back (the only
  other unhide is `select_beat_image`, which needs a live beat to act
  on). `DatabaseManager._release_beats(conn, beat_ids, purge_images=False)`
  is the shared helper — it runs `UPDATE media SET hidden = 0` for the
  affected `beat_images.file_path`s and deletes the beats'
  `generation_jobs` rows (keyed by `beat_id`, so a restart can't re-adopt
  jobs for beats that no longer exist).
  `DatabaseManager._release_panels(conn, panel_ids, purge_images=False)`
  delegates to `_release_beats` for the panels' beats first, then deletes
  the panels' own (video) `generation_jobs` rows (keyed by `panel_id`).
  Both are called, inside the same transaction as the delete, from
  `delete_beat`/`delete_panel`/`delete_scene`/`delete_storyboard`, and from
  the replace functions (`replace_storyboard_structure`,
  `replace_storyboard_scenes`, `replace_scene_panels`,
  `replace_panel_beats`) — a re-parse or recompose destroys the old tree
  the same way a delete does. **Confirmed destructive recomposes purge
  (2026-08-21):** each replace function takes `purge_images: bool = False`
  and returns the purged native paths (tuple'd with the new ids); the
  runner wires it to the compose/parse `confirm` flag — accepting a
  `confirm_required` gate means the destroyed tree's generated media is
  deleted via `_purge_media_rows` and the files go to the OS trash via
  `metascan/utils/trash.py::remove_files_to_trash` (the shared helper
  `StoryboardService`'s delete routes also use; tests monkeypatch
  `metascan.utils.trash.send2trash`). The unconfirmed default keeps the
  old release-into-the-library semantics. Beats recompose stays
  beat-scoped either way — the shot's `panel_videos` clips survive it;
  shots/scenes recompose purges clips too.
  With `purge_images=True` (the `?purge_images=true` query flag on the
  storyboard/scene/panel/beat DELETE routes) the unhide is replaced by
  `_purge_media_rows`, run *after* the cascade delete in the same
  transaction: media rows are deleted (indices + `folder_items` cascade)
  and the native file paths returned up through `StoryboardService`,
  which moves the files to the OS trash (`send2trash`, unlink fallback).
  Files still referenced by a surviving `beat_images` row, a
  `storyboard_subjects.reference_path`, or a `scenes.reference_path` are
  unhidden instead of deleted —
  the media FK's `ON DELETE CASCADE`/`SET NULL` would silently destroy
  the other beat's image row / null the subject or scene reference.
  `delete_storyboard` also deletes the storyboard's "Storyboard: <name>"
  folder in the same transaction and returns its id so the route can
  broadcast `folder_deleted` on the `folders` WS channel. The frontend
  prompts via `DeleteImagesDialog.vue` (purge / keep-in-library / cancel)
  on every beat, panel, scene, and storyboard delete that affects
  generated images. The gated beats-recompose confirm is a separate,
  purpose-built inline banner in `ShotHeader.vue` (its own
  `confirmPending` ref, Continue/Cancel buttons) — not
  `DeleteImagesDialog.vue` — that re-posts the compose call with
  `confirm=true` on Continue.
  **No data migration for the reorg** — dev data is disposable by
  decision (spec §2.4): `user_version = 3` drops and recreates
  `panels`/`beats`/`panel_images→beat_images` inside `_init_database`
  (in FK-safe order, before the `CREATE TABLE IF NOT EXISTS` statements
  that follow), first unhiding any media the old `panel_images` table
  left hidden and deleting `generation_jobs` rows with a non-NULL
  `panel_id` (their panels are gone; a restart must not re-adopt them).
  Scenes, storyboards, subjects, and folders survive; users re-run the
  shots/beats stages. Idempotent — gated on the pragma, so it runs once.
- **Story engine composes outline → scenes → shots → beats as four staged
  VLM calls.** `StoryboardRunner.compose_story` runs the requested stages
  (a subset of `storyboard_story.STAGES`) inside `_compose_locked`, which
  holds `_synth_lock` for the whole run (mutually exclusive with
  `synthesize()`/`generate()`'s VLM-unload path) and emits `story_progress`
  (per-stage done/total), `story_stage_complete`, and `story_complete` on
  the `storyboard` WS channel; any exception stamps `_compose_stage` with
  the stage it failed in so `story_error` names the right stage even when
  the failure comes from `check_compose_gates` before the per-stage loop
  starts. `POST /api/storyboard/{id}/compose` calls `check_compose_gates`
  synchronously first and answers 409 `confirm_required` (or 400 for a
  non-gate `StoryboardError`, e.g. no premise) before creating the 202
  fire-and-forget task — outline (already has one) and scenes (rebuilding
  destroys panel identity) are gated whenever content already exists,
  shots are gated only when the target scenes already have panels, and
  **beats are gated too, since the shot/beat reorg gave them identity**:
  409 `confirm_required` when any target panel's beats already have
  `beat_images` rows or a locked prompt (`check_compose_gates` checks
  `beat.get("images") or beat.get("prompt_locked")` over the target
  panels' beats) — recomposing beats would otherwise silently destroy
  generated keepers and hand-edited prompts. The `beats` table
  (`ON DELETE CASCADE` from `panels`) stores dialog as a
  JSON array column and camera fields as free-text columns whose values
  `storyboard_story.py`'s validators constrain to a fixed enum, dropping
  anything else to NULL rather than raising. `subject_ids` also lives only
  on beats now — the beats-stage grammar gains per-beat `subjects` (roster
  names, validated and mapped to ids, unknown names dropped) and
  `shot_size`/`angle`/`lens`; the shots stage no longer emits a
  per-shot `subjects` field at all, since nothing stores it above the beat
  level. Beat durations are rescaled
  to fit each panel's `duration_s` entirely in code
  (`rescale_beat_durations`), never by the VLM. Grammars (`OUTLINE_GRAMMAR`,
  `SCENES_GRAMMAR`, `SHOTS_GRAMMAR`, `BEATS_GRAMMAR`) live in Python in
  `storyboard_story.py`; system prompts (`STORY_OUTLINE_SYSTEM`,
  `STORY_SCENES_SYSTEM`, `STORY_SHOTS_SYSTEM`, `STORY_BEATS_SYSTEM`) live in
  `data/meta_prompt.yml` and hot-reload through the same `PromptStore` as
  the render pipeline's prompts. **Story length is scaled by
  `storyboards.story_scale`** (`short`/`standard`/`extended`,
  `STORY_SCALES` in `storyboard_story.py`, default `standard` = the
  pre-scale behavior byte-for-byte). A scale moves three things together
  per `_SCALE_SPECS`: the GBNF repetition caps (arc 2–4/2–7/2–12, scenes
  1–4/2–8/2–12, shots per scene 1–4/1–6/1–12 — accessors
  `outline_grammar(scale)`/`scenes_grammar(scale)`/`shots_grammar(scale)`;
  the bare `*_GRAMMAR` constants stay the standard versions), the explicit
  shot-count numbers in the shots prompt (`pacing_guidance`'s
  `shot_factor` ×0.5/×1/×2 clamped to the scale's grammar ceiling — this
  explicit instruction is what binds in practice; premise-level "make it
  long" requests never beat it), and `stage_max_tokens(stage, scale)` so
  extended output isn't tail-truncated through `_loads_array`'s salvage.
  Beats never scale (bounded by the clip cap). The outline prompt gains a
  scale hint (extended: repeat `rising` arc entries — the arc/scene lint
  requires scenes' `arc_beats` to cover the outline arc exactly, so a
  long middle expresses itself as more scenes). `PATCH
  /api/storyboard/{id}` validates against `STORY_SCALES` (400); the UI
  exposes it in the create dialog (rides the create-then-PATCH follow-up),
  Settings, and the Compose dialog (commit-on-change PATCH, so changing
  it there and rebuilding stages regenerates at the new length).
- **Describe endpoints are review-only.** `POST /api/storyboard/subjects/{id}/describe`
  and `/scenes/{id}/describe` VLM-describe a subject/scene from its reference
  image(s) and return the parsed descriptor JSON without ever writing the
  DB — the frontend shows the suggestion and the user accepts it through the
  normal PATCH flow. `VlmClient.generate_text` takes `image_path` (single) or
  `image_paths` (multi-image, one `image_url` part per path in prompt
  reference order); passing both raises `ValueError`. `pick_vlm_model` in
  `metascan/core/vlm_select.py` is the shared model picker used by both the
  describe routes and `StoryboardRunner`. `storyboard_subjects.reference_path_2`
  and `scenes.reference_path` follow the existing `reference_path`
  POSIX-storage / `InvalidReferenceError` conventions. Grammars and
  validators live in `metascan/core/ref_describe.py`; its system prompts
  (`REF_DESCRIBE_SUBJECT_SYSTEM`, `REF_DESCRIBE_SETTING_SYSTEM`) are
  YAML-backed in `data/meta_prompt.yml` via the same hot-reloading
  `PromptStore`. The frontend persists a scene's reference on pick/clear and
  gates the Describe button on that persisted value, since the endpoint
  reads the reference from the DB rather than taking it as a request body.
- **H3 (MiniMax) video-prompt compiler splits pure logic from bounded VLM calls.**
  `metascan/core/h3_compiler.py` is pure (no I/O): it assigns
  reference/speaker labels, computes shot timelines, renders the
  deterministic sections (`subject_definitions`, `summary`,
  `retention_analysis`), builds the machine-readable scaffold, assembles
  the six-section document, and runs an expectation-driven lint over it.
  **`storyboard_subjects.sheet_ref`** (INTEGER 0/1, checkbox in the
  settings dialog's subject row) marks the first reference picture as a
  three-view character sheet: `render_subject_definitions` emits the
  sheet boilerplate ("…the person shown in `<Picture N>`, a three-view
  character reference sheet (full front, full back, facial close-up) of
  one single individual") with the panel's ACTUAL assigned labels and
  appends the description after it for extra identity detail — never
  hardcode `<Subject 1>`/`<Picture 1>` text into a description, the
  labels are per-panel and upload-order dependent.
  **`storyboard_subjects.pov_ref`** (INTEGER 0/1, checkbox next to
  sheet_ref) marks a subject's reference picture as the shot's
  first-person camera vantage (usually a partial torso view). Any panel
  whose roster contains a pov_ref subject *with a labeled reference
  picture* (`h3.pov_subject` — flagged-but-pictureless never activates,
  it only warns) compiles in POV mode: `compute_timeline(pov=True)`
  merges every beat into a single `[Shot 1]` (per-beat rescaled starts
  live on `Timeline.beat_starts` in both modes),
  `render_detailed_description` opens with the vantage lock ("The shot
  begins from `<Picture N>` and holds that exact vantage … POV, Static
  Shot, eye height and lens unchanged, horizon line constant") and
  renders beats as in-shot "At MM:SS.mmm, …" prose — per-beat
  camera/framing sentences and `is_cut` phrasing are deliberately
  suppressed (any later motion text would drift the vantage); dialog and
  sound render as usual. `render_subject_definitions`/`render_summary`/
  `render_retention_analysis` each self-detect the POV subject and emit
  the vantage boilerplate / "continuous single-take POV shot" clause /
  first-frame-anchor retention line ("serves as the target video's first
  frame and as the fixed camera vantage"). `_compile_panel` passes
  `beats=None` to `build_expectations` in POV mode (camera_vocab must
  not demand the suppressed motion phrases) and appends
  `h3.pov_warnings` (advisory-only `pov_no_reference`/`pov_multiple`;
  first flagged-with-picture subject by sort_order wins the vantage) to
  the lint issues. MiniMax only retains a POV camera across beats when
  the whole clip is one continuous shot with intra-shot timestamps —
  that constraint is the entire reason for the merge.
  **`detailed_description` is deterministic — the beat script IS the shot
  script.** `compute_timeline` maps every beat to its own `[Shot n]`
  (`is_cut` only tunes phrasing — "the shot cuts." vs "continuing without
  a cut." — never grouping), and `render_detailed_description` renders
  each beat's action/camera/dialog/sound verbatim with ref-guide cut-time
  timestamps on every shot after the first; the VLM never paraphrases
  shot structure. `StoryboardRunner._compile_panel` makes exactly one VLM
  call per panel: the sound section, grammar-constrained JSON
  (`h3.SOUND_GRAMMAR`, `{overall_soundscape, non_diegetic_music}`). The
  lint still runs as a safety net (a word-count shortfall is a warning,
  not an error — a terse beat script legitimately compiles short).
  `compile_video` shares `StoryboardRunner._synth_lock` with `synthesize`
  and emits `compile_progress`/`compile_complete`/`compile_error` on the
  `storyboard` WS channel, mirroring the synthesize contract. A lint
  failure still writes the assembled document to
  `panels.video_prompt` (with the lint messages in `video_prompt_warnings`)
  rather than discarding the draft; a per-panel exception
  (`VlmError`/`TimeoutError`/`RuntimeError`/`h3.H3Error`) leaves
  `video_prompt` untouched and records only the exception message in
  `video_prompt_warnings`, so other panels in the batch keep compiling.
  `PATCH /api/storyboard/panels/{id}` mirrors the `prompt`/`prompt_locked`
  server-wins rule for `video_prompt`: sending a non-null `video_prompt`
  forces `video_prompt_locked=1, video_prompt_source="user"`; sending
  `video_prompt: null` clears `video_prompt_source`, `video_prompt_locked`,
  and `video_prompt_warnings` together. `video_target` accepts only
  `"minimax"` and `video_mode` only `t2va`/`i2va`/`fl2va`/`ref2va` (400 on
  anything else); `POST /{id}/compile` 404s on an unknown storyboard and
  400s synchronously when `video_target != "minimax"`, before creating the
  202 fire-and-forget task. `metascan/core/video_targets.py::shot_cap` is
  the single coupling between story composition and the video dialect — the
  shots-composition stage reads it for per-shot duration guidance and the
  frontend's `PacingStrip.vue` mirrors the same constant
  (`VIDEO_TARGET_CAPS`/`DEFAULT_SHOT_CAP` in `types/storyboard.ts`) to warn
  when a shot's beats exceed the target's clip cap. The two MiniMax H3
  prompt-format guides are vendored verbatim at
  `data/prompt_guides/minimax-h3/` and are the format authority every
  renderer in `h3_compiler.py` follows.
- **Workflow presets carry an optional dialect tag and are validated per
  (target, mode).** `workflow_presets.video_target`/`video_mode` (nullable
  TEXT, idempotent adds that must stay AFTER the kind-CHECK rebuild block
  in `_init_database` — the rebuild recreates the table from an explicit
  column list and would silently drop columns added before it).
  `metascan/core/workflow_validation.py` is the pure validation layer over
  `comfy_bindings`: `validate_workflow(workflow, kind, target, mode)`
  returns a `ValidationReport` of every error/warning at once (duplicate
  titles, unknown `MS_*` titles, missing required titles, missing widgets)
  plus `TitleFix` suggestions — the one auto-fix computable without
  knowing a target's node classes is renaming a misspelled `MS_*` title
  via fuzzy match (`apply_fixes`). Per-(target, mode) validators live in
  its `_VALIDATORS` registry; only `("minimax", "ref2va")` is implemented
  (warnings for missing `MS_REF_IMAGE`/`MS_AUDIO`/`MS_DURATION`, unused
  keyframe slots) — an unregistered pair gets a single `no_validator`
  warning, never an error, so future targets don't hard-fail. `POST
  /api/comfy/presets` blocks on validation errors with a structured 400
  (`{code:"validation_failed", findings, fixes, fixed_workflow?}`) and
  returns `warnings` on success; `POST /api/comfy/presets/validate` is
  the pure dry-run the registration dialog's Validate/Apply-fixes flow
  uses. `StoryboardRunner.generate_video` rejects a preset whose tag
  mismatches the storyboard's target/mode (untagged legacy presets pass),
  which is what makes the association binding. `VIDEO_TARGETS`/
  `VIDEO_MODES` in `workflow_validation.py` are the canonical axes to
  extend when a new dialect (ltx, wan, …) lands.
- **Video generation drives ComfyUI with a third preset kind, `ref2v`.**
  `workflow_presets.kind`'s CHECK gained `'ref2v'` alongside `t2i`/`ref`; a
  dev DB with the old two-value CHECK baked into its DDL is detected via
  `sqlite_master` and rebuilt (create/copy/drop/rename), same procedure as
  the `storyboards.folder_id` migration — the pending init transaction is
  committed first so `PRAGMA foreign_keys = OFF` actually takes effect
  before the `DROP TABLE`. `ref2v` only requires `MS_POSITIVE`/`MS_SEED`/
  `MS_SAVE`; everything else (`MS_REF_IMAGE`/`_2`/`_3`, `MS_FIRST_FRAME`,
  `MS_LAST_FRAME`, `MS_AUDIO`/`_2`, `MS_DURATION`, `MS_NEGATIVE`, `MS_LORA`)
  is optional per-workflow. `Bindings.latent` is now `Optional[str]` —
  `t2i`/`ref` still require `MS_LATENT` via `_REQUIRED_TITLES`, only `ref2v`
  workflows may omit it. `ComfyClient.upload_file` generalizes the old
  image-only ref upload to any file (video/audio included), keyed by
  content sha256 so a repeated input uploads once per run regardless of
  how many panels reference it; `collect_outputs` scans a save node's
  history entry across every `_OUTPUT_KEYS` key (`images`/`gifs`/`videos`/
  `video`/`audio` — video-combine nodes commonly emit the same mp4 under
  both `gifs` and `videos`) and de-dupes on the `(filename, subfolder,
  type)` identity `/view` itself resolves by; a downloaded file whose
  suffix `Scanner` doesn't recognize (e.g. a video node's sidecar `.json`)
  is still written to disk but excluded from ingest and the returned list.
  `StoryboardRunner.generate_video` validates every target panel upfront —
  compiled `video_prompt` present, reference/audio counts against the
  preset's actual slot count, voice files exist on disk, `video_anchor`
  prerequisites for `i2va`/`fl2va` — and raises one `StoryboardError`
  naming every failing panel before submitting anything; only frame
  extraction/upload are runtime-only failures, and those skip just that
  panel (`generate_video` returns `{"jobs": [...], "skipped": [{panel_id,
  error}, ...]}`, not a hard failure). `_panel_subjects` — since the
  shot/beat reorg, the union of every one of the panel's beats'
  `subject_ids` ∪ every subject a beat's dialog names (panel-level
  `subject_ids` no longer exists) — is factored out of
  `_compile_panel` specifically so `compile_video`'s prompt text and
  `generate_video`'s uploaded reference pictures/audio can never disagree
  about who's in the shot. Rendered clips ingest as **`panel_videos`** rows
  (`_ingest_video_outputs` — a clip covers the whole shot, so it is
  panel-scoped, never a beat candidate), hidden like images — but the
  frontend surfaces hidden *clips* inside the storyboard's manual-folder
  view (`stores/media.ts` fetches `include_hidden=true` always and
  `displayedMedia` excepts `hidden && is_video && ∈ activeManualItemSet`),
  so clips are folder-only in the library. `_init_database` idempotently
  relocates any video-suffixed `beat_images` rows left by the pre-
  `panel_videos` first-beat keying (clearing keeper pointers that
  referenced them) and re-asserts `hidden = 1` for every path in
  `panel_videos` on each start — release paths delete the rows in the
  same transaction they unhide, so released clips are never re-hidden. `_release_panels` unhides/purges
  `panel_videos` media the same way `_release_beats` handles beat images,
  and `_purge_media_rows`'s survival checks include `panel_videos` —
  while beats-recompose (`replace_panel_beats`) leaves clips untouched,
  which is the point of panel scoping. The `storyboard` WS channel's
  `panel_videos_changed` (`{storyboard_id, panel_id, files}`) signals new
  clips; the frontend shows them in `ShotHeader.vue`'s "Takes" strip and
  the outline rail's per-shot 🎬 count.
  The side panel's "Anchor changed since the last
  compile" chip compares live `video_anchor` against `video_compiled_anchor`
  (the anchor recorded at compile time), not against re-validating the
  actual anchor prerequisites. `<Audio N>` reference/retention lines
  (`render_audio_definition_lines`/`render_audio_retention_lines`) are
  emitted only for subjects that both have a `voice_ref_path` and actually
  speak in the panel (`_active_audio_entries` filters `RefPlan.audio_labels`
  against `SpeakerPlan.lines`) — that same active set is what
  `active_audio_refs` uploads, so the document and the ComfyUI submission
  can't drift apart. `<Audio N>` retention lines lint against their own
  §4.2 marker vocabulary (`fully_copy`/`partially_copy`/`reference`/
  `weak_reference`), distinct from the §4.1 `<Subject N>`/`<Picture N>` set
  — `_lint_retention_and_sound` picks the marker set per-line based on
  whether the line starts with `<Audio`. `fl2va`'s second keyframe anchor
  (the compiled document's `<Picture N+1>` end-frame reference) is a
  documented scope cut: `generate_video` only ever populates `first_frame`
  (`MS_FIRST_FRAME`) for `keeper`/`prev_last` anchors — `MS_LAST_FRAME` is
  bound and written by `apply_overrides` when a preset's workflow wires it,
  but nothing in the runner ever sets `GenerationParams.last_frame`, so an
  `fl2va` workflow needing its second anchor must supply it by hand in
  ComfyUI.
- **Shot-list templates are a per-scene branch of the shots stage (spec
  `docs/plans/refactor-spec-visual-story-quality.md`, Phase E).**
  `metascan/core/shot_templates.py` is pure: it loads `data/templates/*.json`
  (lazily, cached; `reload_templates()` drops the cache) and validates every
  camera field against the REAL code vocabulary (`SHOT_SIZE_VALUES`,
  `ANGLE_VALUES`, `COMPOSITION_VALUES`, `CAMERA_MOTION_VALUES`, …) at load
  time — a template written in another vocabulary (`"EYE"`, `"THIRDS_L"`,
  `"STATIC"`) raises `TemplateError` naming the path instead of silently
  nulling through the beats validator. The template owns every structural
  field (`duration_s`, `kind`, `is_cut`, `cast` roles → `subject_ids`,
  `dialog_slot`, camera); the VLM fills only prose. **The user picks the
  template per scene** (`scenes.template_id`, set through `PATCH
  /api/storyboard/scenes/{id}`, 400 on an unknown id) — there is no
  standalone apply-template route any more, and no `apply_template` /
  `check_template_gates` on the runner. The **shots stage is the single
  path**: per target scene, a set `template_id` runs
  `StoryboardRunner._template_scene` (bind roles — grammar alts = the
  scene's castable character names → `instantiate` → per-section fill
  (`fill_grammar` bakes the slot count in and makes a `dialog_slot`'s
  dialog `string`, not `nullable`, so an unfilled line is structurally
  impossible) → `conform` (**hard-fails** with
  `TemplateConformanceError`, the one exception to
  lint-never-hard-fails) → `replace_scene_panels` + `replace_panel_beats`),
  a NULL one runs the free-form VLM shots call. Either way the scene gets a
  `composed_from = {"stage": "shots", "template_id": …, "outline_hash": …,
  "at": …}` provenance stamp (same dict shape the scenes stage writes), and
  `story_stage_complete` for `shots` carries `template_scenes: [scene_id,
  …]` — `_run_stage` returns `(count, warnings, extra)` and `_compose_locked`
  spreads `extra` into the event. The **beats stage skips template-built
  scenes** (a `template_id` plus beats on every panel) unless the caller
  passes an explicit `panel_ids` — the "Re-beat shot" button always wins.
  `check_compose_gates` with `"shots"` (and **not** `"scenes"` — a full
  "Compose all" recreates every scene row with `template_id` NULL, so the
  selections would be discarded anyway) runs `templates.validate_assignment`
  over every target scene **before** the confirm short-circuit and raises a
  `StoryboardError` (400) listing every problem, so `confirm=true` cannot
  push a bad selection through; a duration mismatch between the template
  and the scene's share of the outline is a *warning* by decision (the user
  chose the template knowing its length). `GET /api/storyboard/{id}` runs
  `templates.annotate_tree`, attaching `template_problems` /
  `template_warnings` / `outline_stale` per scene plus a board-level
  `outline_hash`. Routes: `GET /api/storyboard/templates` (registered
  BEFORE `/{storyboard_id}` so the literal path wins the match).
  Supporting schema: `storyboard_subjects.subject_type`
  (`character|location|prop`, `user_version = 4` backfills via
  `infer_subject_type`; only characters are castable — `story.
  castable_subjects` is the roster the beats stage and templates use),
  `scenes.template_id` / `scenes.brief` / `scenes.composed_from`,
  `scenes.function` (`SCENE_FUNCTION_VALUES`, emitted by the scenes stage,
  the template selection key), `beats.kind`, and `panels.video_compiled_at`
  (the frontend's "beats changed since compile" chip). `render_retention_
  analysis(..., beats=)` is per-beat: a subject's `(appears in …)` list
  follows the beats that cast it (Phase A1). `story.lint_scene_dialogue`
  runs once per scene after its beats stage for verbal functions
  (`VERBAL_FUNCTIONS`), warnings only.
- **Stored paths vs. API paths in the storyboard tree.** `beat_images.file_path`
  is stored POSIX (same convention as `media.file_path` and `folder_items.file_path`).
  `get_storyboard_tree` and `list_beat_images` convert it through
  `to_native_path` before returning, mirroring `get_folder`'s precedent —
  `GET /api/storyboard/{id}` and `GET /api/media` must agree on path shape.
  `StoryboardRunner._ingest_outputs` builds its `beat_images_changed` WS
  payload from the same `to_posix_path`-normalized value it inserted
  (converted back with `to_native_path`), not the raw pre-conversion string
  from the ComfyUI `job_outputs` event. `storyboard_subjects.reference_path`
  FKs `media(file_path)` the same way — `create_subject`/`update_subject`
  run it through `to_posix_path` before the INSERT/UPDATE, and an unknown
  path (raw `sqlite3.IntegrityError` from SQLite) is translated to
  `InvalidReferenceError` → HTTP 400 in `StoryboardService`, not a 500.
- **Deleting a subject strips every non-text reference; the caller picks
  what happens to the beats.** `DatabaseManager.delete_subject(subject_id,
  mode)` (`SUBJECT_DELETE_MODES = unlink|content|purge`, returns
  `(deleted, purged_paths)` like `delete_beat`) first collects every beat
  of the storyboard that casts the subject (`beats.subject_ids`) or has a
  dialog line with its `subject_id` (`_subject_referencing_beats`; name
  mentions in prose never count). `unlink` (default) rewrites those beats
  in place — id stripped from `subject_ids`, dialog lines keep their text
  with `subject_id: null`, `updated_at` bumped so open editors resync;
  `content` deletes them through `_release_beats`, then deletes any shot
  left beat-less (`_release_panels`) and any scene left shot-less;
  `purge` is `content` with the media trashed. Never restore the old
  bare `DELETE FROM storyboard_subjects` — dangling ids survived in beat
  JSON, and `BeatCard`'s cast toggles re-PATCHed them. `GET
  /api/storyboard/subjects/{id}/references` (`{beat_ids, image_count}`)
  is what `StoryboardSettingsDialog` consults to decide between a plain
  confirm and `DeleteSubjectDialog.vue`'s three-way choice; `DELETE
  /subjects/{id}?mode=` 400s on an unknown mode. **The outline compose
  stage dedupes VLM-emitted subjects against the WHOLE roster, not the
  castable (character-only) one** — the outline prompt lists every
  existing subject and the model echoes them back, so checking only
  characters re-created each location/prop as a duplicate character on
  every outline rebuild. New outline subjects get `infer_subject_type`.
  A subject the user deleted whose name the premise still carries does
  legitimately come back on an outline rebuild (the stage builds the
  roster from `source_text`).
- **`beats.prompt_locked` / `prompt_source` gate re-synthesis.** Moved down
  from panels in the shot/beat reorg — keyframes are a per-beat concern now.
  `prompt_source` is one of `'brief'` (deterministic template, no VLM),
  `'llm'` (VLM-composed), or `'user'` (hand-edited). `StoryboardRunner.synthesize`
  skips any beat with `prompt_locked=1` unless the caller explicitly named
  it in `beat_ids` with `force=true`. `PATCH /api/storyboard/beats/{id}`
  sets `prompt_locked=1, prompt_source="user"` server-side whenever the body
  includes `prompt` — the caller cannot leave a hand-edited prompt unlocked
  by also sending `prompt_locked=false` in the same request; the server wins.
- **Style block is concatenated after synthesis, never paraphrased.**
  `compose_brief` / `build_render_messages` never see `storyboards.style_block`
  — `StoryboardRunner.synthesize` calls `finalize_prompt(text, style_block)`
  *after* the VLM (or deterministic brief fallback) produces the per-panel
  prompt, appending the style block verbatim. This keeps the global
  look-and-feel from drifting through paraphrase across dozens of separate
  LLM calls (spec §7.2).
- **Per-shot lora stacks live on `panels.image_loras` / `panels.video_loras`**
  (JSON `[{name, strength}, ...]`, `NOT NULL DEFAULT '[]'`; PATCH rejects
  `null` — clear with `[]`). `generate()` injects `image_loras`,
  `generate_video()` injects `video_loras`, both via
  `GenerationParams.loras` into the preset's **`MS_LORA_STACK`** node — a
  stackable loader (rgthree Power Lora Loader) with dynamic `lora_N`
  entries, so the title has no required-widget check. `apply_overrides`
  **owns** that node: baked-in `lora_N` entries are cleared and replaced
  with exactly the supplied list (an empty list clears them), and loras
  supplied against a preset with no `MS_LORA_STACK` raise `BindingError`
  (runner validation catches this upfront per panel). The single-lora
  `MS_LORA` + subject `lora_name` path is unchanged and coexists.
  Bindings are resolved from `workflow_json` at use time —
  `ComfyClient._load_preset` and `StoryboardRunner.generate` both call
  `resolve_bindings` instead of deserializing the stored
  `workflow_presets.bindings` snapshot (registration-time validation
  artifact only), so presets registered before a new optional `MS_*`
  title existed pick it up without re-registration. `GET /api/comfy/loras`
  proxies ComfyUI's `/object_info/LoraLoader` for the frontend picker
  (`LoraListEditor.vue`, mounted once in `ShotHeader.vue` for the shot's
  video loras — `image_loras` remain in the schema and PATCH API but have
  no UI editor since the video-only UX pass); it returns `[]`
  when ComfyUI is unreachable and the picker degrades to free text.
- **Deterministic per-beat seeds.** `beat_seed(base_seed, panel_sort_order,
  beat_sort_order, variant_index) = base_seed + (panel_sort_order * 100 +
  beat_sort_order) * 1000 + variant_index`
  (`metascan/core/storyboard_brief.py`, replaces the old `panel_seed`).
  Collision-free for < 100 beats/shot and < 1000 variants/beat. A reroll
  just advances `variant_index` (read from `count_beat_images(beat_id)`
  plus in-flight jobs at submit time — see below), so the same beat always
  starts from the same seed run-to-run.
- **`bucket_dims` is keyed on `TargetModel`, not architecture.** `sd` and
  `pony` (`SDXL_TARGETS`) snap to the nearest of five trained SDXL buckets;
  every other target (Flux and later) computes the nearest multiple-of-16
  dimensions at a ~1MP budget. Both `POST /api/storyboard` and
  `PATCH /api/storyboard/{id}` call `bucket_dims(aspect_ratio, target_model)`
  and answer 400 with the raw `ValueError` message on an unsupported aspect
  ratio — validation happens at save time, not at generate time.
- **Runner layering keeps the ComfyUI driver storyboard-agnostic.**
  `StoryboardRunner` (`metascan/core/storyboard_runner.py`) is the only
  layer that knows about subjects/scenes/panels/beats; `comfy_client.py`
  stays a generic job driver with no storyboard imports. Correlation flows
  one way: `generate()` passes both `panel_id` and `beat_id` into
  `ComfyClient.submit(..., panel_id=..., beat_id=...)`,
  which round-trips them onto `generation_jobs.panel_id`/`.beat_id` (still
  images set both; video jobs set `panel_id` only, `beat_id` NULL — video
  stays per shot); when ComfyUI
  finishes, `handle_job_event` (registered via `comfy_client.on_job_event`
  in the lifespan) reads the `job_outputs` payload, looks up the job's
  `beat_id`, and ingests the produced files as `beat_images` rows (image
  jobs) or `panel_videos` rows (`beat_id` NULL — video jobs). Events
  the runner emits (`folder_created`, `folder_items_changed`,
  `beat_images_changed`, `panel_videos_changed`, `synthesis_progress`) go
  out through its own
  `on_event` callback list, wired straight to `ws_manager.broadcast_sync` in
  the lifespan — `backend/api/storyboard.py` never re-broadcasts them, only
  translates exceptions to HTTP.
- **`generation_jobs.output_dir`** holds the per-beat directory
  `StoryboardRunner.generate` computes
  (`<comfy.output_root>/<storyboard-slug>/scene_NN/panel_NN/beat_NN/`) so a
  job's files land next to their beat instead of a flat `comfy.output_root`.
  Non-storyboard submits (`POST /api/comfy/submit`) leave it NULL and the
  ComfyUI driver falls back to `comfy.output_root` directly.
- **Lifespan shuts the event source down before its consumer.** The
  shutdown block in `backend/main.py`'s `lifespan` calls
  `comfy_client.shutdown()` **before** `storyboard_runner.aclose()`
  (each in its own try/except). `comfy_client.on_job_event` fans `job_outputs`
  out to both `ws_manager.broadcast_sync` and
  `storyboard_runner.handle_job_event`, and the latter schedules a
  fire-and-forget ingest task; closing the runner first would leave a
  window where a collect task completing between the two shutdowns spawns
  an ingest task nobody ever awaits.
- **`generate()`'s per-beat seed accounts for in-flight jobs, not just
  ingested ones.** `beat_seed`'s `variant_index` used to come from
  `count_beat_images(beat_id)` alone (`panel_seed`/`count_panel_images`
  before the shot/beat reorg), which only counts rows already
  ingested from a *finished* job — two `generate()` calls (e.g. two
  rerolls) for the same beat before the first has ingested read the same
  count and submitted identical seeds. `variant_base` is now
  `count_beat_images(beat_id) + batch_size * len(queued/running jobs for
  that beat)`. Relatedly, every `StoryboardRunner` call into
  `db.list_generation_jobs` (this one, plus `cancel()`) passes
  `limit=10000` explicitly — the default `limit=100` silently truncates and
  would under-cancel or under-count on a storyboard with more in-flight
  jobs than that (mirrors `ComfyClient._rehydrate_jobs`'s precedent).
  `latest_jobs_for_beats` (used by `generate()`'s `only_failed`) and
  `latest_jobs_for_panels` (used by `generate_video()`'s `only_failed`,
  which still targets panels — video is per shot) both have no `limit` —
  each is one row per beat/panel by construction, so both are exempt.
- **`generate()` waits for a live `synthesize()` before unloading the
  VLM.** Both share `StoryboardRunner._synth_lock`: `synthesize()` holds it
  for its entire run, `generate()` acquires it only around the
  `unload_vlm_during_generation` shutdown call (never across the submit
  loop — submits don't need the VLM and shouldn't block on synthesis of an
  unrelated panel). Without this, `generate()` could tear the VLM out from
  under an in-progress `synthesize()` call.
- **`stores/storyboard.ts` correlates ComfyUI jobs to panels/beats
  client-side.** `refreshActiveJobs()` rebuilds `jobToPanel` and
  `jobToBeat` from `GET /api/comfy/jobs`
  (`comfyApi.listJobs('queued'|'running', 1000)`) filtered to the current
  tree's panel ids and beat ids — this is what survives a page reload
  mid-generation, since there's no other durable client record of
  in-flight jobs. Live
  updates then come off the `comfy` WS channel: `job_update` moves a panel
  in/out of `panelJobState` (`done`/`cancelled` clears it), `job_progress`
  sets `{state: 'running', value, max}`; `job_outputs` is ignored on this
  channel — image ingestion is signaled separately. The `storyboard` channel
  drives refreshes: `beat_images_changed` and `synthesis_complete` both
  trigger a full `refresh()` (no per-panel GET exists, and refresh preserves
  selection), `synthesis_progress` updates the running counter in place, and
  `synthesis_error` surfaces the message without refetching. Both handlers
  drop events whose `storyboard_id` doesn't match the loaded `tree.value.id`
  — necessary because `attachWs()` is called from `StoryboardView`'s
  `<script setup>` on every mount (no module-level "already attached"
  guard), so switching boards must not let a stale board's events leak in.
  Keeper selection always goes through `selectImage(beatId, …)` →
  `POST /api/storyboard/beats/{id}/select` — never
  `PATCH /api/storyboard/beats/{id}`,
  which has no concept of `beat_images` and cannot flip `media.hidden` on
  the old/new keeper. (No UI calls `selectImage` since the video-only UX
  pass removed the keeper strip; the rule binds any reintroduction.)
- **Storyboard UX is video-only (2026-08-20).** All image-generation UX
  was removed from the frontend: the "Generate all" / per-beat Reroll /
  Re-synth buttons, the per-beat prompt editor (+ `BeatPromptDialog.vue`,
  deleted), the candidates/keeper strip and keeper thumbnails
  (`PacingStrip.vue` segments and the outline rail no longer render
  images), the Image LoRAs editor, the subject LoRA name/strength inputs,
  and the create/settings dialog fields that only fed the image path
  (target model, stills preset, batch size, style block, negative, image
  name prefix — verified: `generate_video` reads `base_seed` but not
  `negative`/`style_block`). `PresetRegistrationDialog.vue` registers
  `ref2v` only. This was strictly a frontend pass: the backend endpoints
  (`/synthesize`, `generate()`, `/beats/{id}/select`), the store actions,
  and the DB columns all remain — `CreateStoryboardDialog.vue` silently
  sends a default `target_model` because the column is NOT NULL, and the
  `DeleteImagesDialog.vue` purge/keep flows are kept because legacy
  boards still hold generated images the delete cascades must handle.
- **Downstream-dependency prompts are store-driven, not editor-driven.**
  `frontend/src/utils/storyboardDeps.ts` is the single table of which
  editable fields feed which recompute stage — shot `action`/`subtext`/
  `is_turn` → beats compose; scene descriptors (name, subtitle, setting,
  location, mood, lighting, time_of_day, function) → shots(+beats)
  compose; every beat field the H3 compiler renders → video-prompt
  compile — with `downstreamFor(entity, changedFields)` as the pure
  query. `stores/storyboard.ts::noteDownstream` runs after every
  *successful* `patchPanelFields`/`patchSceneFields`/`patchBeatFields`
  (so every commit-on-change handler is covered without touching it),
  skips when nothing downstream exists yet (shot has no beats, scene has
  no shots, shot has no compiled `video_prompt`), and merges repeated
  edits into one prompt per `(kind, target)` key. `ShotHeader.vue`
  renders the beats/compile prompts inline (accept → the existing
  `rebeat()`/`compile()` paths, so the 409 `confirm_required` flow still
  gates destructive re-beats); `SceneEditDialog.vue` stays open after Save
  to offer "Rebuild shots" (`composeStory({stages:['shots','beats'],
  scene_ids})`). `composeStory`/`compileVideo` clear the matching prompts
  on success and `load()` clears them all on a board switch. Premise /
  story-scale edits deliberately do NOT prompt — outline rebuild is
  whole-board destructive and lives in the Compose dialog. When a stage
  starts reading a new field, add it to the table; never re-add per-editor
  ad-hoc prompts.
- **Detail editors with local commit-on-change copies must resync on id +
  updated_at, not id alone.** `ShotHeader.vue` (the shot header — action,
  subtext) and `BeatCard.vue` (framing, subject picker, camera/dialog/
  sound — the per-beat editing surface since the shot/beat reorg moved
  those fields off panels) each keep a local
  editable ref
  per text/select field (bound `:value` + `@change`, not `v-model`) so an
  in-flight edit survives the store's optimistic `Object.assign`. Resyncing
  only when the selected panel's/beat's *id* changes misses every
  server-side rewrite of the panel/beat currently open — a compose pass
  or another tab's PATCH
  that rewrites a field in place never reaches the
  textarea, and a later blur then PATCHes the stale (often empty) local
  value back over the server's write, destroying it. Each field pairs its
  local ref with a "last synced from server" snapshot ref, updated
  together by the field's own commit handler; the resync watcher fires on
  `[panel.value?.id, panel.value?.updated_at]` (updated_at bumps on every
  successful PATCH, including server-driven ones) and only overwrites a
  field whose local ref still equals its snapshot — i.e. no pending
  uncommitted edit for that specific field. Apply the same pattern to any
  other detail editor that caches server fields in local commit-on-change
  refs.
- **Storyboard PATCH routes use `exclude_unset`, not `exclude_none`.**
  `backend/api/storyboard.py`'s five PATCH routes (storyboard, subject,
  scene, panel, beat) call `body.model_dump(exclude_unset=True)` so an explicit
  JSON `null` in the request body clears a nullable column (`preset_id`,
  `shot_size`, `notes`, `lora_name`, …) instead of being silently dropped —
  a field simply absent from the body is still left untouched. Each route
  runs the result through `_reject_null_for_required` first, which 400s
  (naming the field) if the caller sent `null` for a `NOT NULL` column
  (`name`/`aspect_ratio`/`target_model`/`architecture`/`base_seed`/
  `batch_size`/`pacing`/`story_scale` on storyboards;
  `name`/`description`/`sort_order` on
  subjects; `name`/`sort_order` on scenes; `sort_order`/`action`/
  `duration_s`/`video_prompt_locked` on panels; `sort_order`/`duration_s`/
  `action`/`is_cut`/`dialog`/`subject_ids`/`prompt_locked` on beats —
  `subject_ids`/`prompt_locked` moved from the panel list to the beat list
  in the shot/beat reorg) — otherwise that would reach
  SQLite as a raw NOT NULL constraint violation (500). Frontend callers
  that want to send an explicit clear (e.g. `StoryboardSettingsDialog`'s
  preset picker sending `preset_id: null` for "None") must diff against
  `null` as a real change, not skip it as falsy.

## Development Rules

### Python
- **Formatter:** `black` (v25.11.0 — must match in both requirements.txt and requirements-dev.txt)
- **Linter:** `flake8` on `metascan/ backend/ tests/` — fatal errors (E9, F63, F7, F82) must be zero; style warnings are non-fatal (`--exit-zero`)
- **Type checker:** `mypy` with `python_version = 3.11`, strict on `metascan/core/*`
- **Tests:** `pytest` — 175 tests, all must pass. `tests/test_inference_client.py` spawns a fake NDJSON worker (no CLIP required) to exercise the live-inference subprocess wiring. `tests/test_folders_{db,api}.py` cover DB CRUD + REST handlers against an isolated temp DB using `fastapi.testclient.TestClient`. `tests/test_hardware.py` (42 tests) covers probes + tier classification + feature gates + the `detect_hardware`/`report_to_dict`/`select_torch_device` aggregator. `tests/test_models_hardware_api.py` patches `detect_hardware` against fake reports to exercise `/api/models/hardware` + the `gates` payload of `/api/models/status` via `TestClient`. `tests/test_embedding_device.py` stubs `_torch` and patches `detect_hardware` to verify `_resolve_device` honours preference + auto-picks CUDA/MPS/CPU correctly.
- **Python version:** 3.11.x only — not 3.12, not 3.13+. `setup.py` declares `python_requires=">=3.11,<3.12"`, CI builds 3.11, and `install.sh` refuses anything else. 3.13+ cannot work (pinned Pillow 10.2.0 has no wheel past cp312 and its sdist fails to build); 3.12 resolves but is untested, so it is not supported.
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
2. Add state/open flag in `views/LibraryView.vue` (or `views/StoryboardView.vue` for a storyboard-only dialog) — `App.vue` is just the router shell (`<router-view>` + `ToastHost` + the folders WS bridge) and holds no dialog state.
3. Add button/shortcut trigger in `ContentSearchBar.vue` (the header action row).
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
