# API Reference

[← Back to README](../README.md)

The backend exposes a REST API at `http://localhost:8700`. Full interactive documentation is available at `/docs` (Swagger UI) or `/redoc`.

## Authentication

If `METASCAN_API_KEY` is set, every request must carry it as a bearer token:

```
Authorization: Bearer <METASCAN_API_KEY>
```

Without the env var the API is unauthenticated — fine for localhost, but set a key before exposing the server to a network.

## Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/media` | List media summaries (with sort/filter params). `include_hidden=true` also returns media hidden via storyboard beat-image curation (default: hidden rows are excluded) |
| GET | `/api/media/{path}` | Get single media record |
| DELETE | `/api/media/{path}` | Delete media (move to trash) |
| PATCH | `/api/media/{path}` | Update favorite/playback speed |
| GET | `/api/stream/{path}` | Stream file with HTTP Range support |
| GET | `/api/thumbnails/{path}` | Serve cached thumbnail |
| GET | `/api/filters` | Get filter groups with counts |
| POST | `/api/filters/apply` | Apply filters, return matching paths |
| POST | `/api/filters/tag_paths` | Resolve a list of tag keys to file path sets (used by smart folders) |
| GET | `/api/folders` | List static + smart folders |
| POST | `/api/folders` | Create folder |
| PATCH | `/api/folders/{id}` | Update folder name / icon / rules / sort order |
| DELETE | `/api/folders/{id}` | Delete folder |
| POST | `/api/folders/{id}/items` | Add items to a static folder |
| DELETE | `/api/folders/{id}/items` | Remove items from a static folder |
| POST | `/api/scan/prepare` | Count files for scan confirmation |
| POST | `/api/scan/start` | Begin scan (progress via WebSocket) |
| POST | `/api/similarity/search` | Find similar media (image → image); light unbounded results |
| POST | `/api/similarity/content-search` | CLIP text-to-image search; light unbounded results |
| POST | `/api/duplicates/find` | Find duplicate groups |
| POST | `/api/upscale` | Submit upscale tasks |
| GET | `/api/upscale/queue` | List queue tasks |
| GET | `/api/models/status` | Per-model availability rows + tier + gates |
| GET | `/api/models/hardware` | Full hardware probe report |
| GET | `/api/comfy/status` | ComfyUI driver connection snapshot |
| GET | `/api/comfy/loras` | Lora filenames installed on the ComfyUI server |
| GET | `/api/comfy/presets` | List registered workflow presets |
| POST | `/api/comfy/presets` | Register a workflow preset |
| DELETE | `/api/comfy/presets/{id}` | Delete a workflow preset |
| POST | `/api/comfy/submit` | Submit a generation job |
| GET | `/api/comfy/jobs` | List generation jobs |
| GET | `/api/comfy/jobs/{id}` | Get one generation job |
| POST | `/api/comfy/jobs/{id}/cancel` | Cancel a queued or running job |
| GET | `/api/storyboard/templates` | List shot-list templates (`data/templates/*.json`) |
| GET | `/api/storyboard` | List storyboards |
| POST | `/api/storyboard` | Create a storyboard |
| GET | `/api/storyboard/{id}` | Full storyboard tree (subjects, scenes, panels, beats, beat images) |
| PATCH | `/api/storyboard/{id}` | Update storyboard fields |
| DELETE | `/api/storyboard/{id}` | Delete a storyboard (its folder survives) |
| POST | `/api/storyboard/{id}/parse` | VLM-parse source text into subjects/scenes/panels |
| POST | `/api/storyboard/{id}/compose` | Staged VLM compose: outline → scenes → shots → beats (background, progress via WS) |
| POST | `/api/storyboard/{id}/synthesize` | Compose per-beat prompts (background, progress via WS) |
| POST | `/api/storyboard/{id}/generate` | Submit beats (still keyframes) to ComfyUI |
| POST | `/api/storyboard/{id}/cancel` | Cancel queued/running jobs for a storyboard |
| POST/PATCH/DELETE | `/api/storyboard/{id}/subjects`, `/api/storyboard/subjects/{id}` | Subject CRUD |
| POST/PATCH/DELETE | `/api/storyboard/{id}/scenes`, `/api/storyboard/scenes/{id}` | Scene CRUD |
| POST/PATCH/DELETE | `/api/storyboard/scenes/{id}/panels`, `/api/storyboard/panels/{id}` | Panel (shot) CRUD |
| POST/PATCH/DELETE | `/api/storyboard/panels/{id}/beats`, `/api/storyboard/beats/{id}` | Beat CRUD |
| POST | `/api/storyboard/beats/{id}/select` | Choose (or clear) a beat's keeper image |
| POST | `/api/i2v/prompt` | VLM-expand an idea into an I2VA video prompt (review-only, writes nothing) |
| POST | `/api/i2v/generate` | Submit an image-to-video job to ComfyUI |
| GET | `/api/i2v/videos` | List generated clips for a source image |
| DELETE | `/api/i2v/videos/{id}` | Delete a generated clip |
| GET | `/api/i2v/config` | Get the `i2v` config section (fast/quality presets, durations) |
| WS | `/ws` | Multiplexed WebSocket — channels: `scan`, `upscale`, `embedding`, `watcher`, `models`, `folders`, `comfy`, `storyboard`, `i2v` |

