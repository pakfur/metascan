# Compose performance & rhythm — implementation plan

> **For agentic workers:** this is a *design-level* plan, written to be discussed before
> implementation (brief: "Do not begin implementation until we have discussed the plan").
> Once agreed, each phase below becomes its own bite-sized task plan via
> `superpowers:writing-plans`, executed with `superpowers:subagent-driven-development`.

**Goal:** make the beats stage emit dramatically alive shots (bodies that change, reaction
beats, contrasted durations, less setup) so the H3 prompt is usable without hand-rewriting.

**Architecture:** keep the four-stage grammar-constrained compose ladder
(`storyboard_runner._run_stage` → `generate_validated` → validator → lint → one targeted
re-roll) and add: an edit-capture instrument (Phase 0), a compile fix that stops dropping
performance text (Phase 0.5, new), per-subject performance fields (1), a narrow-scope
punch-up VLM pass (2), a beat `kind` (3), code-side durations (4), a coverage-cell library
(5), and arc-weighted scene time (6).

**Spec:** `docs/claude-code-planning-brief.md` + `docs/coverage_cells_v1.json`.
Context: `docs/storyboard-compose-pipeline.md` (accurate as of today, one caveat below),
`docs/scene-to-storyboard-prompt-chain.md` (intent only — its bible / decoupage /
screen_side / eyeline / two-panels-per-move machinery was never built).

## Global constraints (from the brief; every phase inherits these)

- Grammars stay in Python, built from the same enum constants the validators use.
- Retry ladder unchanged: `StoryError` → one blind retry; lint violations → one targeted
  re-roll with violations appended verbatim; survivors become warnings. Lints never
  hard-fail a stage.
- Stage-subset API (`stages`, `scene_ids`, `panel_ids`) and the skipped-panel continuity
  chain (`prev_action` / `prev_summary` / `prev_sizes` in `_beats_for_scene`) keep working.
- `check_compose_gates`, the `confirm`/purge semantics, and `prompt_locked`/`prompt_source`
  are preserved.
- `_synth_lock`, the `parallel_slots` semaphore, and the WS events (`story_progress`,
  `story_stage_complete`, `story_complete`, `story_error`) are unchanged.
- Every schema change gets an idempotent migration (`_idempotent_add_column` for additive
  columns; `PRAGMA user_version` bump only if a data backfill is needed) and a story for
  existing rows.
- Arithmetic is code-side; never re-prompt to fix a number.
- Beats stay unscaled by `story_scale`.

---

## 1. Findings — what the code actually does, and where the brief is wrong

Verified against `storyboard_runner.py`, `storyboard_story.py`, `h3_compiler.py`,
`database_sqlite.py`, `backend/api/storyboard.py`, `BeatCard.vue`, `data/meta_prompt.yml`,
and the dev database `data/metascan.db` (3 storyboards, 20 scenes, 63 panels, 174 beats;
read-only queries).

### 1.1 The performance signal already exists and is dropped at the compile boundary (brief's "verify before designing" — confirmed)

`h3_compiler.render_detailed_description` (lines 804–843) renders, per beat: `action`,
a framing sentence from `shot_size`/`angle`/`lens`, `render_camera(...)`, dialog, `sound`.
It never reads `emotional_intent`, `reveals`, `composition`, `light_quality`, or
`movement_motivation`. None of those reach the H3 prompt anywhere else either
(`render_summary` uses only `panel.action`; `build_sound_user_prompt` uses only `sound`).

And `emotional_intent` is *already* a performance field: `STORY_BEATS_SYSTEM` defines it as
"the visible physical evidence of the moment's feeling — jaw set, eyes fixed on the floor,
shoulders dropped — never an emotion label". In the dev DB, **149/174** `emotional_intent`
values contain a body-part noun (brief's Phase 2 regex, slightly widened), versus 131/174
for `action`. Examples from the DB:

> action: "The Girl's facial muscles fully relax into a neutral, slack state…"
> intent: "Cheeks and forehead completely smooth; mouth slightly open; eyes glassy…"

So the video model has been getting *one* of two performance sentences the compose stage
already writes. This is the brief's predicted one-line fix and it re-orders the plan:
**Phase 0.5 (new) — emit `emotional_intent` in the shot block** ships immediately after
Phase 0 capture starts, and Phase 1 becomes "add the *per-subject* split (listener +
gaze)" rather than "add performance at all".

Caveat that keeps Phase 1 alive: `emotional_intent` is one string per beat, so it describes
the primary subject. The listener still reaches H3 as a `<Subject N>` label with no body.

### 1.2 The shot-size distribution is skewed *tight*, not wide (brief's Phase 3 audit — the fear is inverted)

| shot_size | beats | share |
|---|---|---|
| CU | 55 | 32% |
| ECU | 43 | 25% |
| MS | 35 | 20% |
| WS | 22 | 13% |
| MCU | 17 | 10% |
| MLS / EWS | 1 / 1 | 1% |

57% of beats are CU/ECU; scene-opening first beats are WS 19 / EWS 1 (the lint works).
Faces are not too small for H3. If anything, the ECU share (25%) is where *body change*
becomes invisible — an extreme close-up of a pendant cannot show "the shoulders come
forward". Phase 3 should therefore not add a "tight frame at the peak" rule; it should add
the opposite guard: reaction beats default to CU/MCU (face + shoulders), never ECU.

