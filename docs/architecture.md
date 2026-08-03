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

- **`media`** — serialized Media object as JSON, plus favorite status, playback speed, and a `hidden` flag (see below).
- **`indices`** — inverted index for fast filtering (`source`, `model`, `extension`, `path`, `tag`, `prompt`, `lora`). Tag rows carry a `source` column (`'prompt'` / `'clip'` / `'both'`) so CLIP-derived tags survive rescans.
- **`media_hashes`** — perceptual hashes and CLIP embedding status.
- **`folders`** — `(id, kind ∈ {manual,smart}, name, icon, rules JSON, sort_order, created_at, updated_at)`.
- **`folder_items`** — `(folder_id, file_path, added_at)` with `ON DELETE CASCADE` on both sides.
- **`storyboards`** — script + render settings: `name`, `source_text`, `aspect_ratio`, `style_block`, `negative`, `target_model`, `architecture`, `preset_id` (→ `workflow_presets`), `base_seed`, `batch_size`, `folder_id` (→ `folders`, `ON DELETE SET NULL`).
- **`storyboard_subjects`** — recurring characters/props: `storyboard_id` (`ON DELETE CASCADE`), `name`, `description`, `lora_name`, `lora_strength`, `reference_path` (→ `media.file_path`, `ON DELETE SET NULL`), `sort_order`.
- **`scenes`** — `storyboard_id` (`ON DELETE CASCADE`), `sort_order`, `name`, `subtitle`, `setting`, `location`, `time_of_day`, `mood`, `lighting`, `notes`. `subtitle` is display-only; `setting` is woven into every panel brief (`SETTING:` line in `compose_brief`).
- **`panels`** — one generated shot: `scene_id` (`ON DELETE CASCADE`), `sort_order`, `shot_size`, `angle`, `lens`, `action`, `subject_ids` (JSON array), `notes`, `brief`, `prompt`, `prompt_locked`, `prompt_source`, `negative`, `selected_image_id` (→ `panel_images.id`, `ON DELETE SET NULL`).
- **`panel_images`** — one rendered variant: `panel_id` (`ON DELETE CASCADE`), `file_path` (→ `media.file_path`, `ON DELETE CASCADE`), `seed`, `variant_index`, `prompt_used`, `preset_id`, `comfy_prompt_id`.

`media.hidden` (`INTEGER NOT NULL DEFAULT 0`) keeps storyboard-generated
variants out of the main grid until curated: ingest inserts every rendered
file hidden, selecting a panel's keeper unhides it and re-hides whatever
was selected before. `GET /api/media` filters `hidden = 0` by default;
`include_hidden=true` opts back in. `hidden` is part of both grid covering
indexes (see below) so the filter doesn't force a main-table scan.

Deleting a panel/scene/storyboard releases its generated images into the
library by default (media rows unhidden), or — with `purge_images=true` on
the DELETE route — removes them entirely: media rows deleted in the same
transaction (`DatabaseManager._purge_media_rows`), files moved to the OS
trash by the service layer. Files still referenced by another panel's
`panel_images` or a subject's `reference_path` are spared (unhidden
instead) — the media FK's `ON DELETE CASCADE`/`SET NULL` would otherwise
silently destroy those references. Deleting a storyboard also deletes its
"Storyboard: <name>" folder (`folder_items` cascade) and broadcasts
`folder_deleted`.

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
  router/       # index.ts — hash-history vue-router: `/` (LibraryView), `/storyboard/:id?` (StoryboardView)
  views/        # LibraryView (desktop/mobile grid shell), StoryboardView (desktop-only authoring canvas)
  stores/       # Pinia stores (media, filters, folders, settings, scan,
                #   similarity, upscale, models, storyboard)
  composables/  # useWebSocket (multiplexed), useKeyboard, useFoldersUi, useToast
  components/
    layout/     # ContentSearchBar (header action row), ViewMenubar, ThreePanel, ScopeBreadcrumb, ToastHost
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

## Qwen3-VL VLM tagger

`VlmClient` (`metascan/core/vlm_client.py`) is an asyncio supervisor that
manages a `llama-server` subprocess running an Abliterated Qwen3-VL GGUF.
It mirrors `InferenceClient`'s state machine (idle → spawning → loading
→ ready → error/stopped) but talks HTTP to llama-server instead of NDJSON
to a Python worker. Tag generation goes through `generate_tags(image_path)`
which POSTs to `/v1/chat/completions` with a JSON-array grammar.

The `models` WebSocket channel carries two new event types:
- `vlm_status`: full snapshot when the supervisor's state changes.
- `vlm_progress`: progress payloads from background retag jobs
  (`{job_id, current, total}`).

The full design rationale (engine choice, hardware tier mapping, tag-merge
matrix) is in `docs/superpowers/specs/2026-05-02-qwen3vl-tagging-design.md`.