## Similarity Search Endpoints

Both search endpoints return a light, score-ordered list — no full media records:

```json
[ { "file_path": "/native/path/to/file.png", "similarity_score": 0.254 }, ... ]
```

- **`POST /api/similarity/content-search`** — body `{ "query": string, "threshold": float = 0.0, "max_results": int | null = null }`. Encodes the query with the active CLIP model and searches the FAISS index.
- **`POST /api/similarity/search`** — body `{ "file_path": string, "threshold": float = 0.7, "max_results": int | null = null }`. Encodes the image (or video keyframes) and searches the index.

The `threshold` is applied server-side (results with `similarity_score >= threshold`). When `max_results` is omitted or null, the result set is unbounded — every indexed file above the threshold is returned. Paths are native-format, matching `/api/media`.

## WebSocket Envelope

Every message on `/ws` carries a JSON envelope:

```json
{ "channel": "scan", "event": "progress", "data": { "completed": 42, "total": 100 } }
```

Subscribe per channel on the frontend with `useWebSocket('<channel>', handler)`.

## Error Shapes

Most errors are FastAPI's default `{ "detail": "..." }`. Two endpoints return a structured error the UI matches against:

- **Dim mismatch (HTTP 409)** from similarity endpoints when the loaded CLIP model's embedding dim differs from the on-disk FAISS index:
  ```json
  { "detail": { "code": "dim_mismatch", "index_dim": 768, "model_dim": 1024 } }
  ```
  The frontend renders an actionable "Rebuild index" prompt in the filter panel's SEARCH section.

## VLM tagging (`/api/vlm/*`)

### `GET /api/vlm/status`
Returns the current `VlmClient` snapshot:
`{state, model_id, base_url, progress, error}`. State is one of
`idle | spawning | loading | ready | error | stopped`.

### `POST /api/vlm/tag`
Body: `{path: string}`. Re-tags one image with the active VLM. If no model
is active, picks the recommended one for the host tier and starts it.
Returns `{tags: string[]}`. 404 if the file doesn't exist; 503 if no
recommended VLM model is available on this hardware.

### `POST /api/vlm/retag` (status: 202)
Body: `{scope: 'paths' | 'all_clip', paths?: string[], force?: boolean}`.
Enqueues a background re-tag job. Returns `{job_id, total}`. Progress is
broadcast on the `models` WS channel as `vlm_progress` events:
`{job_id, current, total}`.

### `DELETE /api/vlm/retag/{job_id}`
Cancels a running re-tag job. Returns `{status: 'cancelled'}`. Returns 404
if the job id is unknown.

### `POST /api/vlm/active`
Body: `{model_id: string}`. Switches the loaded VLM to a different model
in the `vlm_models.REGISTRY`. Cancels any in-flight retag jobs first.
Returns the new VlmClient snapshot. 400 if `model_id` isn't recognised.

## ComfyUI driver (`/api/comfy/*`)

