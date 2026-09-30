---
paths:
  - "metascan/core/storyboard*.py"
  - "metascan/core/h3_compiler.py"
  - "metascan/core/shot_templates.py"
  - "metascan/core/video_targets.py"
  - "metascan/core/ref_describe.py"
  - "backend/api/storyboard.py"
  - "backend/services/storyboard_service.py"
  - "data/templates/**"
  - "data/prompt_guides/**"
  - "tests/test_storyboard*.py"
  - "tests/test_h3*.py"
  - "tests/test_story_quality_phases.py"
  - "tests/test_shot_templates.py"
  - "tests/test_video_targets.py"
  - "tests/test_ref_describe.py"
---

# Storyboards, the story engine and the H3 video pipeline (backend)

Moved out of the root CLAUDE.md so it loads only when you work with the files above;
the root CLAUDE.md still applies.

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
- **Scene merge is deterministic and non-destructive.**
  `DatabaseManager.merge_scenes(scene_ids)` (`POST
  /api/storyboard/{id}/scenes/merge`, spec
  `docs/superpowers/specs/2026-08-29-merge-scenes-design.md`) folds
  ADJACENT scenes into the first: descriptors from the first, `charge_out`
  from the last, `brief`/`notes` joined with a blank line, `arc_beats` the
  union in `ARC_BEAT_VALUES` order, `template_id` cleared, `composed_from
  = {stage: "merge", …}`. Panels are re-parented after the survivor's own
  (sort order continues; beats/images/jobs untouched) BEFORE the absorbed
  scene rows are deleted, so the scenes→panels cascade never fires;
  remaining scenes are resequenced 0..n-1. Non-adjacent or cross-storyboard
  ids raise `ValueError` → 400. The UI (Compose dialog checkboxes + "Merge
  selected", rail ⋯ → "Merge with next scene") calls `store.mergeScenes`,
  which refreshes and raises the normal "Rebuild shots?" downstream prompt
  for the merged scene. It exists because the scenes stage emits
  location/time units while a shot-list template is a dramatic unit.
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
