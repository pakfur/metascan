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
- **`storyboards`** — script + render settings: `name`, `source_text`, `aspect_ratio`, `style_block`, `negative`, `target_model`, `architecture`, `preset_id` (→ `workflow_presets`), `base_seed`, `batch_size`, `folder_id` (→ `folders`, `ON DELETE SET NULL`), `notes` (free-form, UI-only).
- **`storyboard_subjects`** — recurring characters/props: `storyboard_id` (`ON DELETE CASCADE`), `name`, `description`, `lora_name`, `lora_strength`, `reference_path`/`reference_path_2` (→ `media.file_path`, `ON DELETE SET NULL`), `sort_order`, `voice`, `voice_ref_path`.
- **`scenes`** — `storyboard_id` (`ON DELETE CASCADE`), `sort_order`, `name`, `subtitle`, `setting`, `location`, `time_of_day`, `mood`, `lighting`, `notes`, `reference_path`. `subtitle` is display-only; `setting` is woven into every beat brief (`SETTING:` line in `compose_brief`).
- **`panels`** — one shot: `scene_id` (`ON DELETE CASCADE`), `sort_order`, `action`, `duration_s`, `video_prompt`, `video_prompt_locked`, `video_prompt_source`, `video_prompt_warnings`, `video_anchor`, `video_compiled_anchor`. Since the 2026-08-17 shot/beat reorg, a panel is a thin H3-scene-like container — the compiled per-shot video prompt lives here, but every per-shot *creative* field moved down to `beats`.
- **`beats`** — one shot-internal timeline unit (one H3 `[Shot n]` section): `panel_id` (`ON DELETE CASCADE`), `sort_order`, `duration_s`, `action`, `shot_size`, `angle`, `lens`, `subject_ids` (JSON array), `camera_motion`, `camera_amplitude`, `camera_speed`, `is_cut`, `dialog` (JSON array), `sound`, `brief`, `prompt`, `prompt_locked`, `prompt_source`, `selected_image_id` (→ `beat_images.id`, `ON DELETE SET NULL`). This is where a shot's framing, cast, and still-image prompt/keeper now live — a metascan Shot (panel) maps to one H3 generation unit, a Beat maps to one H3 `[Shot n]` section.
- **`beat_images`** — one rendered variant: `beat_id` (`ON DELETE CASCADE`), `file_path` (→ `media.file_path`, `ON DELETE CASCADE`), `seed`, `variant_index`, `prompt_used`, `preset_id`, `comfy_prompt_id`. Renamed and re-parented from `panel_images` in the shot/beat reorg.

`media.hidden` (`INTEGER NOT NULL DEFAULT 0`) keeps storyboard-generated
variants out of the main grid until curated: ingest inserts every rendered
file hidden, selecting a beat's keeper unhides it and re-hides whatever
was selected before. `GET /api/media` filters `hidden = 0` by default;
`include_hidden=true` opts back in. `hidden` is part of both grid covering
indexes (see below) so the filter doesn't force a main-table scan.

Deleting a beat/panel/scene/storyboard releases its generated images into the
library by default (media rows unhidden), or — with `purge_images=true` on
the DELETE route — removes them entirely: media rows deleted in the same
transaction (`DatabaseManager._purge_media_rows`), files moved to the OS
trash by the service layer. Files still referenced by another beat's
`beat_images`, a subject's `reference_path`, or a scene's `reference_path`
are spared (unhidden
instead) — the media FK's `ON DELETE CASCADE`/`SET NULL` would otherwise
silently destroy those references. Deleting a storyboard also deletes its
"Storyboard: <name>" folder (`folder_items` cascade) and broadcasts
`folder_deleted`.

No data was migrated when this schema shipped — dev-stage storyboard data
is disposable by decision. A `PRAGMA user_version = 3` gate drops and
recreates `panels`/`beats`/`panel_images→beat_images` once, unhiding any
media the old `panel_images` table left hidden and discarding
`generation_jobs` rows tied to the dropped panels first. Scenes,
storyboards, subjects, and folders are untouched by the migration.

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
                #   search, upscale, models, storyboard)
  composables/  # useWebSocket (multiplexed), useKeyboard, useFoldersUi, useToast
  components/
    layout/     # ContentSearchBar (header action row), ViewMenubar, ThreePanel, ScopeBreadcrumb, ToastHost
    filters/    # FilterPanel, SearchSection (content/tag search + threshold slider),
                #   FilterSection, FoldersSection, FolderRow, FolderKebabMenu
    thumbnails/ # ThumbnailGrid (virtual scroll), ThumbnailCard
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
- **Search is a filter layer, not a separate results view.** `useSearchStore` holds text search, Find Similar, and tag-AND path sets that the media store intersects with the folder/preset scope. The search endpoints return light unbounded `[{file_path, similarity_score}]` lists with the score threshold applied server-side, and a Relevance sort orders the grid by score while a search is active.

## Qwen3-VL / Qwen3.8 VLM tagger

`VlmClient` (`metascan/core/vlm_client.py`) is an asyncio supervisor that
manages a `llama-server` subprocess running an Abliterated VLM GGUF.
It mirrors `InferenceClient`'s state machine (idle → spawning → loading
→ ready → error/stopped) but talks HTTP to llama-server instead of NDJSON
to a Python worker. Tag generation goes through `generate_tags(image_path)`
which POSTs to `/v1/chat/completions` with a JSON-array grammar.

`metascan/core/vlm_models.REGISTRY` holds five entries across two model
families: four Qwen3-VL Abliterated sizes (`qwen3vl-2b/4b/8b/30b-a3b`)
and `qwen38-27b` (Qwen3.8 27B Abliterated, a dense hybrid Gated
DeltaNet model). Each `VlmModelSpec` carries `extra_args` (extra
llama-server argv appended verbatim — e.g. `qwen38-27b` disables
reasoning so its GBNF grammar constraints stay enforced) and `ctx_size`
(the total `--ctx-size` budget, split across `parallel_slots`) alongside
the download/VRAM metadata; `vlm_client._build_command` reads both
fields off the active spec rather than switching on model id.

The `models` WebSocket channel carries two new event types:
- `vlm_status`: full snapshot when the supervisor's state changes.
- `vlm_progress`: progress payloads from background retag jobs
  (`{job_id, current, total}`).

The full design rationale (engine choice, hardware tier mapping, tag-merge
matrix) is in `docs/superpowers/specs/2026-05-02-qwen3vl-tagging-design.md`.