The `ComfyClient` singleton is constructed in the FastAPI lifespan from the
`comfy` section of `config.json` (see `docs/configuration.md`) and connects
to an existing ComfyUI server — metascan never spawns one. A ComfyUI that
isn't reachable yet is not an error: `ComfyClient.start()` never blocks, and
the driver reconnects with backoff once ComfyUI comes up. Endpoints that
call into the client (`POST /submit`, `POST /jobs/{id}/cancel`,
`POST /presets`) return 503 only in the (non-standard) case where no
`ComfyClient` singleton was installed at all. The remaining endpoints —
`GET /status`, `GET /presets`, `DELETE /presets/{id}`, `GET /jobs`,
`GET /jobs/{id}` — read only from the database service layer and never
require the client, so they work even before the driver comes up.

### `GET /api/comfy/status`
Returns the driver's connection snapshot, not the full `comfy` config
section: `{base_url, client_id, in_flight}`, where `client_id` is a
per-process UUID generated by `ComfyClient` (not a config key) that
identifies this metascan instance to ComfyUI's websocket. `output_root`,
`unload_vlm_during_generation`, and `request_timeout_s` are not included.
When no client is installed, returns `{base_url: null, client_id: null,
in_flight: 0}` with a 200 — this endpoint never errors.

### `GET /api/comfy/loras`
Returns the lora filenames installed on the connected ComfyUI server
(`["style.safetensors", ...]`), read live from ComfyUI's
`/object_info/LoraLoader`. Best-effort: an unreachable server or missing
client returns `[]` with a 200 — the frontend lora picker degrades to
free-text entry.

### `GET /api/comfy/presets`
Returns registered workflow presets, summary shape (omits the workflow
graph): `[{id, name, kind, bindings, created_at, updated_at}, ...]`.

### `POST /api/comfy/presets`
Body: `{name: string, kind: "t2i" | "ref", workflow: object}`, where
`workflow` is a ComfyUI API-format export with `MS_*` node titles. Returns
`{id: int}`. 400 with the missing/invalid title list if the workflow
doesn't satisfy the `MS_*` binding contract (`BindingError`).

### `DELETE /api/comfy/presets/{id}`
Returns `{status: "deleted"}`.
- **404** if the preset doesn't exist.
- **409** if generation jobs still reference it. Job history is never
  cascaded away, so a preset that has ever been used cannot be deleted
  while its jobs remain; the detail names how many are in the way, e.g.
  `Preset 3 still has 12 generation job(s) referencing it. Delete those
  jobs first; preset history is never removed automatically.`

### `POST /api/comfy/submit`
Body:
```json
{
  "preset_id": 1,
  "positive": "a cat",
  "seed": 42,
  "width": 1024,
  "height": 576,
  "batch_size": 1,
  "negative": null,
  "lora_name": null,
  "lora_strength": null,
  "ref_image": null,
  "priority": false
}
```
Returns `{job_id: int}` immediately — the job is enqueued and reaches
ComfyUI once an `in_flight` slot frees up (or right away if `priority` is
`true`, which jumps the queue). Validation happens synchronously before
the job is created, so a bad request never reaches the queue:
- **404** if `preset_id` doesn't name a registered preset.
- **400** if the preset doesn't support a supplied parameter (e.g.
  `negative` with no `MS_NEGATIVE` node) — the detail names the missing
  node title.
- **503** if the driver isn't installed (no `ComfyClient` singleton).

Once a validated job is actually dispatched to ComfyUI, a rejection there
(as opposed to a local validation failure) surfaces asynchronously: the
job's `state` flips to `failed` and `error` holds ComfyUI's own message —
poll `GET /api/comfy/jobs/{id}` or subscribe to the `comfy` WS channel's
`job_update` event to see it.

### `GET /api/comfy/jobs`
Query params: `state` (optional, filters to one state), `limit` (default
100). Returns generation job rows, newest-id-last.

### `GET /api/comfy/jobs/{id}`
Returns one generation job row: `{id, preset_id, panel_id, beat_id, state,
params, comfy_prompt_id, error, created_at, started_at, finished_at}`.
Still-image (keyframe) jobs carry both `panel_id` and `beat_id`; video jobs
carry `panel_id` only (`beat_id` is `null` — video generation stays per
shot). `state` is
one of `queued | running | done | failed | cancelled`. 404 if unknown.