Related dead fields: `composition` is `centered` on 165/174 beats; `lens` is `macro` on 34
(the ECUs); `camera_motion` is `static` 107 / `push_in` 46 / everything else 2. The model
is choosing exactly one value for composition and two for motion. This is the strongest
argument for Phase 5 (cells own camera): the model demonstrably cannot use these axes.

### 1.3 There is no edit history to backfill from (Phase 0 — build it)

- `beats.updated_at != created_at` on **12/174** beats (9 on 2026-08-21, 3 on 2026-08-26).
- `panels.updated_at != created_at` on 58/63 — but this is noise: `_sync_panel_duration`
  bumps `panels.updated_at` on every beat mutation, and `compile_video` writes
  `video_prompt`. Not an edit signal.
- `beats.prompt_locked` is 0 on every row; `panels.video_prompt_source` is `compiled` on
  58/63 and never `user`. No compiled prompt has been hand-edited through the app.
- `replace_panel_beats` DELETEs and re-INSERTs (new ids) on every beats recompose, so any
  edit history that lived on the row is destroyed by the next compose.

Two consequences. First, the brief's "every scene requires hand-rewriting" is not visible
in this database — either the edits happen outside the app (e.g. in ComfyUI/text), or the
rewritten beats were recomposed away, or the three boards here pre-date the frustration.
Phase 0 must capture *before* the next round of authoring or the plan has no data. Second,
capture cannot be a column on `beats`; it needs its own table that survives
`replace_panel_beats`.

### 1.4 Durations: flat, and the compiler rescales a second time

- Beat durations: min 2.0, p25 3.5, median 4.0, p75 4.5, max 10.0. In 21/63 panels every
  beat has the *same* duration; 33 more have only two distinct values. There is no pacing
  contrast to preserve — Phase 4's "proportional rescale flattens contrast" is not the
  current failure; "the model never wrote contrast in the first place" is.
- `rescale_beat_durations` (compose) rescales to the shots-stage `duration_s` when off by
  >25% — in either direction, including *stretching* a 7 s beat script to 12 s.
- `panels.duration_s` is **already derived** from the beat sum: `_sync_panel_duration`
  runs inside every beat mutation including `replace_panel_beats`. The brief's Phase 4
  instruction "derive `panels.duration_s` from the beat sum" is done; 0/63 panels
  disagree with their beat sum.
- `h3_compiler.compute_timeline` rescales beats to `panel.duration_s` *again* at compile.
  Because of the sync above the factor is 1.0 today, but any Phase 4 change that lets
  `panels.duration_s` diverge from the beat sum silently reintroduces proportional
  rescaling at compile. Phase 4 must keep them equal (or make `compute_timeline` trust
  the beats).
- The brief's Phase 4 and Phase 5 instructions contradict each other only apparently:
  "scale the whole cell by a single factor" *is* proportional rescaling. Both preserve
  ratios; both shrink absolute gaps. What the brief actually objects to is scaling
  *up* to fill a budget and scaling at all when nothing forces it. Recommendation in
  Phase 4: never scale up; scale down by one factor only when the sum exceeds
  `shot_cap()`.

### 1.5 Scene time allocation — confirmed, with numbers

Setup scene seconds / story total: sb8 22/84 (26%), sb7 26/211 (12%), sb6 22/428 (5%,
extended). Shots per scene is 2 for every scene in the two contemplative/standard boards
regardless of arc position. sb7's turn (`charge_in == charge_out == 4` on "Bubbling Hot
Tub") shows `lint_scene_charges`'s "turn has the largest swing" rule surviving as a
warning, as designed. Note the outline's `duration_target_s` (90–120) is never enforced
downstream: sb7 targeted 120 s and composed 211 s. Phase 6 gets its budget from that field
and would be the first consumer of it.

### 1.6 Other brief claims, checked

| Brief says | Code |
|---|---|
| `lint_beats` rejects triple shot sizes, unmotivated motion, empty `reveals`, opener not WS/EWS, turn tightest on beat 1 | Correct (lines 817–872). Every rule is a prohibition; 0/174 beats have empty `reveals` — the re-roll fixes it, so the model *is* padding a reveal onto reaction-shaped moments. |
| Validator drops unknown names without inventing ids | Correct (`validate_beats_response`, warns and drops). Same for dialog speakers. |
| Cell enums will be coerced to null silently | Correct — every camera field falls through `x if x in VALUES else None`. Mapping table in Phase 5. |
| Beats "2–6" per the pipeline doc | Grammar is `beat (…){1,5}` = 2–6; the system prompt says "three to five"; `pacing_guidance` says 2–4 (standard). Actual: 2 beats ×15 panels, 3 beats ×48. The model sits at the low end every time. |
| `subjects` grammar | `namelist` of 0–5 strings. Phase 1's object list needs a new nonterminal, not a change to `namelist`. |
| `is_cut` | 100/174 beats are cuts (57%). With beat == `[Shot n]`, `is_cut` only changes phrasing ("the shot cuts" vs "continuing without a cut"). Cells set it; Phase 5 copies it. |
| Beats temperature | 0.6. The older design doc recommends 0.35 for decoupage. Once cells own camera (Phase 5) the remaining fields are prose, so 0.6 is right; before that, 0.6 is part of why composition/motion are one-valued — the model isn't exploring, it's defaulting. Not a plan item, but a free knob to test in Phase 0's A/B. |

