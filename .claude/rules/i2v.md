---
paths:
  - "**/*i2v*"
  - "**/*I2V*"
  - "data/i2v_templates/**"
---

# Image to video (I2V)

Moved out of the root CLAUDE.md so it loads only when you work with the files above;
the root CLAUDE.md still applies.

- **i2v flow (image→video, MiniMax H3 I2VA).** Right-click an image →
  I2VDialog. `metascan/core/i2v_compiler.py` is pure: duration→beat-count
  table, GBNF grammar with the beat count baked into the root rule and
  camera constrained to `I2V_CAMERA_VALUES` (no `pov`), deterministic
  `assemble_i2v_prompt` (the base guide's I2VA structure — exact §2.1
  alignment line, `[Shot 1]` opener, three core fields), and advisory
  `lint_i2v_prompt` that runs on generated AND hand-edited prompts
  (`POST /api/i2v/lint` lints the text). `I2vRunner`
  mirrors StoryboardRunner's layering: correlation via
  `generation_jobs.i2v_source_path` (nullable TEXT, no REFERENCES — the
  panel_id precedent), ingest keyed on that column (storyboard ingest
  keys on beat_id/panel_id; the two never collide), `i2v_videos_changed`
  on the `i2v` WS channel. Generated clips are VISIBLE library media
  (never hidden — unlike storyboard clips); star = `media.is_favorite`, the
  favorite only (`setFavorite`, never a grade). A clip tile shows a read-only
  ✓ / ✗ badge from the row's `grade`; grades set in the I2V result viewer
  reach the tile live through `store.applyJudgment` (`judgmentMirror`), and
  a clip-list refresh never undoes one: `refreshVideos` drops a reply a newer
  refresh or another source image superseded, and puts back on its rows
  each ★ / grade the server answered after it went out
  (`utils/rowNotes.ts`, as the T2I store does; the tile ★ applies its
  answer by path through `applyJudgment`). Clips
  keep collecting grades, but no consumer trains on video.
  `i2v_videos` has no FK to media: `list_i2v_videos` JOINs and lazily
  prunes rows whose media is gone (never until a full clean is finished,
  which also re-ingests the clips still on disk outside the scan folders,
  `set_lineage_prune_guard`: CLAUDE.md, "Full clean & rescan keeps
  ratings"); deleting the source image keeps the videos.
  Presets are kind `ref2v` tagged `minimax`/`i2va`
  (`_validate_minimax_i2va`: MS_FIRST_FRAME required, MS_DURATION /
  MS_LORA_STACK / MS_RESOLUTION warn-if-missing); the fast/quality slots
  live in `config.json`'s `i2v` section (`get_i2v_config`).
  **Output resolution follows the source image's aspect ratio, always.**
  The selected image IS the first frame, so any other ratio letterboxes
  or crops it — there is deliberately no orientation control, and the
  user picks only a megapixel budget (`i2v.megapixels` /
  `default_megapixels`). `i2v_compiler.i2v_dims(src_w, src_h,
  megapixels)` preserves the source aspect, snaps each edge to a
  multiple of 32 (`I2V_DIM_MULTIPLE` — H3's width/height widgets step by 32) and floors at one multiple so an extreme panorama still
  yields a usable short edge; `I2vRunner.generate` takes `megapixels`
  (never client-supplied dimensions) and resolves the source's real size
  from the media row, falling back to the file header for an image that
  has not been scanned yet. The computed dims ride
  `GenerationParams.width`/`height` into **`MS_RESOLUTION`** (widgets
  `width`/`height`, optional for every kind — do NOT reuse `MS_LATENT`,
  whose contract requires `batch_size` that a resize node has no widget
  for). A preset with no `MS_RESOLUTION` keeps its baked-in resolution
  rather than raising, the `MS_DURATION` precedent, so presets
  registered before this feature need no retrofit. Ingest reads the dims
  back out of the job's stored `params` (not the in-memory `_job_meta`
  that carries quality/idea) and writes them to `i2v_videos.width` /
  `.height`, so they survive a server restart. `types/i2v.ts::i2vDims`
  is a display-only mirror for the dialog's live size hint — the backend
  recomputes and stays authoritative; keep the two in step.
  **`MS_STEPS` is a High-quality-only binding** (widget `steps`, put on
  the graph's `BasicScheduler`). `I2vRunner.generate(steps=…)` writes it
  only when `quality == "quality"` AND the preset binds the title — the
  Fast slot is a step-distilled turbo build, and 20–40 steps through a
  4-step distillation renders garbage, so never send steps for `fast`. A
  quality preset with no `MS_STEPS` keeps its baked-in count (the
  `MS_DURATION` precedent); `GET /api/i2v/config` reports that as
  `quality_steps_supported: false` (computed per request from
  `workflow_json`, never stored) and the dialog disables the selector
  with a hint instead of offering a no-op control. The validator is never
  told which slot a preset is headed for, so `_validate_minimax_i2va`
  reads the graph: `sampler_step_node` finds the baked-in step count and
  `STEP_DISTILLED_MAX = 8` splits the two builds — `no_steps` warns on a
  full-step graph without the title, `steps_on_distilled` warns on a
  turbo graph that carries it. `scripts/validate_i2v_workflow.py` prints
  `ok` / `n/a` for `MS_STEPS` on the same boundary. The steps ladder
  (`i2v.steps`, `default_steps`, default 20–40 / 25) lives in
  `get_i2v_config` like durations and megapixels. `i2v_videos.steps` and
  `.render_s` (idempotent column adds) feed the per-clip details label:
  both are read from the durable job row at ingest (`params.steps`;
  `render_seconds(started_at, finished_at)` — `job_outputs` fires BEFORE
  `_finish_job`, so a missing `finished_at` means "now"), unlike
  `quality`/`idea`, which are still in-memory `_job_meta`. `created_at`
  is SQLite `datetime('now')` — UTC with no zone marker — so
  `types/i2v.ts::formatI2vTimestamp` appends `Z` before formatting;
  parsing it bare reads it as local time. **Cancel is the generic
  `POST /api/comfy/jobs/{id}/cancel`** (`api/comfy.ts::cancelJob`), not an
  i2v route: the dialog's footer Cancel runs `store.cancelSourceRuns()` (cancel each
  of the image's runs, below, then `cancelAllJobs()` over every
  queued/running job for it), and each job tile has its own ✕. Tiles leave via the `comfy` channel's `job_update` →
  `cancelled`, and are also dropped on the POST succeeding so a missed WS
  frame cannot strand one.
  **Clip placement is `i2v.output_root` + `i2v.output_prefix`**, resolved
  by the pure `metascan/core/i2v_output.py::resolve_output_target`. The
  prefix is a path RELATIVE to the root (a leading slash is cosmetic)
  whose last component is the filename prefix; it is split on `/` FIRST
  and each component strftime-expanded and sanitized AFTER, so a slash
  born from a token (`%D`) can't create directories, and `..` raises.
  The runner appends `_next_output_number()` (epoch seconds, strictly
  increasing per process) and submits with **`output_name`** — a
  `generation_jobs` column that, unlike `output_prefix`, REPLACES
  ComfyUI's filename (only its suffix survives): ComfyUI's counter
  restarts whenever its output dir is cleared, so it can't be trusted in
  a long-lived library folder. `collect_outputs` routes named files
  through `_unclobbered`, which never overwrites — extra outputs and
  existing names get `_2`, `_3`…. An empty root keeps the original
  `<comfy.output_root>/i2v/<stem>/i2v_<stem><comfy name>` layout; a
  configured root that doesn't exist is a 400 BEFORE the first-frame
  upload, never auto-created. `%M` is minutes — `output_prefix_warnings`
  flags it when `%H` is absent, and `GET /api/i2v/output-preview` (always
  200; `error` is form data) feeds the config tab's live preview so date
  expansion has one implementation. The root is chosen with
  `DirectoryPicker.vue` over `GET /api/config/browse` — a browser file
  input cannot yield a server path, so the picker walks the SERVER's tree
  (directories only, dot-dirs hidden). Both routes are deliberately sync
  `def` (threadpool): they stat possibly slow `/mnt/<drive>` mounts.
  **`I2VDialog` closes only via its ✕** — no `@click.self` on the overlay;
  don't re-add one. The ✕ asks `confirm()` first when the form holds
  changes no render has used (below). A Generate whose `requestSignature()`
  (prompt, seed, quality, effective steps, duration, size, loras) equals the
  last successful submit's, with box text, Batch 1 and Count 1, asks
  `confirm()` first; the snapshot is component-local by decision, so
  earlier sessions never count. Only the dialog's button is called
  Generate: `POST /api/i2v/generate` is gone (the dialog posts
  `/batches`), and `I2vRunner.generate` is the per-video worker the run
  calls.
  **Each clip carries two sets of values, and only one is editable.**
  The as-rendered columns (`prompt_used`, `seed`, `duration_s`, `quality`,
  `steps`, `width`/`height`, plus `megapixels`/`loras`) are facts: written
  once at ingest, they feed the tile's details label and must keep
  agreeing with the metadata embedded in the file. `form_state` (JSON) is
  the editable copy — seeded at ingest from the dialog exactly as
  submitted (`i2v_form.build_form_state`, including a step selection a
  Fast render never applies), loaded when a clip is single-clicked, and
  autosaved into via `PATCH /api/i2v/videos/{id}`.
  `DatabaseManager.set_i2v_video_form_state` is deliberately the ONLY
  `i2v_videos` update there is; never add a route that writes the fact
  columns. **Nothing is persisted before ingest, by decision** — the form
  snapshot rides the in-memory `_job_meta` (so a cancelled/failed render
  leaves nothing, and a restart between submit and ingest leaves
  `form_state` NULL); do not move it onto `generation_jobs` or add a draft
  table. `i2v_form.form_state_for_row` makes the API shape complete either
  way — stored values win field-by-field, gaps (a legacy clip, or a field
  added to the form later) fill from the facts, megapixels snapped from
  `width × height` to the nearest configured option — and it runs in
  `I2vService`, not the DB layer, because it needs config. In
  `I2VDialog.vue`: with no clip selected the form is an unsaved scratch
  area; `savedSnapshot` stops a load echoing straight back as a save;
  saves are chained so an early slow request can't overwrite a later one,
  and capture the clip id at flush time (the user may have clicked
  another clip since); `flushFormState` runs before a clip switch, on
  **Stop editing**, on close and on unmount. Deselect is an explicit
  button, not a second click on the tile — a double-click (play) would
  otherwise fire two single-clicks first, so the tile click goes through
  `useDelayedClick` and a double-click never reaches `onSelectClip`. `loadClip` arms `lastSubmitted`: the one deliberate
  exception to that snapshot being session-local, because the clip's exact
  seed is now in the form. `withCurrentOption` keeps a loaded value that
  has since left the config lists visible in its select.
  **Découpage templates own an i2v clip's shot structure; the VLM fills
  only prose.** `metascan/core/i2v_templates.py` loads
  `data/i2v_templates/*.json` (id must equal the file stem; cached until
  `reload_i2v_templates()`), validated at load against the storyboard
  vocabularies imported from `storyboard_parse.py`/`storyboard_story.py`
  — never redeclare them; `pov` is excluded from angle and motion. Beats
  must tile `[0, duration_s]`; **shots are derived**: `cut`/`j_cut` open
  a `[Shot n] At MM:SS.mmm,`, `continuous` stays in the shot with an
  intra-shot timestamp (`shots_of`). The first beat is always
  `continuous`. `i2v_template_grammar` bakes role ids and beat count into
  the GBNF and makes `line` required exactly where a beat has a
  `speaker`. `assemble_i2v_template_prompt` renders the H3 document in
  code: first mention of a role → its `description`, after → its `tag`;
  `(S1)`/`(S2)` in first-line order; camera through
  `h3_compiler._CAMERA_PHRASES` with amplitude then speed; a `j_cut`
  puts the line before the cut with "the words carrying over from the
  previous shot". **Off-screen rule:** an unbound role (VLM said it is
  not in the picture) is dropped from Shot 1's framing (the only shot the
  picture anchors) and enters at its next beat; an unbound Shot-1 speaker
  speaks "Off-screen,". Assembly returns `(text, notes)` — the runner
  appends `notes` to the lint warnings. `lint_against_template` is
  text-only and warnings-only (shot count/numbering, Shot 1 has no
  timestamp, cut times increasing/inside duration/equal to the template,
  camera phrase per shot, a `<d>` per speaker beat); it scopes its
  `[Shot n]` scan to the `integrated_multimodal_description:` body via
  `i2v_compiler._DESCRIPTION_RE`, because `ALIGNMENT_LINE` itself contains
  "(from [Shot 1])" and would otherwise be misread as a shot marker; the
  beat label in its messages is `_beat_label` (e.g. `Beat 3, 5.0-10.0 s`).
  It cannot check role tags without the fill, so that is guaranteed by
  assembly for generated text and unchecked on hand edits.
  `lint_i2v_prompt(..., template=)`
  imports it at function level (import cycle). **`template_id = null` is
  the single-take path, byte-for-byte** — `tests/test_i2v_compiler.py::
  test_single_take_output_is_pinned` guards it; never route Single take
  through the template code. A template **fixes the duration**: `/prompt`
  400s on a mismatch, the dialog locks the Duration select. `template_id`
  is a `FORM_FIELDS` entry (null = single take) and rides `_job_meta["form"]`
  to ingest; `generate()` does not validate it against the library, since
  the prompt text already embodies the cadence and a renamed file must not
  block a render. `GET /api/i2v/templates` stamps `available` against
  `i2v.durations`. No aspect ratio (output follows the source) and no
  constraints/negative section (H3 has none) — by decision.
  `POST /api/i2v/prompt`'s 404 for an unknown `template_id` is keyed on
  `isinstance(exc.__cause__, I2vTemplateNotFound)` — a subclass of
  `I2vTemplateError` raised only from `get_i2v_template`'s id miss (a
  broken template *file* still raises the bare `I2vTemplateError` and is a
  500, not a 404/400 — a data fault must not be reported as a bad
  request). The runner re-raises the lookup failure as
  `I2vRequestError(str(exc)) from exc`, and the route checks the chained
  cause, never the message text; `/lint` and `/batches` apply the same
  split when resolving `template_id` (unknown → lint as a single take,
  broken library → 500). The dialog's Cadence
  `<select>` binds `:value`/`@change` rather than `v-model`, so picking a
  new cadence commits `onCadenceChange` synchronously (clearing the
  missing-template note on a genuine pick) instead of racing the
  `loadClip` watcher that also assigns `templateId` directly.
  **The dialog's four prompt panels are a view, not a representation.**
  `frontend/src/utils/i2vPromptSections.ts` locates four spans in the one
  prompt document — header (the alignment line through the anchored opener
  sentence), prompt, soundscape, music — and `replaceI2vSection` splices a
  single span, copying every other byte through, so editing one panel
  cannot disturb another. `prompt` stays the canonical value everywhere it
  already was (`form_state`, `i2v_videos.prompt_used`, the lint, what
  Generate sends): there is no sectioned representation in the API or the
  DB, and adding one would be a regression. **The header is derived, never
  assumed constant** — a découpage template's `look` sentence is spliced in
  *before* the opener, so it belongs to the header region; matching a fixed
  literal would mis-split every templated prompt. `parseI2vPrompt`
  validates its own spans (separators must be exactly the document's, spans
  ordered and covering the text, each label occurring once) and returns
  `null` otherwise, which drops the dialog back to one textarea — a
  hand-mangled prompt, a pre-panels clip, or a pasted field label degrades
  the UI instead of corrupting text. The markers mirror
  `i2v_compiler.py`'s `_OPENING` and field labels; if those change, the
  fallback catches it. `TextEditPopup` is shared with the storyboard and
  collapses newlines on save unless it is given `multiline`, which every
  I2V panel passes: a Prompt, Soundscape or Music field keeps its line
  breaks, and the lint's description ends at the next field, not at a
  blank line (`83b0875`; a blank line inside the prompt no longer cuts the
  check short). Its `width`/`rows` props are optional and default to the
  size its nine original callers were written against.
  **RAW / STRUCTURED is a view preference.** `components/generation/
  I2vPromptEditor.vue` shows the one prompt as a single box (RAW) or as the
  four panels (STRUCTURED, the default); the choice lives in localStorage
  (`metascan.i2v.promptView.v1`). A view switch never writes `prompt`, and
  when the document stops parsing while STRUCTURED is preferred the editor
  stays in RAW (`promptViewState`) even if a keystroke makes it parse again,
  so the view never flips under the cursor, until the user picks STRUCTURED
  or a clip load calls `resetView`; the header panel is read-only with no
  edit control.
  **Batch × Count, and a run is server-owned.** Batch (1..`i2v.max_batch_size`)
  is how many prompts a run uses, Count (1..`i2v.max_count_per_batch`) how
  many videos each renders; both default 20 and the route 400s outside them.
  The seed policy, Batch and Count are session-local, not `form_state`: they
  are actions, not properties of a clip, so clicking a clip never changes
  what the next Generate makes. The box text is batch 1's prompt; an empty
  box is written, as are batches 2..N (`I2vRunner.write_prompt`), so only
  then is a VLM needed. `plan_i2v_seeds` plans every seed before anything
  runs: `fixed` gives each batch the one seed and forces Count to 1 by
  decision (the same seed would render the same clip N times; the field
  keeps what was typed for when the policy changes back); `increment` /
  `decrement` run on across batches and a seed leaving `0..2^31-1` raises
  `I2vRequestError` (400) instead of repeating one; `random` starts at the
  box seed. The reply's `next_seed` is what the field shows afterwards.
  `POST /api/i2v/batches` returns at once (`I2vRunner.start_batch`
  creates an asyncio task, `_I2vBatch`, kept in memory only: **a restart
  loses the run's unwritten prompts and unsubmitted videos**, while jobs
  already submitted are `generation_jobs` rows and follow ComfyClient's own
  rules; `GET /api/i2v/batches` lists the live runs so a reopened dialog
  rebuilds its status). Order: with `comfy.unload_vlm_during_generation`
  every prompt is written first, then the VLM is shut down, then the jobs
  are submitted; off, each batch renders as soon as its prompt is written.
  Submitting goes through a per-run `window` (`i2v.window`, default 4,
  floored at 1; an `asyncio.Semaphore`): a job takes a slot when submitted
  and its terminal `job_update` (`done`, `failed`, `cancelled`) frees it, so
  a run keeps at most that many unfinished jobs in ComfyClient and the rest
  wait here. `I2vRunner.handle_job_event` takes `job_update` as well as
  `job_outputs`; a job that ends before `submit` returns finds no owner and
  is held in `_early_ended` (capped at 256), then settled by `_register` and
  `_submit_one` so it neither keeps a slot nor leaves its `_job_meta`
  behind (an unsettled one stalled the run). A failed job does not end the
  run. Events on the `i2v` channel, all carrying `{batch_id, source_path,
  phase, batch, total_batches, queued, total_videos}`: `batch_progress`
  (and `warnings`, the lint findings of the written prompts), then exactly
  one of `batch_done`, `batch_failed` (plus `error`: the writer or a
  submit failed; what was queued stays) or `batch_cancelled`.
  `POST /api/i2v/batches/{id}/cancel` stops the writing and cancels every
  unfinished job of the run; it is idempotent for a run that is already
  over (a short list of finished ids is kept) and 404s only for an id the
  runner never saw. The "nothing has changed" confirm is checked only for
  a single video from box text, since any larger run differs by
  construction.
  **The dialog has three panels and two clip lists.** The left list is the
  open image's clips (`GET /api/i2v/videos?source_path=`); the right is
  every clip, newest first, `GET /api/i2v/videos` without `source_path`
  paged by `limit` (1..200, default 60) and `before_id`. The all-videos
  listing **prunes nothing** (the per-image one still lazily prunes, under
  the same guard); the store keeps both lists, the open source and the
  runs, and `i2v_videos_changed` refreshes both. **Switching image:** a
  click on a clip of another image asks `confirmLeave` (kind `clip`), then
  `findSource` looks up the clip's source in the media store and
  `store.switchSource` makes it the open image; the form then loads the
  clip (`loadClip`). A media store that is loaded and lacks the path means
  **deleted**: the source is null, Generate and Generate prompt are
  disabled with `SOURCE_DELETED_NOTE`, and the clip's values can still be
  viewed. An empty store is loaded first, and when it is still empty the
  image is not called deleted (unknown is not deleted): it stays usable on a
  stand-in sized like the clip, since the server refuses a really missing
  source anyway. **The dialog has a `ready` state**, false until the store
  has opened and the config defaults are in the form: clip clicks (a toast
  says it is still loading), Generate and Generate prompt do nothing before
  it, since a click would be compared with a baseline that does not exist
  and the defaults would land over the clip's values.
  **Unsaved changes.** `frontend/src/utils/unsavedForm.ts` (pure,
  shared with the T2I dialog) compares the form with `cleanForm`, the form
  as it last matched a render or a load, and `confirmLeave` asks a native
  `confirm()` before a clip click, an image switch or the ✕ would drop
  them. A selected clip's edits autosave, so they are not unsaved unless
  the save failed (then it asks, in different words); the seed advance
  after a Generate and the values of a clip that vanished underneath the
  form move the baseline and never count as edits.
  **Prompt rewrites are offered, never applied.**
  `metascan/core/i2v_fixes.py` (pure, no model call) owns the two lint
  findings that can be fixed mechanically, as `I2vFinding(code, message,
  start, end, replacement)` — `replacement is None` means "warn only".
  `camera_findings` maps natural wording onto `_CAMERA_PHRASES` (leading
  adverbs become `with small/large amplitude` / `at slow/fast speed`
  AFTER the verb, the guide's order; arc/tracking drop their object
  because the guide phrase already says "the subject"; static and shake
  take no modifiers) and **returns None rather than guess** — "moves
  left" could be a pan or a truck. Every rewrite must itself pass
  `_accepted()`, the same prefix test the lint uses, or it is discarded.
  The lint is a PREFIX check, so "zooms into" / "tilts upward" already
  pass and are never findings — don't add synonyms for them.
  `speech_findings` wraps quoted speech as `<speaker> (S1) <verb>:
  <d>[Language] words</d>` per base-guide §4.4: words verbatim (only an
  attribution comma in `"Hi," she says.` becomes a period), id after a
  leading pronoun else before the (adverb+)verb, one id per
  `_speaker_key` (she/the woman share one), numbering continuing past
  ids already in the text, language from the script (Latin stays
  English). Quotes led by sign/label/screen/reads… are visible text and
  skipped silently; a quote with no speech verb is warn-only.
  `i2v_compiler.lint_i2v_prompt` gets its camera + speech messages from
  this module (it imports i2v_fixes; `lint_i2v_report` imports
  `lint_i2v_prompt` at function level to avoid the cycle). `POST
  /api/i2v/lint` returns `{warnings, fixes, fixed_prompt}`; `I2VDialog`
  lints 400 ms after the prompt or duration changes and enables **Apply
  fixes** only when `fixes.length > 0` AND `lintedFor === prompt` (a
  stale rewrite must never clobber newer typing). Never auto-apply on
  Generate: the box is "Generate uses this text".
- **Generation categories.** `POST /api/i2v/batches` takes `category_ids` (default `[]`), validated by `checked_category_ids` (404 `{code: "unknown_category"}` before upload/queue) and passed to `I2vRunner.generate(category_ids=…)`, which keeps them in the in-memory `_job_meta` (not the form). Ingest tags each new clip via `generation_categories.tag_generated_media` (source `generation`) before `i2v_videos_changed`; a deleted category is a logged skip. The dialog's selection is session-local like the seed policy (`metascan.i2v.categories.v1`).