### `POST /api/comfy/jobs/{id}/cancel`
Cancels a queued or running job — a queued job never reaches ComfyUI; a
running job is interrupted there. Returns `{status: "cancelled"}`. 404 if
the job doesn't exist. `ComfyClient.cancel()` is a no-op on a job that has
already reached a terminal state (`done` / `failed` / `cancelled`) — the
response is still `{status: "cancelled"}` in that case, but the job's own
`state` (`GET /api/comfy/jobs/{id}`) is left exactly as it was; the
response describes the cancel request being accepted, not a guarantee that
it changed anything.

### `comfy` WebSocket channel
Bridged from `ComfyClient.on_job_event` via
`ws_manager.broadcast_sync("comfy", event, payload)`:

- **`job_update`** — `{job_id, state, error}`, sent on every state
  transition (queued → running → done/failed/cancelled).
- **`job_progress`** — `{job_id, value, max}`, ComfyUI's step progress for
  the running job.
- **`job_outputs`** — `{job_id, files}`, sent once a job's images have been
  downloaded and ingested; `files` is a list of local paths.

## Storyboard domain (`/api/storyboard/*`)

The `StoryboardRunner` singleton is constructed in the FastAPI lifespan
alongside `ComfyClient` and installed via `set_storyboard_runner`. Plain
CRUD routes (storyboards, subjects, scenes, panels, beats, select) go
straight
through `StoryboardService` and never need the runner. Routes that drive
the pipeline (`parse`, `synthesize`, `generate`, `cancel`) return **503**
`"storyboard runner not initialized"` if the singleton is missing.

As of the 2026-08-17 shot/beat model reorganization, a metascan **panel**
is a shot (an H3-scene-like container: `action` summary, `duration_s`, the
compiled `video_*` fields) and a **beat** is the shot's internal timeline
unit, one per H3 `[Shot n]` section. All the per-shot creative fields —
framing (`shot_size`/`angle`/`lens`), `subject_ids`, the still-image
`prompt`/`prompt_locked`/`prompt_source`/`brief`, and the keeper
(`selected_image_id`) — live on beats, not panels. Generated keyframes
(`beat_images`, formerly `panel_images`) are owned by the beat that
generated them.

### `GET /api/storyboard`
Lists storyboards (summary rows, no nested subjects/scenes/panels).

### `POST /api/storyboard`
Body: `{name, target_model, architecture="t2i", aspect_ratio="16:9",
style_block?, negative?, preset_id?, base_seed?, batch_size=4, notes?}`.
Returns
`{id: int}`.
- `base_seed` omitted → a random `0..2**31-1` seed is assigned.
- `batch_size` is clamped to `1..16`.
- **400** if `bucket_dims(aspect_ratio, target_model)` rejects the aspect
  ratio (unsupported ratios: anything outside `1:1`, `4:3`, `16:9`,
  `2.39:1`, `9:16`) — validated at save time, not at generate time.
- `notes` is free-form and UI-only — no pipeline stage reads it.

### `GET /api/storyboard/templates`
Lists shot-list templates loaded from `data/templates/*.json`
(`shot_templates.list_templates`), each summarized as `{id, function,
description, roles: [{id, screen_side, note}], sections: [{panel_index,
duration_s, label, slot_count}], slot_count, duration_s}`. Registered
before `/{storyboard_id}` so the literal path wins the route match.
**500** if a template file on disk fails validation.

### `GET /api/storyboard/{id}`
Returns the full nested tree: `{...storyboard fields, outline_hash,
subjects: [...], scenes: [{...scene fields, template_problems: [...],
template_warnings: [...], outline_stale: bool, panels: [{...panel
fields, beats: [{...beat fields, images: [...]}]}]}]}`. Panel fields are
just `id`, `scene_id`, `sort_order`, `action`, `duration_s`, the
`video_*` fields, and timestamps — the framing/prompt/subject/keeper
fields live on each beat in its `beats` array instead, along with
`images` (the beat's `beat_images` rows, paths via `to_native_path`).
404 if unknown.

`outline_hash` and the per-scene `template_problems`/`template_warnings`/
`outline_stale` fields are computed live on every read by
`shot_templates.annotate_tree` — they are never stored:
- `template_problems` — errors from `validate_assignment` for the
  scene's `template_id` (e.g. the template needs more castable
  characters than the storyboard has, or `template_id` no longer names a
  template on disk). A scene with problems can't be built by the shots
  compose stage until the selection is fixed (see `POST
  .../compose` below).
