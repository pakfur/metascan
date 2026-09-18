# Image-to-Video (i2v) for MiniMax H3 — Design

**Date:** 2026-09-18
**Status:** Approved

## 1. Overview

A standalone image-to-video flow: the user right-clicks a library image,
opens an "Image to Video…" dialog, types a short idea, expands it into a
MiniMax H3 **I2VA** prompt via the local VLM, tunes workflow parameters
(duration, quality, seed, loras), and generates a video through ComfyUI.
Generated videos ingest as normal, visible library media; the
image↔video relationship is persisted so reopening the dialog on the same
image shows every video it produced, with view/star/delete in place.

This is a new feature with its own UI — it reuses storyboard *machinery*
(VLM framework, ComfyUI driver, workflow validation, WS multiplexing) but
none of the storyboard UI or its subjects/scenes/panels/beats domain.
`StoryboardRunner`, the storyboard routes, and the storyboard frontend are
untouched.

### Decisions locked during brainstorming

- **Prompt dialect: I2VA, single image.** The selected image is the first
  frame; the prompt follows the base guide's I2VA structure exactly
  (single-picture alignment line + first-frame-anchor narrative). No
  last-frame picker (fl2va two-picture structure is out of scope).
- **Generated videos are visible in the main library grid** immediately —
  no `hidden` flag (unlike storyboard clips).
- **Duration is a fixed-choice control**, default choices 6/10/15/20 s
  (configurable list). Duration drives the number of narrative beats in
  the generated prompt.
- **The prompt is editable and manual entry is allowed.** Generate uses
  whatever text is in the prompt box; the VLM expansion step is optional.
- **Prompt composition is "beats + deterministic assembly"** (approach A):
  a GBNF-constrained VLM call returns beats + sound fields; pure Python
  renders the final document. The VLM never produces document structure.

## 2. Prompt expansion pipeline (idea → I2VA document)

Reuses the storyboard's LLM framework piece-for-piece. The format
authority is `data/prompt_guides/minimax-h3/VIDEO_PROMPT_WRITING_GUIDE_base_en.md`
(the base guide): I2VA final prompt = §2.1 alignment line +
`integrated_multimodal_description` + `overall_soundscape` +
`non_diegetic_music`.

**Stage 0 — model lifecycle (reused as-is).** `POST /api/i2v/prompt` uses
the describe-endpoint wiring: `get_vlm_client()` → `pick_vlm_model()` →
`ensure_started()` → `VlmClient.generate_text(...)`, with the same 502
(`VlmError`/`TimeoutError`/`RuntimeError`) / 503 (no VLM) mapping.

**Stage 1 — prompts via `PromptStore`.** System prompt `I2V_BEATS_SYSTEM`
lives in `data/meta_prompt.yml` and hot-reloads through the existing
`PromptStore`. The user prompt is built deterministically in code: idea
text, duration, the exact beat count, and the I2VA arc instruction
(beat 1 emerges from the depicted first-frame state; the final beat lands
the "result/reaction").

**Stage 2 — grammar-constrained generation.** One `generate_text` call
with `image_path=<source image>` and `grammar=i2v_grammar(duration_s)`.
Structural guarantees baked into the GBNF:

- **Beat count is baked into the repetition rule** (the `fill_grammar`
  pattern) — a wrong beat count is structurally impossible. The
  duration → beat-count table lives in code
  (`beat_count(duration_s)`: 6 s → 3, 10 s → 4, 15 s → 5, 20 s → 6;
  non-listed durations round to the nearest entry), never decided by the
  model.
- **`camera` is an enum alternation** over the base guide §4
  camera-motion vocabulary (shared with `h3_compiler`'s
  `CAMERA_MOTION_VALUES` where they coincide) — off-vocabulary camera
  language cannot appear. Free prose is confined to `action` and the two
  sound fields, all length-bounded.
- GBNF hyphens stay literal (the `\-` escape SIGSEGVs `llama-server`).

Output shape: `{beats: [{action, camera}, …], overall_soundscape,
non_diegetic_music}`. Sound rides in the same call — one VLM round-trip,
not h3's separate `SOUND_GRAMMAR` call. `max_tokens` scales with beat
count (the `stage_max_tokens` pattern) so long durations are never
tail-truncated.

**Stage 3 — deterministic validation.** `validate_i2v_beats(raw)` in the
pure compiler module: strict JSON parse, whitespace normalization,
non-empty `action` enforcement, defense-in-depth vocab re-check that
drops to a safe default rather than raising (grammar is the enforcement;
the validator is the seatbelt).