### 1.7 In-flight, unrelated

`metascan/core/h3_compiler.py` and `tests/test_h3_compiler.py` have uncommitted changes
(picture-label dedupe in `assign_reference_labels`). Not part of this plan; land or stash
before Phase 0.5 touches the same file.

---

## 2. Phases

Ordering here is the **recommended** order (see §7 for the resequencing from the brief):
0 → 0.5 → 3 → 1 → 2 → 4 → 5 → 6.

### Phase 0 — Gold capture (permanent instrument)

**Storage decision: a `beat_snapshots` table, not JSON on disk, not columns on `beats`.**
Rows must survive `replace_panel_beats` (which deletes beat rows), so no FK cascade to
`beats`; the schema already has the transaction hooks (`replace_panel_beats`,
`update_beat`, `_ingest_video_outputs`) to write from.

```sql
CREATE TABLE IF NOT EXISTS beat_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    storyboard_id INTEGER NOT NULL,          -- denormalized: survives cascades
    scene_id      INTEGER NOT NULL,
    panel_id      INTEGER NOT NULL,
    beat_id       INTEGER NOT NULL,          -- no REFERENCES: outlives the beat
    stage         TEXT NOT NULL CHECK (stage IN ('raw','accepted')),
    compose_run   TEXT NOT NULL,             -- uuid per compose_story call
    payload       TEXT NOT NULL,             -- full beat row as JSON
    context       TEXT NOT NULL,             -- JSON, see below
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_beat_snapshots_beat ON beat_snapshots(beat_id, stage);
```

`context` JSON: `{scene_index, scene_count, arc_beats, panel_sort_order, panel_count,
is_turn, shot_function (null until Phase 5), beat_sort_order, beat_count, kind (null until
Phase 3), pacing, story_scale}`. The brief's "shot function / is_turn / arc position / beat
kind / position" are all here; `function` and `kind` slots exist from day one so later
phases fill them without a migration.

**Pair boundary — chosen: render-and-keep.**
- `raw` is written inside `replace_panel_beats` (same transaction, one row per inserted
  beat) — the compose output *before* any edit. `compose_story` mints `compose_run`
  and threads it to the DB call.
- `accepted` is written by `StoryboardRunner._ingest_video_outputs` when a clip for the
  panel ingests: one row per current beat of that panel. That is the only moment the user
  has demonstrably decided "this beat script is worth rendering". Repeated renders
  overwrite (delete the panel's previous `accepted` rows for the same `compose_run`
  first) so the *last* render before a recompose is the final.