- `template_warnings` — advisory-only mismatches (the template's
  `function` differs from the scene's `function`; the template's
  duration differs from the scene's rough share of the story's runtime).
  These never block anything.
- `outline_stale` — `true` when the scene has been composed
  (`composed_from` is set) but its `composed_from.outline_hash` no
  longer matches the storyboard's current `outline_hash` — i.e. the
  outline changed since this scene's shots were last built from it.

### `PATCH /api/storyboard/{id}`
Partial update of any storyboard column. Returns `{status: "updated"}`.
404 if unknown. If the effective `aspect_ratio` or `target_model` after
the patch changes, `bucket_dims` is re-validated the same way as create
(400 on mismatch).

All five storyboard/subject/scene/panel/beat PATCH routes use
`model_dump(exclude_unset=True)`, not `exclude_none=True`: a field that's
**absent** from the request body is left untouched, but a field sent as
explicit JSON `null` clears the column (e.g. `{"preset_id": null}`,
`{"shot_size": null}`, `{"notes": null}`). Sending explicit `null` for a
`NOT NULL` column (`name`, `aspect_ratio`, `target_model`, `architecture`,
`base_seed`, `batch_size` on storyboards; `name`/`description`/`sort_order`
on subjects; `name`/`sort_order` on scenes; `sort_order`/`action`/
`duration_s`/`video_prompt_locked` on panels; `sort_order`/`duration_s`/
`action`/`is_cut`/`dialog`/`subject_ids`/`prompt_locked` on beats) is
rejected with **400**, naming
the offending field(s), rather than surfacing as a raw 500 from a SQLite
NOT NULL constraint violation.

### `DELETE /api/storyboard/{id}?purge_images=false`
Returns `{status: "deleted"}`. 404 if unknown. The storyboard's linked
"Storyboard: <name>" folder (if any) is deleted with it (`folder_items`
cascade; a `folder_deleted` event goes out on the `folders` WS channel).
Generated images: by default their media rows are unhidden — released
into the main library. With `purge_images=true` the media rows are
deleted and the files moved to the OS trash instead, except files still
referenced by another beat's `beat_images`, a subject's
`reference_path`, or a scene's `reference_path`, which are unhidden
rather than deleted.

### `POST /api/storyboard/{id}/parse`
Body: `{text: string, confirm: boolean = false}`. VLM-parses free text
into subjects/scenes/panels and destructively replaces the storyboard's
existing structure. Returns the fresh tree (same shape as `GET
/api/storyboard/{id}`, including the `outline_hash` and per-scene
`template_problems`/`template_warnings`/`outline_stale` annotation).
- **409** `{code: "confirm_required"}` if the storyboard already has
  scenes and `confirm` wasn't set — re-parsing destroys panel/beat
  identity (locked prompts, beat images, job history keyed by
  `panel_id`/`beat_id`).
- **422** if the VLM's output doesn't validate against the parse schema.
- **503** if no VLM client is available to parse with.

### `POST /api/storyboard/{id}/compose` (status: 202)
Body: `{stages?: string[], scene_ids?: int[], panel_ids?: int[], confirm:
boolean = false}`. Runs the staged VLM story engine
(`outline` → `scenes` → `shots` → `beats`, default all four) in the
background. Returns immediately: `{status: "started"}`; progress and
completion stream on the `storyboard` WS channel (below) — there is no
other signal that a 202'd run has finished.
- **400** if `stages` names an unknown stage, if `outline` is requested
  with no premise (`source_text`) set, or — when `shots` is requested
  without `scenes` in the same call — if any target scene's selected
  `template_id` fails `validate_assignment` (e.g. a template that needs
  more castable characters than the storyboard has, or a `template_id`
  that no longer names a template on disk). This check runs before the
  confirm gate and **regardless of `confirm`** — a bad template
  selection can't be forced through. Duration and scene-function
  mismatches between a template and its scene are warnings only and
  never block the call (see `template_warnings` on the tree above).
  When `scenes` is also in `stages`, this check is skipped entirely: the
  scenes stage recreates every scene row with `template_id` reset to
  `NULL`, so whatever was selected is about to be discarded anyway.