**Stage 4 — deterministic assembly.** `assemble_i2v_prompt(result,
duration_s)` owns 100 % of document structure: the §2.1 single-picture
alignment line rendered verbatim from a code template; a fixed
first-frame-anchor opening sentence; beats flowed in order with camera
phrases rendered from the vocabulary table; then the two sound sections.
VLM text is slotted into structure it never produced.

**Stage 5 — expectation-driven lint.** `lint_i2v_prompt(text,
expectations)`, modeled on `build_expectations` + `lint_h3_prompt` but
targeting the base guide's I2VA form:

- alignment line present and exact;
- the only reference label is `<Picture 1>` (any other `<…>` label is a
  violation);
- camera vocabulary check;
- word-count band scaled to duration (shortfall is a warning);
- no `[Shot n]` headers or ref-guide six-section markers (wrong dialect);
- sound sections present and non-empty.

Warnings only — lint never hard-fails. **Lint runs twice:** once on the
generated document (returned as `{prompt, warnings}`), and again on
whatever text the user actually submits to `POST /api/i2v/generate`
(returned alongside `{job_id}`) — hand-written or hand-edited prompts get
the same structural check without being blocked.

## 3. Data model

- **New table `i2v_videos`:**
  `id INTEGER PRIMARY KEY AUTOINCREMENT`,
  `source_path TEXT NOT NULL` (POSIX, the library image),
  `file_path TEXT NOT NULL UNIQUE` (POSIX, the generated clip),
  `prompt_used TEXT`, `idea TEXT`, `seed INTEGER`, `duration_s REAL`,
  `quality TEXT` (`fast` | `quality`), `preset_id INTEGER`,
  `comfy_prompt_id TEXT`, `created_at TEXT NOT NULL DEFAULT (datetime('now'))`.
  Index on `source_path`. No `REFERENCES media(file_path)` — list queries
  JOIN `media` and lazily prune rows whose media row is gone, so deleting
  a video from the main library grid cannot strand a relation row.
  Deleting the *source image* keeps the videos (independent library
  media; the dialog can still be opened from any surviving video's
  source path, but no UI for that is in scope).
- **`generation_jobs.i2v_source_path TEXT`** added via
  `_idempotent_add_column`, mirroring the `panel_id`/`beat_id` no-FK
  precedent. `ComfyClient.submit()` gains an optional `i2v_source_path`
  kwarg threaded to `create_generation_job` the same way `beat_id` was.
- Generated clips ingest as **normal visible media** — `set_media_hidden`
  is never called, so star (`is_favorite`), grid display, and
  `PATCH /api/media` work unchanged. Both grid covering indexes are
  unaffected (no new summary columns).

## 4. Core modules

### `metascan/core/i2v_compiler.py` (pure, no I/O)

- `beat_count(duration_s) -> int` — the fixed table above.
- `i2v_grammar(duration_s) -> str` — GBNF with beat count baked in,
  camera enum alternation, bounded strings.
- `i2v_max_tokens(duration_s) -> int`.
- `build_i2v_user_prompt(idea, duration_s) -> str`.
- `validate_i2v_beats(raw) -> I2vResult` (dataclass: beats + the two
  sound fields).
- `assemble_i2v_prompt(result: I2vResult, duration_s) -> str`.
- `lint_i2v_prompt(text, duration_s) -> list[str]` (expectations derived
  from `duration_s` internally; warnings only).

### `metascan/core/i2v_runner.py`

Owns the non-pure work; ComfyUI-driver-agnostic layering like
`StoryboardRunner` (which is untouched). Constructed in the lifespan with
`db`, `comfy_client`, `vlm_client accessor`, `scanner`, config accessor,
and an `on_event` callback list wired to `ws_manager.broadcast_sync`.

- `generate_prompt(source_path, idea, duration_s) -> (prompt, warnings)`
  — stages 0–5 above; **review-only**, writes nothing.
- `generate(source_path, prompt, duration_s, quality, seed, loras) ->
  job_id` — resolves the preset id from the config's `fast`/`quality`
  slot; validates synchronously before submit: slot configured, preset
  exists and is kind `ref2v`, tag matches (`minimax` + `i2va`; untagged
  legacy presets pass, per the storyboard precedent), loras supplied only
  when the preset binds `MS_LORA_STACK`, source image exists. Uploads the
  image via `comfy.upload_file` as `first_frame`; builds
  `GenerationParams(positive=prompt, seed, first_frame,
  duration_s if bindings.duration else None, loras)`;
  `submit(..., i2v_source_path=…, output_dir=<comfy.output_root>/i2v/<image-stem>/)`.
  Seed is whatever the caller sends (the dialog defaults to random with a
  🎲 rerandomize button; no deterministic seed scheme — rerolls are
  explicit user actions here).