- Intermediate saves are **not** stored. The pair is `raw` vs the latest `accepted`; the
  per-field diff is computed on read. Why not idle-timeout: WSL2 sessions idle
  unpredictably and the timer would need to live in the browser. Why not an explicit
  Accept button: it's one more thing to forget, and the brief's flag ("did I re-render and
  accept?") falls out of render-and-keep for free — a panel with `raw` rows and no
  `accepted` row is exactly "gave up / never rendered".
- "Edited but never rendered" remains observable: `beats.updated_at != created_at`
  with no `accepted` row. Reported as its own count.

**Files:** `database_sqlite.py` (DDL in `_init_database`, `write_beat_snapshots(conn, …)`
helper called from `replace_panel_beats` and a new `snapshot_panel_beats(panel_id, stage,
compose_run)`; `edit_stats(storyboard_id)` reader), `storyboard_runner.py`
(`compose_story` mints `compose_run`; `_ingest_video_outputs` calls
`snapshot_panel_beats(..., "accepted", …)`), `backend/api/storyboard.py`
(`GET /api/storyboard/{id}/edit-stats`), `backend/services/storyboard_service.py`,
`scripts/edit_stats.py` (CLI over all boards, prints the table in §3), frontend: a
read-only "Edit volume" block in `OutlineDialog.vue` (the Compose dialog) showing the
per-field table for the current board. No migration version bump (additive table).

**Existing rows:** the three existing boards get no `raw` rows; they are excluded from
the metric (reported as "uninstrumented"). Nothing to backfill (§1.3).

**Observed at a render:** compose a board → edit → generate video → `edit_stats` shows
per-field counts for that panel. This is the only phase whose render observation is the
instrument itself.

**Tests:** `tests/test_storyboard_beats_db.py` — raw rows written by `replace_panel_beats`
survive a second `replace_panel_beats`; `accepted` overwrite semantics; `edit_stats`
diff on a hand-built pair. `tests/test_storyboard_videogen.py` — `_ingest_video_outputs`
writes `accepted`.

### Phase 0.5 (new) — Emit `emotional_intent` in the compiled shot block

One renderer change in `render_detailed_description`, both branches (normal and POV):
after the action sentence, if `beat.get("emotional_intent")` is non-empty, append
`_sentence(_labeled(emotional_intent))`. Do **not** emit `reveals` (it is audience-facing
bookkeeping, not visible action) or `movement_motivation` (already folded into the camera
phrase's intent; H3 doesn't need the why). `composition`/`light_quality` are a separate
question — 95% `centered` means emitting them today adds "centered composition" to every
shot; defer until Phase 5 gives them real values.

**Lint interaction:** `_lint_word_count` floor 150 / target 350–500 — adding one sentence
per beat moves typical panels *toward* the target. `build_expectations` needs no change
(it checks labels, dialog, timing, camera vocab).

**Files:** `h3_compiler.py` (~6 lines), `tests/test_h3_compiler.py` (intent appears in
each `[Shot n]`, subject-label substitution applies, POV branch), docs:
`storyboard-compose-pipeline.md` "What consumes the finished tree" line.

**Observed at a render:** recompile any existing panel (`force`) and render; the delta is
the only variable. This is the cheapest render experiment in the plan and should run
before Phase 1 is designed in detail — if H3 visibly responds to one performance
sentence, the per-subject split is worth its migration; if not, Phase 1 is downgraded.

**Rollback:** revert the six lines; recompile.

### Phase 3 (moved up) — Beat `kind`

Moved ahead of Phase 1 because it is one enum column with no shape change, it unblocks
Phase 4 (durations keyed on `kind`) and Phase 5 (cells carry `kind`), and it delivers the
first positive lint — the reaction beat — which is the brief's own diagnosis of why scenes
read as event-event-event.

**Schema:** `ALTER TABLE beats ADD COLUMN kind TEXT` (nullable; via
`_idempotent_add_column`). Existing rows: NULL, treated as `action` everywhere.
`BEAT_KIND_VALUES = ("action", "reaction", "establishing", "insert")` in
`storyboard_story.py`; `_BEATS_TEMPLATE` gains `"kind" ws ":" ws kind` with
`kind ::= {kind_alts}` (nullable); validator coerces like the camera enums.
`_BEAT_UPDATABLE`, `BeatPatch`, `replace_panel_beats` INSERT, `Beat` TS type, and a
`kind` select in `BeatCard.vue` (next to Cut).

**Lints (`lint_beats`):**
- `reveals` empty → violation **unless** `kind == "reaction"` (and, with `INSERT`-mapped
  cells in Phase 5, unless `kind == "insert"` and the beat has an action).
- New positive rule: in a turn panel with ≥2 beats, the beat immediately after the
  tightest-framed beat must be `kind == "reaction"`; if the tightest beat is the last
  beat, violation "the turn lands on the final beat with no reaction hold after it —
  add a reaction beat". The brief's "any beat carrying dialog with subtext must be
  followed by a reaction" can't be evaluated per beat — subtext is a *panel* field
  (`panels.subtext`), and there is no per-line subtext. Recommended reduction: in a panel
  whose `subtext` is non-empty, the **last** dialog-carrying beat must be followed by a
  `reaction` beat. Every dialog beat would over-constrain 2–3-beat panels.
- Tightness guard from §1.2: `kind == "reaction"` with `shot_size == "ECU"` → "a reaction
  beat needs the face and shoulders: use CU or MCU".

**Prompt:** one paragraph in `STORY_BEATS_SYSTEM` defining the four kinds, stating that a
reaction beat *shows nobody acting and reveals nothing new*, and that one is required
after the turn; `build_beats_user_prompt` gets no change (the turn line already exists).
Beat count guidance: raise `beats_min` for standard from 2 to 3 so a reaction beat isn't
crowding out the action (`_PACING_TABLE`).

**Compile:** `kind` is not emitted as text. A reaction beat's `action` already reads as
prose ("She does not move.").

**Observed at a render:** recompose beats on a turn panel (gated 409 → confirm) and check
the `[Shot n]` after the turn is a hold on a face.

**Rollback:** column stays (nullable, ignored by old code); revert grammar/lint. No
`user_version` bump needed.

### Phase 1 — Per-subject performance

**Schema decision: a new JSON column, not a reshaped `subject_ids`.**
`subject_ids` is read by `_panel_subjects`, `generate()`'s `_primary_subject`,
`h3.assign_speakers`, `check_compose_gates` (indirectly), `BeatCard`'s cast chips, and the
PATCH path. Changing its element type touches all of them for no gain. Instead:

```sql
ALTER TABLE beats ADD COLUMN performances TEXT NOT NULL DEFAULT '[]'
-- [{"subject_id": 3, "performance": "the smile goes out of her face", "gaze": "LOOK_L"}, ...]
```

Invariant (validator + `update_beat`): every `performances[].subject_id` ∈ `subject_ids`;
`subject_ids` stays the casting list and its order. Existing rows: `[]`; the compiler
falls back to `emotional_intent` when `performances` is empty (so Phase 0.5's output is the
floor, never regressed).

**Grammar:** `subjects` becomes a list of objects:
```
subjects ::= "[" ws (perf (ws "," ws perf){0,5})? ws "]"
perf ::= "{" ws "\"name\"" ws ":" ws string ws "," ws "\"performance\"" ws ":" ws string ws "," ws "\"gaze\"" ws ":" ws gaze ws "}"
gaze ::= {gaze_alts}   -- GAZE_VALUES = ("LOOK_L","LOOK_R","TO_CAMERA","DOWN","UP","OFF_L","OFF_R","CLOSED"), nullable
```
Validator: same name→id mapping and unknown-name drop; `subject_ids` = mapped ids in
order, `performances` = mapped objects. `emotional_intent` stays in the grammar for now
(it becomes the *primary* subject's performance in practice; Phase 2 decides whether to
retire it — see §6).

**Prompt:** the register rule from `coverage_cells_v1.json._meta.register_rule`, verbatim
into `STORY_BEATS_SYSTEM`: "Every action and performance string names a body part and a
change to it. 'She looks upset' is a state… 'The smile goes out of her face' is a
change." Plus: "every subject in frame gets a performance, including the one who is only
listening."

**Compile:** in `render_detailed_description`, after the action sentence, emit each
performance as `<Subject N> {performance}.`; gaze renders through a small phrase map
(`LOOK_L` → "looks screen-left", `TO_CAMERA` → "looks into the lens", `CLOSED` → "eyes
closed", …). Fallback to `emotional_intent` when `performances == []`. `_lint_camera_vocab`
must ignore these sentences (they contain no camera phrases; verify with a test that
"looks screen-left" isn't matched by the `pan_left` phrase).

**UI:** `BeatCard.vue` cast section — each checked subject gets a one-line performance
input + gaze select (commit-on-change, same local/snapshot pattern, keyed on
`updated_at`). `Beat.performances: Performance[]` in `types/storyboard.ts`; `BeatPatch`
gains `performances: Optional[List[Performance]]`; `_reject_null_for_required` list gains
`performances`.

**Gold capture:** `context` unchanged; `payload` picks the new column up automatically
(full row JSON). The per-field diff gains `performances.<subject_id>.performance` and
`.gaze` keys.

**Observed at a render:** a two-hander panel where the listener has a performance line;
compare against the Phase 0.5 render of the same panel.

**Rollback:** revert code; column stays with `'[]'` default; no data loss. If the grammar
change misbehaves (e.g. the model stops emitting subjects at all), the validator's
fallback path — accept a bare string element as `{name, performance: null, gaze: null}` —
should be in from the start so a partial rollback of the *prompt* alone is possible.

### Phase 2 — Punch-up pass + register lint

**Shape: reuse `generate_validated`, with a different validator contract.** The pass
consumes valid beats and produces a *patch*:
```
root ::= "[" ws rw (ws "," ws rw){0,5} ws "]"
rw ::= "{" ws "\"index\"" ws ":" ws [0-9] ws "," ws "\"action\"" ws ":" ws string ws "," ws "\"performances\"" ws ":" ws perfs ws "}"
```
Validator `validate_punchup_response(raw, beats, roster)`: index must exist; count must
equal `len(beats)` (else `StoryError` → blind retry); names map exactly as in beats;
returns the beats list with only `action` and `performances[].performance` replaced —
camera, duration, `is_cut`, `kind`, dialog, `reveals`, `gaze`, `subject_ids` are copied
from the input, so the pass structurally *cannot* change them. Lint =
`lint_register(beats)`: regex
`\b(hand|hands|jaw|shoulder|shoulders|eye|eyes|breath|knuckle|knuckles|spine|throat|mouth|chin|finger|fingers|chest|head|face)\b`
over `action` and every `performance`; violation text names the beat/subject. One targeted
re-roll; survivors → warnings prefixed `panel <id>: register:`.

**Where it runs:** inside `_beats_for_scene`, after `lint_beats`' re-roll and before
`rescale`/`replace_panel_beats`, under the same semaphore slot. Second VLM call per panel
(`temperature=0.7`, `max_tokens=1200`). Skipped when `config.story.punch_up` is false
(default true once shipped) and for panels where every beat already passes
`lint_register` (saves the call when Phase 1's prompt alone suffices — this skip rate is
itself a metric).

**Exemplars:** `build_punchup_user_prompt(beats, cells)` injects 2–3 `action_exemplar` +
`performance_exemplar` pairs from `coverage_cells_v1.json` (Phase 5's loader, or a
minimal `load_cells()` that only reads exemplar strings if Phase 2 lands first), with
`{A}`/`{B}` bound to the panel's first two roster names. The Phase 0 pairs are never
injected (brief's reason: floor, not target).

**Retire `emotional_intent`?** After Phase 1+2, `emotional_intent` duplicates the primary
subject's `performance`. Recommendation: keep it in the grammar one more cycle, drop it
from the compiled output once `performances` is non-empty, and retire it from the grammar
when the edit metric shows it is never edited. Not a migration — the column just stops
being written.

**Observed at a render:** same panel rendered from (a) compose without punch-up and (b)
with — `config.story.punch_up` is the switch. Regression measure from §3 applies from here.

**Rollback:** config flag off; nothing persisted beyond ordinary beat rows.

### Phase 4 — Durations computed, not generated

**Narrow first version (recommended):**
1. `derive_beat_durations(beats)` in `storyboard_story.py`, pure:
   `reaction` 1.5 s (2.0 if it follows the tightest beat in a turn panel — "post-turn
   hold" 4–6 s from the brief is applied only to the *last* beat of a turn panel);
   `insert` 1.0 s; `establishing` 3.5 s; `action` = 2.5 s base + dialog time, where dialog
   time = `words / 2.5` s summed over the beat's lines (+0.6 s per line for the breath).
   Then `+0.5 s` per beat for the charge delta when the panel is a turn panel (one number,
   not a model field). Every constant lives in one `_DURATION_RULES` dict.
2. Remove the model's `duration_s` from `BEATS_GRAMMAR` and from the beats prompt's "roughly
   K seconds each"; the shots-stage `duration_s` remains as the *target* line only.
3. Replace the `rescale_beat_durations` call in `_beats_for_scene` with
   `fit_beats_to_cap(beats, shot_cap(video_target))`: if the sum exceeds the cap, multiply
   every beat by `cap / sum` (one factor — this is the cell rule and it is the only
   scaling that ever happens); never scale up. `panels.duration_s` follows via
   `_sync_panel_duration` (already true), so `compute_timeline`'s factor stays 1.0.
4. **No panel splitting in v1.** Blast radius of splitting: `panels.video_prompt` /
   `video_compiled_anchor` / `video_anchor='prev_last'` chains (a split changes which panel
   is "previous"), `panel_videos` ownership, `generation_jobs.panel_id`, `panel_ids` in
   compose/compile/generate filters, `is_turn` uniqueness per scene, `subtext`,
   `image_loras`/`video_loras`, `PacingStrip`, `OutlineRail` counts, the beats-recompose
   gate (a split panel has beats with new ids → gate semantics change). That is most of
   the storyboard surface. Instead, v1 caps beats per panel through the grammar (`{1,4}`)
   and lets the cap-fit warn: "beat script sums to 19.4 s, clamped to 15 s — split this
   shot in the shots stage". Splitting is Phase 4b, gated on the metric showing clamps are
   frequent (> 20% of panels).

**UI:** `BeatCard`'s duration input stays editable (hand override); `PacingStrip` already
warns over cap.

**Observed at a render:** a dialogue panel whose lines are long — the clip's speech no
longer overruns its beat; a reaction beat is visibly short.

**Rollback:** re-add `duration_s` to the grammar, restore the `rescale_beat_durations`
call. No schema.

### Phase 5 — Cell library

**Loader:** `metascan/core/coverage_cells.py`, pure. `load_cells(path=data/
coverage_cells_v1.json) -> Dict[function, Cell]`, validated at import/first use with a
hard `CellError` on any value that does not map — never coerced. Mapping table (the
brief's conflict #1), all applied at load time:

| Cell vocabulary | metascan constant | Note |
|---|---|---|
| shot_size `EWS/WS/MLS/MS/MCU/CU/ECU` | same | direct |
| `TWO_SHOT` | `shot_size="MS"` + `composition="deep_staging"` unless the cell sets composition | two-shot is a framing of MS |
| `OTS` | `shot_size="MCU"`, `angle="ots"` | OTS is an *angle* in `ANGLE_VALUES` |
| `INSERT` | `shot_size="ECU"`, `kind="insert"` | `kind` carries the semantics |
| angle `EYE/LOW/HIGH` | `eye/low/high` | lowercase |
| lens_mm `14,24` / `35,50` / `85,135` | `wide` / `normal` / `tele` | `macro` never produced by a cell |
| composition `THIRDS_L/THIRDS_R/CENTERED/SYMMETRICAL/NEGATIVE_SPACE/FRAME_IN_FRAME/LEADING_LINES/DEEP_STAGING` | snake_case equivalents | `FLAT_STAGING` (unused by v1 cells) → `CellError` |
| camera_motion `STATIC/PUSH_IN/PULL_OUT/PAN_L/PAN_R/TILT_UP/TILT_DOWN/TRUCK_L/TRUCK_R` | `static/push_in/pull_out/pan_left/…` | direct |
| `STEADICAM` | `tracking` | |
| `HANDHELD` | `shake_slight` (`shake_strong` when amplitude `large`) | |
| `RACK_FOCUS` | `static`, keep `movement_motivation` as prose appended to `action` | no focus vocabulary in H3 phrases; the motivation text is the only place it survives |
| `CRANE_UP/DOWN` | `pedestal_up/down` | |
| amplitude/speed `medium` | `None` | metascan has only small/large, slow/fast; null is allowed and renders nothing |
| light `SOFT/HARD/DAPPLED/MOTIVATED_WINDOW/PRACTICAL/FIRELIGHT` | `soft/hard/dappled/window/practical/firelight` | |
| gaze | `GAZE_VALUES` (Phase 1) | direct |
| `screen_side` | ignored | deferred per brief |

**Selection key:** `panels.function TEXT` (nullable; `SHOT_FUNCTION_VALUES =
("dialogue_exchange","confrontation","reveal","arrival","reaction","physical_action",
"transit","contemplation")`), one grammar field + one prompt sentence in the shots stage,
validator-coerced. `transit` and `contemplation` have **no cell in v1** → a panel with
those functions composes beats the current way (model chooses camera) and gets a
`story_stage_complete` warning "no cell for function X"; that is the load-time-vs-
runtime split the brief asks for.

**Beats stage with a cell:** `apply_cell(cell, panel, roster)` produces the beat skeleton
(camera fields, `is_cut`, `kind`, `gaze`, base `duration_s` × one factor from Phase 4's
cap-fit); the beats grammar for a celled panel drops every camera field and `kind`
(`beats_grammar_for_cell(n_beats)` with exact count `{n-1}` repetitions) and asks only for
`action`, `performances[].performance`, `reveals`, `emotional_intent`, `sound`, `dialog`.
The prompt injects the cell's exemplars with `{A}/{B}/{OBJ}` bound to the roster
(binding rule: `A` = first `subject_ids` of the panel's previous beats or the roster's first
subject; `B` = second; `OBJ` = a roster subject only if one is left, otherwise the literal
"the object"). Exemplar text never reaches `beats` rows.

**Opener conflict (brief's conflict #2) — recommend relaxing the lint** to "the scene's
opening shot must contain at least one WS/EWS beat" *and* constrain cell selection: the
shots-stage prompt is told which shot is the scene opener and that its function should be
`arrival` (or any function whose cell contains a WS/EWS beat). Relaxing alone lets
`reveal_kuleshov_4` open a scene (WS at beat 2, which is fine dramatically); constraining
alone forbids nothing useful. Both together keep the geography guarantee without
forbidding the two tight-opening cells anywhere but scene openers.

**Phase 3/4 interplay:** `lint_beats`' triple-size rule must skip celled panels for the
camera axes (the cell is authoritative; lint only the prose rules and the register).

**Observed at a render:** a `confrontation` panel — five clips whose framing tightens
monotonically with the inverted heights.

**Rollback:** `config.story.cells=false` restores the model-camera path; `panels.function`
stays nullable.

### Phase 6 — Scene time allocation

**No schema.** `allocate_scene_seconds(scenes, duration_target_s, scale) -> List[float]`,
pure, in `storyboard_story.py`: weight per arc stage `setup 0.12, rising 1.0 (split
evenly among rising scenes), turn 0.28, climax 0.28, resolution 0.10`, normalized to
`outline.duration_target_s` (the first downstream consumer of that field, §1.5), setup
clamped to ≤ 15% for `short`/`standard`. Shots per scene = `round(seconds /
pacing.panel_target_s)` clamped to `[1, scale.shot_cap]`, replacing the uniform
`shots_min..shots_max` line in `build_shots_user_prompt` with an exact count ("Produce
exactly N shots" — the older design doc's observation that small models obey exact
numbers is already the binding mechanism today).

**Outline directive:** one sentence in `STORY_OUTLINE_SYSTEM`: "Open at the latest moment
at which the story still parses; deliver setup as embedded exposition inside rising
action rather than as its own stage — omit `setup` from the arc unless the premise cannot
be understood without it." `lint_scene_charges` needs no change (subset arcs already
pass).

**Observed at a render:** total runtime of a `standard` board's first scene vs. the
board's total.

**Rollback:** revert prompt builder; nothing persisted.

---

## 3. The edit-volume metric

Computed by `db.edit_stats(storyboard_id)` from `beat_snapshots`:

- Universe: beats with a `raw` row (instrumented composes only).
- Per beat: `fields_changed` = set of top-level fields whose `raw.payload[f] !=
  accepted.payload[f]` (dialog compared as normalized text; `performances` compared per
  subject); a beat with no `accepted` row is counted in `unrendered`, not in the field
  totals.
- Per scene (and per board): `edit_rate[f] = beats_with_f_changed / beats_rendered`,
  plus `beats_deleted` (raw ids absent from the panel's accepted set), `beats_added`
  (accepted ids not in raw), `duration_delta_mean`.
- Surfaces: the Compose dialog block (current board), `scripts/edit_stats.py --all`
  (table across boards, grouped by `compose_run` date so pre/post-phase runs separate),
  and the `GET /api/storyboard/{id}/edit-stats` JSON both read.

**Pass/fail thresholds**, measured over ≥ 20 rendered beats composed *after* the phase
ships, on premises also composed before it:

| Phase | Primary field(s) | Pass | Fail (re-plan) |
|---|---|---|---|
| 0.5 | none — qualitative render check | H3 output visibly changes with the sentence | no visible change → downgrade Phase 1 to prompt-only |
| 3 | `beats_added` where added beat is `reaction`; `reveals` | reaction insertions drop ≥ 50% | unchanged |
| 1 | `performances.*.performance` (new edits), `action` | `action` edit rate drops ≥ 30%; listener performances edited < 50% | `action` unchanged |
| 2 | `action`, `performance` | `action` edit rate ≤ 25%; register lint survivors ≤ 10% of beats | `action` edit rate ≥ 50% ("still rewriting every line" — the brief's explicit fail condition) |
| 4 | `duration_s` | duration edit rate ≤ 20%; cap clamps ≤ 20% of panels | clamps > 20% → do 4b (split) |
| 5 | `shot_size`/`angle`/`lens`/`camera_motion` | camera edit rate ≤ 10% on celled panels | camera edits ≥ pre-phase rate → cells are exemplars only (brief's alternative) |
| 6 | `beats_deleted` in setup scenes; setup share | setup ≤ 15% of runtime; first-beat deletions drop | unchanged |

Regression measurement (brief's use #2): `scripts/edit_stats.py --regress <board>`
re-composes the same premise into a scratch board and reports, per raw beat, whether the
new raw text already contains the body nouns the accepted version added, whether a
`reaction` beat exists where one was inserted, and the setup share — scorable without
a human.

---

## 4. Dependency order and parallelism

```
Phase 0 (capture) ─┬─► Phase 0.5 (compile emit)  [independent; ship the same day]
                   ├─► Phase 3 (kind) ─┬─► Phase 4 (durations)
                   │                   └─► Phase 5 (cells)  ◄── Phase 1 (performances)
                   ├─► Phase 1 ─► Phase 2 (punch-up; can use a stub exemplar loader)
                   └─► Phase 6 (scene allocation)  [independent of everything above]
```

- Parallel tracks after Phase 0: {0.5}, {3 → 4}, {1 → 2}, {6}. Phase 5 waits for 1 and 3.
- Phases 1 and 3 both edit `_BEATS_TEMPLATE`, `validate_beats_response`, `BeatCard.vue`,
  `BeatPatch`, `replace_panel_beats` — serialize their merges even though the logic is
  independent.
- Phase 6 touches only the outline/shots prompt builders; safe alongside anything.

---

## 5. Rollback per phase

| Phase | Persisted change | Rollback |
|---|---|---|
| 0 | `beat_snapshots` table | Leave the table; old code never reads it. Never drop — it is the instrument. |
| 0.5 | none | revert renderer; recompile with `force`. |
| 3 | `beats.kind` nullable | revert grammar/lint/UI; column ignored. |
| 1 | `beats.performances` (`'[]'` default) | revert grammar/compile/UI; column ignored; the validator's bare-string fallback allows a prompt-only rollback. **Migration story:** additive via `_idempotent_add_column`; no `user_version` bump; existing rows `'[]'` → compiler falls back to `emotional_intent`. |
| 2 | none | `config.story.punch_up=false`. |
| 4 | none (beat/panel values only) | restore `duration_s` in grammar + `rescale_beat_durations`; already-composed boards keep their computed durations (they are valid data). **No migration** because nothing structural changes; if 4b (split) ever lands it needs its own plan with a `user_version` bump and a `panels.split_from` pointer. |
| 5 | `panels.function` nullable | `config.story.cells=false`; column ignored. |
| 6 | none | revert prompt builders. |

---

## 6. Open questions and decision points

1. **Retire `emotional_intent` after Phase 1?** Recommend: keep in grammar through Phase 2;
   stop compiling it once `performances` is non-empty; drop from grammar when
   `edit_stats` shows it unedited across a cycle. Cost of keeping: ~40 tokens/beat.
2. **Which ships first, 0.5 or 3?** Recommend 0.5 the same day as 0 — it is six lines and
   it is the experiment that tells us whether H3 responds to performance prose at all.
3. **Reaction-after-dialog rule scope** (Phase 3): recommend "last dialog beat in a
   subtext-bearing panel", not "every dialog beat". Reversible in one line.
4. **Panel splitting** (Phase 4b): recommend deferring behind the clamp-frequency
   metric (> 20% of panels clamped). If the user already knows shots routinely exceed
   15 s, say so and 4b gets planned now.
5. **`transit`/`contemplation` without cells**: recommend authoring two more cells before
   Phase 5 ships rather than leaving a warning path in production; both are ≤ 3 beats.
6. **Opener lint**: recommend relax + constrain together (Phase 5 text).
7. **Beats temperature**: 0.6 today; recommend an A/B at 0.4 during Phase 0's first
   instrumented cycle. Zero code beyond the constant; the metric decides.
8. **`{OBJ}` binding** when the roster has no object subject: recommend the literal
   "the object" in exemplars and let the model name it in `action`; alternatively require
   the shots stage to emit an `object` string for `reveal`/`physical_action` functions
   (one more grammar field). Start with the literal.
9. **Should `accepted` also be written on `compile_video`?** No — compiling is cheap and
   exploratory; rendering is the commitment. But record `compile_count` in `context` so
   "compiled five times, never rendered" is visible.

---

## 7. Cut or resequence

- **Resequence: Phase 3 before Phase 1.** Smaller change, unblocks 4 and 5, delivers the
  positive lint. Phase 1's per-subject split is the more expensive change and its value
  is now conditional on the Phase 0.5 render experiment.
- **Add: Phase 0.5.** The brief predicted it; the code confirms it.
- **Cut from Phase 1: `emotional_intent` migration.** Nothing to migrate — the column
  exists and is populated; only the compile path was missing.
- **Cut from Phase 3: the "tight frame at the peak" audit rule.** Data says the opposite
  problem; replaced with the reaction-beat ECU guard.
- **Cut from Phase 4: panel splitting (v1).** Blast radius is most of the storyboard
  surface; gate it on the clamp metric.
- **Cut from Phase 4: "post-turn hold 4–6 s" as a general rule.** With a 15 s cap and 3–5
  beats, a 5 s hold is a third of the clip; apply it only to the turn panel's last beat.
- **Cut from Phase 5: `screen_side`.** Already deferred by the brief; the loader ignores
  it explicitly.
- **Cut from Phase 6: per-scene `duration_target_s` column.** Derivable at shots time
  from `arc_beats` + outline target; no schema.
- **Keep, contrary to the brief's implicit reading: proportional scaling.** It is the cell
  rule's "single factor". What goes is scaling *up* and scaling when nothing forces it.
- **Not in the brief, worth noting:** `pacing_guidance` standard `beats_min=2` should
  become 3 in Phase 3 — 15/63 panels have two beats, which cannot hold action + reaction.