- **409** `{code: "confirm_required"}` if the target already has content
  a stage would destroy (an existing outline, existing scenes, or shots
  for a target scene that already has panels) and `confirm` wasn't set.
  Beats-only recompose asks for `confirm` when any target beat already
  has `beat_images` or a locked prompt.
- **400** if the storyboard doesn't exist — `check_compose_gates` raises a
  plain `StoryboardError` for an unknown id and the route maps every
  non-confirm `StoryboardError` to 400 (not 404).

### `POST /api/storyboard/{id}/synthesize` (status: 202)
Body: `{beat_ids?: int[], force: boolean = false}`. Composes (or
recomposes) per-beat still-image prompts in the background. Returns
immediately:
`{status: "started", total: n}`, where `n` is the number of beats in
scope (all beats, or the given `beat_ids` filtered to ones that exist)
— **not** reduced by however many are prompt-locked and get skipped.
Progress streams on the `storyboard` WS channel as `synthesis_progress`
events (now carrying `beat_id` alongside `panel_id`). 404 if the storyboard doesn't exist.

### `POST /api/storyboard/{id}/generate`
Body: `{beat_ids?: int[], only_failed: boolean = false}`. Submits beats
with a composed `prompt` to ComfyUI via the storyboard's `preset_id`.
Returns `{jobs: [job_id, ...]}` once every beat has been submitted (or
none, if none qualified). **400** if generation can't proceed — no
workflow preset, a negative prompt with no `MS_NEGATIVE` node, a subject
LoRA with no `MS_LORA` node, shot `image_loras` with no `MS_LORA_STACK`
node, or a `ref`-kind preset with no subject
reference image. Validation runs over every targeted beat before any
job is submitted, so a bad beat never leaves earlier ones half-queued.

### `POST /api/storyboard/{id}/generate-video`
Body: `{panel_ids?: int[], only_failed: boolean = false}`. Submits `ref2v`
(H3/MiniMax) video jobs for the storyboard's panels — video generation
stays per shot, unlike stills. Synchronous (not 202 fire-and-forget).
Returns `{jobs: [job_id, ...], skipped: [{panel_id, error}, ...]}`; a
panel can land in `skipped` for a runtime-only failure (frame
extraction/upload) even after upfront validation passes for everyone
else.

### `POST /api/storyboard/{id}/cancel`
Cancels every `queued`/`running` ComfyUI job for the storyboard's panels
and beats.
Returns `{cancelled: n}`. 404 if the storyboard doesn't exist.

### Subjects, scenes, panels, beats
- `POST /api/storyboard/{id}/subjects` · `PATCH
  /api/storyboard/subjects/{id}` · `DELETE
  /api/storyboard/subjects/{id}` — subject CRUD (name, description,
  `lora_name`, `lora_strength`, `reference_path`, `reference_path_2`,
  `sort_order`, `voice`, `voice_ref_path`).
  `reference_path`(`_2`) FKs `media(file_path)`; **400** if it doesn't name a
  row in the media library.
- `POST /api/storyboard/{id}/scenes` · `PATCH
  /api/storyboard/scenes/{id}` · `DELETE /api/storyboard/scenes/{id}` —
  scene CRUD (name, subtitle, setting, location, time_of_day, mood,
  lighting, notes, sort_order, reference_path, `template_id`, `brief`).
  `template_id` is the user's shot-list template selection (an id from
  `GET /api/storyboard/templates`, or `null` for free-form shots — the
  create default) that the shots compose stage builds the scene from;
  sending an id that doesn't name a template on disk is rejected with
  **400** `unknown template '<id>'` on both create and PATCH, before any
  write. `brief` is a free-form scene summary emitted by the scenes
  compose stage (or hand-edited) and fed into the shots/beats prompts;
  both fields are nullable and PATCH follows the usual
  `exclude_unset`/explicit-`null`-clears rule.
- `POST /api/storyboard/{id}/scenes/merge` — body `{scene_ids: [...]}`,
  two or more **adjacent** scenes; returns `{id}` of the surviving (first)
  scene. Deterministic, no VLM: descriptors come from the first scene,
  `charge_out` from the last, `brief`/`notes` are joined, `arc_beats` is
  the ordered union, `template_id` is cleared, `composed_from.stage` is
  `"merge"`, and the absorbed scenes' shots are re-parented in order (no
  beats/images/jobs touched). 400 on fewer than two ids, non-adjacent
  scenes, or ids outside the storyboard; 404 for an unknown storyboard.
