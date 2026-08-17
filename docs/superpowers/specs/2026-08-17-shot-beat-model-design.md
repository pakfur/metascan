# Shot/Beat Data Model Reorganization — Design

**Date:** 2026-08-17
**Status:** Approved design, pre-implementation

## 1. Motivation

The storyboard data model predates the beats/video pipeline: it was
designed around generating one keyframe per shot (panel), so the
creative fields — shot size, angle, lens, subjects, the still-image
prompt with its lock/source machinery — all live on `panels`. Since
then the H3 video compiler established that **a metascan Shot maps to
an H3 generation unit (one compiled document, one clip) and a metascan
Beat maps to an H3 `[Shot n]` section** — and H3 varies framing,
camera, and who is on screen *per beat*. The data model lags that
reality:

- Framing (`shot_size`/`angle`/`lens`) is stored per shot and consumed
  only by the still-image brief (`storyboard_brief.py`); it never
  reaches the compiled video document at all.
- `subject_ids` is per shot, so a beat cannot say who is actually in
  frame.
- Keyframes are per shot, but with beats as the shot-internal timeline
  the useful still is *per beat*.
- `panels.notes` is consumed by nothing in the pipeline;
  `panels.negative` is only a per-shot override of the existing
  `storyboards.negative`; `panels.prompt` duplicates what the per-beat
  keyframe prompt becomes.

This design moves the per-H3-shot creative fields down to beats, thins
the shot into an H3-scene-like container, and hoists notes/negative to
the storyboard level.

## 2. Decisions (settled with the user)

1. **Keyframes move to beats.** Each beat owns its synthesized prompt,
   generated images, and keeper. The shot no longer has a prompt or a
   selected image.
2. **Video stays per shot.** One compiled H3 document per shot; beats
   render as its `[Shot n]` sections; one clip per shot.
   `video_prompt` (+ `video_prompt_locked` / `video_prompt_source` /
   `video_prompt_warnings`) and `video_anchor` /
   `video_compiled_anchor` remain panel columns. (`video_target` /
   `video_mode` already live on `storyboards` and are unchanged.)
3. **`subject_ids` lives only on beats.** The shot-level subject set is
   derived: `union(beat.subject_ids) ∪ beat-dialog speakers`, computed
   at compile/upload time. The shots composition stage stops emitting
   subject assignments.
4. **No data migration.** Dev-stage data is disposable: the storyboard
   tree tables are dropped and recreated via a `PRAGMA user_version`
   gate. No column copying.

## 3. Schema changes

### 3.1 `storyboards`

- **Add** `notes TEXT` (free-form, UI-only — not consumed by any
  pipeline stage; replaces the dropped `panels.notes`).
- `negative` (already present) becomes the *only* negative prompt;
  the per-shot override is gone. `generate()`'s
  `panel.negative or tree.negative` fallback collapses to
  `tree.negative`.

### 3.2 `panels` (shots)

Keep: `id`, `scene_id`, `sort_order`, `action` (shot summary written
by the shots stage), `duration_s`, `video_prompt`,
`video_prompt_locked`, `video_prompt_source`, `video_prompt_warnings`,
`video_anchor`, `video_compiled_anchor`, `created_at`, `updated_at`.

**Drop:** `shot_size`, `angle`, `lens`, `notes`, `negative`, `brief`,
`prompt`, `prompt_locked`, `prompt_source`, `subject_ids`,
`selected_image_id`.

### 3.3 `beats`

Existing columns unchanged: `id`, `panel_id` (CASCADE), `sort_order`,
`duration_s`, `action`, `camera_motion`, `camera_amplitude`,
`camera_speed`, `is_cut`, `dialog` (JSON array), `sound`,
`created_at`, `updated_at`.

**Add:**

| Column | Type | Notes |
|---|---|---|
| `shot_size` | TEXT | same enum vocabulary as the old panel column |
| `angle` | TEXT | 〃 |
| `lens` | TEXT | 〃 |
| `subject_ids` | TEXT NOT NULL DEFAULT '[]' | JSON array of subject ids |
| `brief` | TEXT | deterministic brief snapshot (moved from panels) |
| `prompt` | TEXT | still-image prompt |
| `prompt_locked` | INTEGER NOT NULL DEFAULT 0 | |
| `prompt_source` | TEXT | 'brief' / 'llm' / 'user' |
| `selected_image_id` | INTEGER REFERENCES beat_images(id) ON DELETE SET NULL | keeper |

