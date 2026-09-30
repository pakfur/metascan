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
  (`POST /api/i2v/generate` re-lints the submitted text). `I2vRunner`
  mirrors StoryboardRunner's layering: correlation via
  `generation_jobs.i2v_source_path` (nullable TEXT, no REFERENCES — the
  panel_id precedent), ingest keyed on that column (storyboard ingest
  keys on beat_id/panel_id; the two never collide), `i2v_videos_changed`
  on the `i2v` WS channel. Generated clips are VISIBLE library media
  (never hidden — unlike storyboard clips); star = `media.is_favorite`.
  `i2v_videos` has no FK to media: `list_i2v_videos` JOINs and lazily
  prunes rows whose media is gone; deleting the source image keeps the
  videos. Presets are kind `ref2v` tagged `minimax`/`i2va`
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
  i2v route: the dialog's footer Cancel runs `store.cancelAllJobs()` over
  every queued/running job for the open source image, and each job tile
  has its own ✕. Tiles leave via the `comfy` channel's `job_update` →
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
  don't re-add one. A Generate whose `requestSignature()` (prompt, seed,
  quality, effective steps, duration, size, loras) equals the last
  successful submit's asks `confirm()` first; the snapshot is
  component-local by decision, so earlier sessions never count.
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
  button, not a second click on the tile — a double-click (play) fires
  two single-clicks first, so `onSelectClip` also ignores
  `event.detail > 1`. `loadClip` arms `lastSubmitted`: the one deliberate
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
  cause, never the message text; `/lint` and `/generate` apply the same
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
  collapses newlines on save — correct here, because each H3 field is one
  logical line; its `width`/`rows` props are optional and default to the
  size its nine original callers were written against.
  **Seed policy and generate count are session-local, not `form_state`.**
  They are actions, not properties of a clip, so clicking a clip never
  changes how many videos the next Generate makes; the defaults (Fixed, 1)
  are byte-for-byte the old behaviour. `Fixed` forces the effective count
  to 1 by decision — the same seed would render the same clip N times — and
  the count field keeps whatever was typed for when the policy changes
  back. The batch submits serially, advancing the seed after each job so
  the field shows the next unused seed, and stops (rather than repeating a
  seed) when increment/decrement would leave `0..2^31-1`. The
  "nothing has changed" confirm is checked once for the batch, since any
  non-Fixed policy makes later submits differ by construction.
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