- `handle_job_event(event, payload)` — registered in the lifespan via
  `comfy_client.on_job_event` (multi-listener fan-out already exists).
  On `job_outputs` where the job row has a non-NULL `i2v_source_path`:
  ingest each recognized output via `Scanner.ingest_file` in
  `asyncio.to_thread`, insert `i2v_videos` rows (paths stored POSIX,
  returned native — the `beat_images` convention), and broadcast
  `i2v_videos_changed {source_path, files}` on the new **`i2v` WS
  channel**. The storyboard ingest branch keys on `beat_id`/`panel_id`
  and ignores i2v jobs; the i2v branch keys on `i2v_source_path` and
  ignores storyboard jobs.

Lifespan shutdown: `comfy_client.shutdown()` still runs before any
consumer teardown; the i2v runner has no VLM-unload interaction
(`unload_vlm_during_generation` handling stays inside the submit path it
already lives in) and no lock shared with `StoryboardRunner`.

## 5. Workflow validation

New `_validate_minimax_i2va` registered under `("minimax", "i2va")` in
`metascan/core/workflow_validation.py`:

- **Error:** missing `MS_FIRST_FRAME`.
- **Warnings:** missing `MS_DURATION` (duration control won't apply),
  missing `MS_LORA_STACK` (lora control won't apply), present-but-unused
  `MS_LAST_FRAME` / `MS_REF_IMAGE*` / `MS_AUDIO*` slots.

Presets register as kind `ref2v`, tagged `video_target="minimax"`,
`video_mode="i2va"`. The user's two workflows (turbo-lora "fast" and
"quality") both register this way; an fl2va-capable graph still validates
— its extra slots draw "unused by i2v" warnings only.

## 6. API — `backend/api/i2v.py` + `I2vService`

Routes (router registered in `backend/main.py`; sync DB methods in
`database_sqlite.py`, async wrappers in `backend/services/i2v_service.py`
via `asyncio.to_thread`):

- `POST /api/i2v/prompt` `{source_path, idea, duration_s}` →
  `{prompt, warnings}`. 503 no VLM, 502 VLM failure, 400 bad input
  (missing image, non-image path — reuse `VlmClient.is_image_path`).
- `POST /api/i2v/generate` `{source_path, prompt, duration_s, quality,
  seed, loras}` → `{job_id, warnings}` (warnings = lint of the submitted
  prompt). 400 with a named reason for every synchronous validation
  failure.
- `GET /api/i2v/videos?source_path=…` → rows joined with `media`
  (`is_favorite`, dims, duration) for the strip, newest first; lazily
  prunes rows whose media is gone.
- `DELETE /api/i2v/videos/{id}` → deletes the relation row + the media
  row (indices/`folder_items` cascade) and trashes the file via
  `metascan/utils/trash.py::remove_files_to_trash`. Returns
  `{status: "deleted"}` (project rule: never 204).
- Star = existing `PATCH /api/media` `{is_favorite}` — no new endpoint.

### Config

New top-level `i2v` section in `config.json`:

```jsonc
{
  "i2v": {
    "fast_preset_id": null,      // workflow_presets.id, tagged minimax/i2va
    "quality_preset_id": null,
    "durations": [6, 10, 15, 20],
    "default_duration": 6,
    "default_quality": "fast"
  }
}
```

`get_i2v_config(config)` defaulting helper in `backend/config.py`
(the `get_comfy_config` pattern). Read/written through the existing
`GET/PUT /api/config` shallow top-level merge — the frontend keeps the
raw fetched section and spread-merges before PUT.

## 7. WebSocket

New `i2v` channel on the multiplexed `/ws`:

- `i2v_videos_changed {source_path, files}` — emitted after ingest.

Job progress reuses the existing `comfy` channel (`job_update`,
`job_progress`) — the dialog filters to its own job ids.

## 8. Frontend

- **Context menu:** "Image to Video…" item in `ThumbnailGrid.vue`
  (rendered for images only, hidden for videos), new emit →
  `LibraryView.vue` opens the dialog with the clicked `Media`.
  Desktop only (mobile never mounts management dialogs).
- **`I2VDialog.vue`** (`components/dialogs/`):
  - Top-left: source image preview. Beside it: idea textarea +
    "Generate prompt" button (spinner while the VLM runs; idea and
    prompt preserved on failure, error via toast).
  - Below: editable prompt textarea (manual entry allowed; Generate uses
    its current contents). Lint warnings render under the textarea.
  - Params row: duration select (choices from config), quality toggle
    (Fast / High quality), seed number input + 🎲 randomize (default
    random per open), `LoraListEditor` (reused component; degrades to
    free text when ComfyUI is unreachable, as today).
  - "Generate" button → `POST /api/i2v/generate`; multiple in-flight
    jobs allowed.
  - Bottom: horizontal scrolling strip cloned from `ShotHeader.vue`'s
    Takes pattern (`.sh-takes-row` / `.sh-tile` CSS): tile = thumbnail +
    hover play overlay + ★ favorite toggle (real `is_favorite`) + ×
    delete (confirm dialog), plus an in-flight placeholder tile per
    queued/running job (progress from the `comfy` channel; flips to an
    error tile showing `generation_jobs.error` verbatim on failure).
  - Viewer: `MediaViewer` with `allow-destructive=false` (star/delete
    live on the tiles — requirement "view/star/delete without going back
    to the library" is satisfied in-dialog; the read-only viewer avoids
    the library-store coupling).
- **`stores/i2v.ts`** (Pinia): dialog state, per-source video list, job
  tracking (rebuilt from `GET /api/comfy/jobs` on open — the
  `refreshActiveJobs` reload-survival pattern — filtered by
  `i2v_source_path` exposure in the job list, plus live `comfy` channel
  updates), WS subscription to `i2v` (`i2v_videos_changed` for the
  current source refreshes the strip and nudges `mediaStore` so the clip
  appears in the grid). Events for other sources are dropped (the
  storyboard channel-guard precedent).
- **Config tab:** `ConfigDialog.vue` `TabKey` gains `'i2v'`; new
  `ConfigI2VTab.vue`:
  - Fast / Quality preset dropdowns listing registered `ref2v` presets
    tagged minimax/i2va (from `GET /api/comfy/presets`).
  - "Register workflow…" flow reusing `PresetRegistrationDialog.vue`
    (moved out of `components/storyboard/` or imported from there; gains
    optional props for default `video_mode`), including the existing
    Validate / structured-findings / Apply-fixes loop, which picks up
    the new `("minimax","i2va")` validator automatically.
  - Duration choices list, default duration, default quality.
  - Save follows the spread-merge PUT pattern.

## 9. Error handling summary

- Prompt expansion: 503 (no VLM) / 502 (VLM error) → toast; user input
  preserved.
- Generate: all static failures are synchronous 400s with named reasons
  (unconfigured slot, missing/mis-tagged preset, loras without
  `MS_LORA_STACK`, missing source file, bad duration/quality value).
- Job failure: strip placeholder shows the ComfyUI error verbatim
  (existing `generation_jobs.error` flow).
- Lint is always advisory — it annotates, never blocks.

## 10. Testing

- `tests/test_i2v_compiler.py` — beat-count table; grammar text
  assertions (beat count baked into repetition, camera alternation
  matches the vocab table, literal hyphens only); golden-output tests
  asserting `assemble_i2v_prompt` matches the base guide's I2VA case
  structure line-for-line; lint tests seeded with each violation class
  (wrong label, missing alignment line, ref-guide section headers,
  off-vocab camera, shot headers); validator drop-to-default behavior.
- `tests/test_i2v_db.py` — `i2v_videos` CRUD, JOIN/prune on missing
  media, source-image delete keeps videos, `generation_jobs.i2v_source_path`
  round-trip.
- `tests/test_i2v_api.py` — `TestClient` with fake VLM + fake Comfy:
  prompt endpoint happy path + 502/503/400s, generate validation paths,
  lint warnings in both responses, videos list, delete (trash
  monkeypatched via `metascan.utils.trash.send2trash`).
- Workflow-validation tests extended for `("minimax","i2va")`.
- Frontend: `vue-tsc --noEmit` + `npm run build` green; `make quality
  test` green.

## 11. Out of scope (deliberate)

- Opening the dialog from `MediaViewer` or from a generated video.
- Mobile support.
- Last-frame / two-picture FL2VA structure, audio references.
- Batch generation across a multi-selection.
- Persisting per-image parameter defaults (seed/loras/idea) beyond the
  dialog session.