### 3.4 `panel_images` → `beat_images`

Renamed table, re-parented FK: `beat_id INTEGER NOT NULL REFERENCES
beats(id) ON DELETE CASCADE`. All other columns unchanged
(`file_path` FK to `media(file_path)` ON DELETE CASCADE, `seed`,
`variant_index`, `prompt_used`, `preset_id`, `comfy_prompt_id`,
`created_at`).

### 3.5 `generation_jobs`

- **Add** `beat_id INTEGER` — like `panel_id`, deliberately **no
  `REFERENCES` clause** (same rationale documented in CLAUDE.md; the
  release helpers delete job rows explicitly instead of relying on FK
  actions).
- Still-image jobs set both `beat_id` and `panel_id` (panel retained
  for output-dir grouping and coarse queries); video jobs set
  `panel_id` only, `beat_id` NULL.
- `output_dir` for still jobs becomes
  `<comfy.output_root>/<storyboard-slug>/scene_NN/panel_NN/beat_NN/`.

### 3.6 Migration: drop and recreate

One-shot, gated on `PRAGMA user_version` (current max is 2; this
migration is `user_version = 3`):
inside `_init_database`, if the gate hasn't run, `DROP TABLE IF
EXISTS` — in FK-safe order — `beats`, `panel_images`, `panels`, then
delete `generation_jobs` rows where `panel_id IS NOT NULL` (their
panels are gone; a restart must not re-adopt them), and `UPDATE media
SET hidden = 0` for any rows left hidden by the dropped
`panel_images` (query the old table's paths *before* dropping it).
Normal `CREATE TABLE IF NOT EXISTS` then rebuilds the new shapes.
Scenes, storyboards, subjects, and folders survive; users re-run the
shots/beats stages. Bump `user_version` at the end of the gate.

## 4. Beats gain identity — lifecycle consequences

Beats previously were disposable VLM output (`replace_panel_beats`
wholesale-replaced them; the beats compose stage was never gated).
Once beats own locked prompts and keeper images they have identity,
so the panel lifecycle rules move down a level:

- **`check_compose_gates`**: the `beats` stage becomes gated — 409
  `confirm_required` when any target panel's beats already have
  `beat_images` rows or a locked prompt. (Outline/scenes/shots gate
  logic unchanged; shots' existing "target scenes already have
  panels" gate now also protects the beats hanging off those panels,
  which the cascade makes literal.)
- **`_release_beats(conn, beat_ids, purge_images=False)`**: new
  shared helper mirroring `_release_panels` — unhide (or collect for
  purge) the beats' `beat_images` media rows and delete their
  `generation_jobs` rows (by `beat_id`), inside the same transaction
  as the destructive operation. Called from: `delete_beat`,
  `replace_panel_beats` (a beats recompose destroys the old beats the
  same way a delete does — keep/unhide by default), and transitively
  from `_release_panels` (which now first releases all beats of the
  doomed panels, then deletes the panels' own video `generation_jobs`
  rows).
- **`_purge_media_rows` shared-reference checks** now consult
  `beat_images` (instead of `panel_images`) plus
  `storyboard_subjects.reference_path` / `scenes.reference_path`,
  unchanged in spirit.
- **Frontend `DeleteImagesDialog`** (purge / keep / cancel) extends to
  beat deletion and to confirming a gated beats recompose.
- **Keeper selection** becomes `db.select_beat_image(beat_id,
  image_id)` with the same hide/unhide symmetry (unhide new keeper,
  re-hide previous).

## 5. Pipeline changes

### 5.1 Story stages (`storyboard_story.py`, runner `compose_story`)

- **Shots stage**: `SHOTS_GRAMMAR` and `validate_shots_response` drop
  the per-shot `subjects` field (nothing stores it anymore). The shot
  output is: action summary + duration. The shots user prompt still
  lists the roster so the action text uses exact names.
- **Beats stage**: `BEATS_GRAMMAR` gains per-beat `subjects` (array of
  strings, validated against the roster by name → mapped to ids;
  unknown names dropped) and `shot_size` / `angle` / `lens` (nullable
  enums, same drop-to-NULL policy as the camera fields).
  `build_beats_user_prompt` now receives the **full storyboard
  roster** (panel `subject_ids` no longer exists to filter by) and the
  shot's action + duration as before. `validate_beats_response` maps
  and validates the new fields.
- **Gating**: see §4.

### 5.2 Synthesize (still prompts) — per beat

- `compose_brief` / `build_render_messages` inputs become: scene
  context + shot `action`/`duration_s` + beat `action`, framing
  (`shot_size`/`angle`/`lens` — from the beat), and the beat's
  subjects. Style block still appended verbatim by `finalize_prompt`
  *after* synthesis, never paraphrased.
- `synthesize()` iterates beats (optionally filtered by `beat_ids`),
  skips `prompt_locked=1` beats unless explicitly named with
  `force=true`, and writes `beats.prompt` / `prompt_source` /
  `brief`. Progress totals (`synthesis_progress`) count beats;
  payload gains `beat_id`.

### 5.3 Generate (stills) — per beat

- `generate()` submits one job batch per target beat. Correlation:
  `ComfyClient.submit(..., panel_id=..., beat_id=...)` round-trips
  `beat_id` onto `generation_jobs`; `handle_job_event` ingests
  outputs as `beat_images` rows keyed by the job's `beat_id`.
- **Seed function** (`storyboard_brief.py`):
  `beat_seed(base_seed, panel_sort_order, beat_sort_order,
  variant_index) = base_seed + (panel_sort_order * 100 +
  beat_sort_order) * 1000 + variant_index`. Deterministic, collision-
  free for < 100 beats/shot and < 1000 variants/beat. Replaces
  `panel_seed`.
- `variant_base` in-flight accounting moves to the beat:
  `count_beat_images(beat_id) + batch_size * len(queued/running jobs
  with that beat_id)` (`limit=10000` convention retained).
- `unload_vlm_during_generation` / `_synth_lock` interaction
  unchanged.

### 5.4 Compile (H3 video) — stays per shot

- `_panel_subjects` becomes: `union(beat.subject_ids over the shot's
  beats) ∪ beat-dialog speaker names`, resolved to subject rows at
  compile time. This is the single source the document sections AND
  `generate_video`'s reference/audio uploads read — the "document and
  uploads can never disagree" invariant becomes structural.
- `render_detailed_description` / `compute_timeline` gain per-beat
  framing: each `[Shot n]` line can open with the beat's shot-size /
  angle / lens phrasing (reusing the `SHOT_SIZES` / `ANGLES` /
  `LENSES` phrase maps, relocated from `storyboard_brief.py` to a
  shared module or `h3_compiler.py`). Framing finally reaches the
  video document.
- Anchors: the `keeper` value of `video_anchor` resolves to the
  keeper of the shot's **first beat** (lowest `sort_order`; validation
  fails the panel upfront if that beat has no keeper). `prev_last`
  unchanged. `fl2va`'s second keyframe remains out of scope, as
  currently documented.

## 6. API changes (`backend/api/storyboard.py`)

- **Storyboard PATCH/POST**: accept `notes` (nullable).
- **Panel PATCH**: drops `shot_size`, `angle`, `lens`, `notes`,
  `negative`, `prompt`, `prompt_locked`, `subject_ids`. Keeps
  `sort_order`, `action`, `duration_s`, video fields with the
  existing server-wins lock rule for `video_prompt`.
  `_reject_null_for_required` lists updated accordingly
  (`subject_ids`/`prompt_locked` leave the panel list).
- **Beat PATCH** (existing route, extended): accepts `shot_size`,
  `angle`, `lens`, `subject_ids`, `prompt`, `prompt_locked` with the
  panel's old server-wins rule moved here verbatim: a body containing
  `prompt` forces `prompt_locked=1, prompt_source='user'`.
  `subject_ids` joins the not-nullable list.
- **New** `POST /api/storyboard/beats/{beat_id}/select`
  `{image_id | null}` — replaces `POST /panels/{id}/select`.
- **`DELETE /beats/{beat_id}`** gains `?purge_images=true` and the
  keep/purge flow (§4).
- **`POST /{id}/synthesize`** and **`/{id}/generate`**: request bodies
  take `beat_ids` (replacing `panel_ids`) + `force` / `only_failed`
  semantics unchanged in spirit.
- **`POST /{id}/compose`**: unchanged shape; beats stage now subject
  to the 409 `confirm_required` gate.
- `GET /api/storyboard/{id}` tree: beats gain the new fields +
  `images` (the `beat_images` rows, paths via `to_native_path`);
  panels lose the dropped fields. Path-shape conventions
  (POSIX-stored, native-returned) carry over to `beat_images`
  untouched.

## 7. WebSocket events

- `panel_images_changed` → **`beat_images_changed`**
  `{storyboard_id, panel_id, beat_id, files}`.
- `synthesis_progress` gains `beat_id`; `synthesis_complete` counts
  are per-beat.
- All other storyboard-channel events unchanged.

## 8. Frontend (`frontend/src/`)

- **`types/storyboard.ts`**: `Panel` loses the dropped fields; `Beat`
  gains framing, `subject_ids`, `prompt`/`prompt_locked`/
  `prompt_source`, `selected_image_id`, `images`. `Storyboard` gains
  `notes`.
- **`stores/storyboard.ts`**: job correlation maps
  `jobToPanel`-equivalents keyed by beat for stills (video jobs stay
  panel-keyed); `selectImage()` targets the beat select route; WS
  handlers renamed per §7. The `refreshActiveJobs()` reload-survival
  path filters `GET /api/comfy/jobs` against the tree's beat ids as
  well as panel ids.
- **`PanelDetail.vue`**: shot pane keeps action / duration / video
  section (compile, anchor chip). Framing selects, subject picker,
  prompt textarea + lock, and the image strip / keeper picker move
  into **`BeatsEditor.vue`** per-beat rows. The commit-on-change +
  snapshot + resync-on-`[id, updated_at]` pattern (documented in
  CLAUDE.md) applies to every new beat-level editor field.
- **`StoryboardSettingsDialog`**: gains `notes`; negative field is
  already storyboard-level there.
- **`DeleteImagesDialog`** flow extends to beat delete / beats
  recompose confirm (§4).

## 9. Testing

- **DB**: beat CRUD with new columns; `beat_images` FK cascade;
  `select_beat_image` hide/unhide symmetry; `_release_beats` from
  `delete_beat` / `replace_panel_beats` / `delete_panel` /
  `delete_scene` / `delete_storyboard` (keep and purge variants);
  migration gate drops old tables, unhides orphaned media, purges
  panel-scoped jobs, and is idempotent.
- **Story stages**: shots validator without `subjects`; beats
  validator with subjects-name→id mapping, framing enum
  drop-to-NULL, roster rejection; beats compose gate (409 when
  images/locked prompts exist, pass with `confirm`).
- **Seeds**: `beat_seed` determinism + in-flight `variant_base`
  accounting per beat.
- **H3**: `_panel_subjects` union (beat subjects ∪ dialog speakers);
  per-beat framing phrasing in `render_detailed_description`;
  `keeper` anchor resolves first beat's keeper and fails validation
  when absent.
- **API**: panel PATCH rejects dropped fields ignored/absent
  cleanly; beat PATCH server-wins lock rule; beat select route;
  `beat_ids` targeting on synthesize/generate.
- Existing panel-level tests migrate to the beat level rather than
  being duplicated.

## 10. Out of scope

- `fl2va` second-keyframe anchor automation (stays manual in
  ComfyUI, as currently documented).
- Per-beat video generation ("Both/selectable" was considered and
  rejected — video is per shot).
- Any data migration of existing panel-level prompts/images (dev data
  is disposable by decision).
- Mobile UI (storyboard authoring remains desktop-only).