- `POST /api/storyboard/scenes/{id}/panels` · `PATCH
  /api/storyboard/panels/{id}` · `DELETE /api/storyboard/panels/{id}` —
  panel (shot) CRUD: `action`, `duration_s`, `sort_order`, `image_loras`
  / `video_loras` (lists of `{name, strength}` injected into the still /
  video preset's `MS_LORA_STACK` node; never `null` — clear with `[]`),
  the `video_*`
  fields (`video_prompt`, `video_prompt_locked`, `video_anchor`). A
  `PATCH` whose body includes `video_prompt` also sets
  `video_prompt_locked=1, video_prompt_source="user"` server-side (and
  clears `video_prompt_warnings`), overriding whatever the caller sent
  for those fields — sending `video_prompt: null` clears the whole
  video-prompt block instead. Since the shot/beat reorg, panels carry
  **no** framing/subject/still-prompt/keeper fields — those all moved to
  beats.
- `POST /api/storyboard/panels/{id}/beats` · `PATCH
  /api/storyboard/beats/{id}` · `DELETE /api/storyboard/beats/{id}` —
  beat CRUD: `action`, `duration_s`, `sort_order`, `shot_size`, `angle`,
  `lens`, `subject_ids`, `camera_motion`, `camera_amplitude`,
  `camera_speed`, `is_cut`, `dialog`, `sound`, `brief`, `prompt`,
  `prompt_locked`, `prompt_source`. A `PATCH` whose body includes
  `prompt`
  also sets `prompt_locked=1, prompt_source="user"` server-side,
  overriding whatever the caller sent for those two fields. Create routes
  404 if the named parent (storyboard / scene / panel) doesn't exist. `selected_image_id`
  is not settable through the beat `PATCH` — use `POST
  /beats/{id}/select`, which keeps `media.hidden` in sync.
- The scene, panel, and beat `DELETE` routes accept the same
  `?purge_images=true` query flag as the storyboard delete: default
  unhides the affected beats' generated images into the library; with the
  flag
  their media rows are deleted and files moved to the OS trash (same
  shared-reference exceptions). Deleting a panel releases (or purges) all
  of its beats' images first.

Every create returns `{id: int}`; every PATCH on a storyboard/subject/scene
returns `{status: "updated"}`; panel `PATCH`, beat `PATCH`, and the select
route return
the full updated panel/beat row (they have a getter, subjects/scenes don't);
every DELETE returns `{status: "deleted"}`.

### `POST /api/storyboard/beats/{id}/select`
Body: `{image_id: int | null}`. Sets (or, with `null`, clears) a beat's
keeper: the new keeper's `media.hidden` flips to `0`, the previous
keeper's (if any) flips back to `1`. Returns the updated beat row.
**404** if the beat doesn't exist, or `image_id` doesn't belong to it.
(Replaces the pre-reorg `POST /api/storyboard/panels/{id}/select`.)

### `storyboard` WebSocket channel
Bridged from `StoryboardRunner.on_event`. The runner also emits
`folder_created` / `folder_items_changed` through the same callback, but
those go out on the **`folders`** channel, not `storyboard`:

- **`story_progress`** — `{storyboard_id, stage, done, total}`, emitted
  repeatedly within a stage as `POST .../compose` works through its
  scope.
- **`story_stage_complete`** — `{storyboard_id, stage, warnings, ...}`,
  sent once per finished stage. For `stage: "shots"` it also carries
  `template_scenes: [scene_id, ...]` — the ids of scenes that were built
  from a selected `template_id` rather than free-form.
- **`story_complete`** — `{storyboard_id, counts}`, sent once when a
  `POST .../compose` background run finishes every requested stage;
  `counts` maps stage name to the number of scenes/panels/beats it
  produced. Since `compose` returns 202 immediately, this (or
  `story_error`) is the only signal a client gets that the run is
  actually done.
- **`story_error`** — `{storyboard_id, stage, error}`, sent instead of
  `story_complete` if the background run raises, naming the stage it
  failed in.
- **`synthesis_progress`** — `{storyboard_id, panel_id, beat_id, done,
  total, prompt_source}`, one per beat as `POST .../synthesize` works
  through
  its scope.
- **`synthesis_complete`** — `{storyboard_id, synthesized, fallback,
  skipped_locked}`, sent once when a `POST .../synthesize` background run
  finishes successfully; counts are per-beat. Since `synthesize` returns
  202 immediately, this
  (or `synthesis_error`) is the only signal a client gets that the run is
  actually done.
- **`synthesis_error`** — `{storyboard_id, error}`, sent instead of
  `synthesis_complete` if the background run raises (e.g. the VLM fails to
  load).
- **`beat_images_changed`** — `{storyboard_id, panel_id, beat_id, files}`,
  sent
  once a ComfyUI job's outputs have been ingested as `beat_images` rows.
  Replaces the pre-reorg `panel_images_changed`.

## Image-to-video (`/api/i2v/*`)

The `I2vRunner` singleton is constructed in the FastAPI lifespan alongside
`ComfyClient`/`StoryboardRunner` and installed via `set_i2v_runner`. It
turns a single library image into a MiniMax H3 I2VA video clip: an idea
expands into a compiled prompt (review-only — nothing is written until the
user generates), then a `ref2v` ComfyUI preset drives the image in as the
first frame. Unlike storyboard clips, generated videos are never hidden —
they're ordinary visible library media, favorited via `media.is_favorite`.

### `POST /api/i2v/prompt`
Body: `{source_path: string, idea: string = "", duration_s: float}`.
VLM-expands the idea into an I2VA prompt scaled to the requested duration's
beat count; writes nothing. Returns `{prompt: string, warnings: string[]}`
(advisory lint warnings from `lint_i2v_prompt`).
- **400** if `duration_s <= 0`, or on `I2vRequestError` (e.g. the source
  path isn't a recognized image).
- **502** if the VLM call itself fails (`I2vError` / `VlmError` /
  timeout / runtime error).
- **503** if the i2v runner isn't installed, or no VLM model is available
  to expand with (`I2vUnavailableError` / `VlmSelectError`).

### `POST /api/i2v/generate`
Body: `{source_path: string, prompt: string, duration_s: float, quality:
"fast" | "quality", seed: int, width: int = 0, height: int = 0, loras:
[{name, strength}, ...] = [], idea?: string}`. Re-lints `prompt` (the
lint is advisory and never blocks) and submits a job to ComfyUI using the
config's `fast_preset_id` or `quality_preset_id` for the requested
`quality`. Returns `{job_id: int, warnings: string[]}`.
- **400** if `duration_s <= 0`, `quality` isn't `"fast"`/`"quality"`, no
  preset is configured for the requested quality slot (set one in
  Configuration → Image to Video), or on `I2vRequestError` /
  `BindingError` / `PresetNotFoundError`.
- **502** if ComfyUI rejects the submission (`ComfyError`).
- **503** if the i2v runner isn't installed.

### `GET /api/i2v/videos?source_path=`
Returns every clip generated from that source image, newest first:
`[{id, source_path, file_path, prompt_used, idea, seed, duration_s,
quality, preset_id, comfy_prompt_id, created_at, is_favorite}, ...]`,
`is_favorite` joined live from `media.is_favorite`. Rows whose backing
media has been deleted from the library are pruned lazily on read —
deleting the *source* image, by contrast, leaves its generated videos in
place (`i2v_videos` has no foreign key to media).

### `DELETE /api/i2v/videos/{video_id}`
Deletes one generated clip: its `i2v_videos` row plus its media row (moved
to the OS trash, subject to the usual "still referenced elsewhere" survival
checks). Returns `{status: "deleted"}`. **404** if the video id doesn't
exist.

### `GET /api/i2v/config`
Returns the `i2v` config section with defaults filled in:
```json
{
  "fast_preset_id": null,
  "quality_preset_id": null,
  "durations": [6.0, 10.0, 15.0, 20.0],
  "default_duration": 6.0,
  "default_quality": "fast"
}
```
`fast_preset_id`/`quality_preset_id` name a `workflow_presets.id` or
`null` if unset — the frontend's Configuration → Image to Video tab is
where they're picked.

### `i2v` WebSocket channel
- **`i2v_videos_changed`** — `{source_path, files}`, sent once a ComfyUI
  job's outputs have been downloaded and ingested as `i2v_videos` rows.
