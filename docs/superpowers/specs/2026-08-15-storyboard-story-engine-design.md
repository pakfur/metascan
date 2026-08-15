# Storyboard Story Engine (Phase V1) — Design

Date: 2026-08-15
Status: Draft — awaiting review
Series: Phase V1 of 4 (V2 reference describe, V3 H3 prompt compiler, V4 video
generation). Later phases depend on this one; this one depends on nothing new.

## 1. Goal

From a few sentences of premise (setting, subjects, desired action,
resolution), the local VLM builds a complete, editable storyboard for a 1–2
minute short: a story **outline**, a set of **scenes** that advance the story
from beginning to resolution, each scene decomposed into **shots** (the
existing `panels` entity) that advance the scene cinematically, and each shot
decomposed into 3–5 timed **beats** carrying action, camera direction
(H3-compatible vocabulary), sound events, and dialog. Everything lands in the
DB for review, editing, and per-level re-synthesis in the existing
StoryboardView UI.

This phase produces **no video and no image prompts** — it produces the
structured story tree. The existing still-image pipeline (brief → render →
ComfyUI) keeps working unchanged on the same `panels` rows.

### Non-goals

- Reference-image describing (V2), H3 prompt compilation (V3), video jobs (V4).
- Renaming `panels` → `shots` in code or schema. The `panels` entity *is* the
  shot; only UI labels may say "Shot".
- Multi-model story engines. V1 uses the active Qwen3-VL via the existing
  `VlmClient` singleton, same as `parse`/`synthesize` today.

## 2. Background constraints (from the codebase)

- `StoryboardRunner` (`metascan/core/storyboard_runner.py`) already implements
  the one-stage `parse()` (freeform → `{subjects, scenes[{panels}]}` via
  `PARSE_GRAMMAR`) and per-panel `synthesize()`. The story engine is a
  generalization of `parse()` into four staged calls.
- `VlmClient.generate_text(system_prompt, user_prompt, image_path=None,
  temperature, max_tokens, timeout, grammar=None)` posts to llama-server's
  `/v1/chat/completions` with a top-level GBNF `grammar` key
  (`metascan/core/vlm_client.py:493`).
- llama-server runs with a fixed `--ctx-size 32768` split across
  `spec.parallel_slots` (`vlm_client.py:218-251`) — 8192 tokens/slot for
  `qwen3vl-30b-a3b` (4 slots). Every stage call below must fit prompt +
  output in one slot's budget; the staging is designed so each call stays
  under ~3K prompt + ~2K output.
- GBNF gotcha (CLAUDE.md): `\-` is not a valid escape; invalid grammars
  SIGSEGV llama-server into a respawn loop. Hyphens go at character-class
  edges. All new grammars follow `storyboard_parse.py:PARSE_GRAMMAR`'s
  conventions and get the same unit-test treatment.
- Prompt text and grammars live in the hot-reloadable YAML prompt store
  (`data/meta_prompt.yml` via `get_prompt_store()`), following the
  `TAGGING_*` precedent in `vlm_prompts.py`.

## 3. Data model

### 3.1 New table: `beats`

```sql
CREATE TABLE IF NOT EXISTS beats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    panel_id    INTEGER NOT NULL REFERENCES panels(id) ON DELETE CASCADE,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    duration_s  REAL NOT NULL DEFAULT 4.0,
    action      TEXT NOT NULL,
    camera_motion    TEXT,      -- H3 vocabulary, validated in code
    camera_amplitude TEXT,      -- 'small' | 'large' | NULL (medium/omitted)
    camera_speed     TEXT,      -- 'slow' | 'fast' | NULL (normal/omitted)
    is_cut      INTEGER NOT NULL DEFAULT 0,  -- beat starts an internal [Shot N] cut
    dialog      TEXT NOT NULL DEFAULT '[]',  -- JSON array, see below
    sound       TEXT,                        -- diegetic sound events, free text
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_beats_panel ON beats(panel_id);
```

`dialog` is a JSON array of `{subject_id: int|null, voice: str|null,
delivery: str|null, language: str, text: str}` — `subject_id` when a board
subject speaks, else `voice` describes an unnamed speaker. Speaker IDs
(`(S1)`, `(S2)`) are **not stored**; V3's compiler assigns them
deterministically in playback order at compile time (per the H3 guide rule).

Camera vocabulary constants (`CAMERA_MOTIONS`, from the H3 base guide §4.3:
`zoom_in, zoom_out, push_in, pull_out, pan_left, pan_right, truck_left,
truck_right, tilt_up, tilt_down, pedestal_up, pedestal_down, arc, tracking,
static, shake_slight, shake_strong, pov, roll_cw, roll_ccw`) live in a new
pure module `metascan/core/storyboard_story.py` beside the stage grammars.
NULL `camera_motion` means "unspecified — let the video model decide".

### 3.2 Extended columns

Via `_idempotent_add_column` (existing pattern, `database_sqlite.py`):

- `storyboards.outline TEXT` — the generated story outline (structured JSON
  text, editable; see §4.1).
- `panels.duration_s REAL NOT NULL DEFAULT 12.0` — target clip length for the
  shot; beats within a panel should sum to roughly this. 15 s is the H3
  per-generation ceiling; the UI warns above it but does not block.
- `storyboard_subjects.voice TEXT` — voice description used to establish
  speaker identity ("clear youthful female voice, light American accent").
  Nullable; only needed for subjects that speak.

`storyboards.source_text` (already present) is the premise; no new column.

### 3.3 DB methods (sync, in `DatabaseManager`)

`create_beat`, `get_beat`, `list_beats(panel_id)`, `update_beat`,
`delete_beat`, `replace_panel_beats(panel_id, beats)` (transactional
delete-and-insert used by the beat-synthesis stage), plus `beats` arrays
embedded in `get_storyboard_tree` output (each panel dict gains
`"beats": [...]` ordered by `sort_order`). `_release_panels` needs no change
— beats have no media references, cascade delete is safe.

## 4. Synthesis pipeline

Four staged calls, all through `VlmClient.generate_text` with per-stage GBNF
grammars, orchestrated by new `StoryboardRunner` methods. Each stage is
independently runnable and re-runnable; each writes to the DB and emits WS
progress. All stages run under the existing `_synth_lock` (they share the
VLM with `synthesize()` and must not interleave with `generate()`'s VLM
unload).

### 4.1 Stage 1 — Outline (`compose_outline`)

Input: `storyboards.source_text` (the premise) + subject roster if subjects
already exist. Output: JSON written to `storyboards.outline`:

```jsonc
{
  "logline": "…",
  "subjects": [{"name": "…", "description": "…", "voice": "…"}],
  "arc": [{"beat": "setup|rising|turn|climax|resolution", "summary": "…"}],
  "tone": "…", "duration_target_s": 90
}
```

One `generate_text` call, grammar-constrained, temperature 0.7,
max_tokens 1500. Subjects named in the outline that don't exist yet are
created as `storyboard_subjects` rows (name/description/voice); existing
subjects (matched case-insensitively by name) are left untouched — the user's
hand-edited descriptions win.

The outline is the **user's primary editing checkpoint**: the UI presents it
for review before the cascade continues (see §6). Re-running stage 1
overwrites `outline` only after the same confirm gate as `parse()`
(`ConfirmRequiredError` when an outline already exists and `confirm=False`).

### 4.2 Stage 2 — Scenes (`compose_scenes`)

Input: outline. Output: `scenes` rows (name, subtitle, setting, location,
time_of_day, mood, lighting, notes, sort_order) — one call, grammar
constrained to a scene-array JSON, 2–6 scenes for a 60–120 s target.
Destructive over existing scenes → same confirm gate; reuses the
`_release_panels`-aware delete path from `replace_storyboard_structure` so
any already-generated panel images are unhidden, never orphaned.

### 4.3 Stage 3 — Shots (`compose_shots`, per scene)

Input: outline + one scene row + neighbor-scene one-liners (for continuity).
Output: `panels` rows for that scene — `action`, `shot_size`, `angle`,
`lens`, `subject_ids`, `duration_s`, `sort_order`. One call **per scene**
(parallel across scenes under the `parallel_slots` semaphore, mirroring
`_synthesize_locked`). 1–4 shots per scene. Grammar reuses
`SHOT_SIZE_VALUES` / `ANGLE_VALUES` / `LENS_VALUES` from
`storyboard_parse.py`. Subject references resolve by name against the
roster; unknown names are dropped with a warning in the stage result rather
than failing the stage.

### 4.4 Stage 4 — Beats (`compose_beats`, per panel)

Input: outline one-liner + scene summary + the panel row + its subjects
(descriptions verbatim) + `duration_s`. Output: 3–5 `beats` rows via
`replace_panel_beats`. One call per panel, parallel under the semaphore.
The grammar constrains `camera_motion`/`amplitude`/`speed` to the enum
values and `duration_s` to a number; the runner post-validates that beat
durations sum to `panel.duration_s` ± 25% and rescales proportionally in
code when they don't (never re-prompts for arithmetic).

### 4.5 Orchestration API on the runner

```python
async def compose_story(self, storyboard_id: int, *,
                        stages: Sequence[str] = ("outline", "scenes", "shots", "beats"),
                        scene_ids: Optional[List[int]] = None,
                        panel_ids: Optional[List[int]] = None,
                        confirm: bool = False) -> Dict[str, Any]
```

- `stages` is any ordered subset of the four stage names, run in canonical
  order. Typical uses: the full cascade (default), or a single stage for
  re-rolls (`stages=("beats",), panel_ids=[…]` re-beats one shot;
  `stages=("shots",), scene_ids=[…]` re-shots one scene).
- Destructive-stage confirm rules: `outline` and `scenes` require
  `confirm=True` when overwriting; `shots` requires it when the target scenes
  already have panels; `beats` never does (beats carry no downstream media).
- Emits on the `storyboard` WS channel:
  `story_progress {storyboard_id, stage, done, total}`,
  `story_stage_complete {storyboard_id, stage}`,
  `story_complete {storyboard_id, counts}`,
  `story_error {storyboard_id, stage, error}` — mirroring the
  `synthesis_*` contract (exactly one of complete/error per run, error
  re-raised for direct callers).
- No-VLM behavior: unlike `synthesize()`, there is no deterministic fallback
  that makes sense for story invention. `compose_story` raises
  `StoryboardError("no VLM active")` → HTTP 503, matching `parse()`.

### 4.6 Prompts and grammars

New YAML keys in `data/meta_prompt.yml` (hot-reloadable):
`STORY_OUTLINE_SYSTEM`, `STORY_OUTLINE_GRAMMAR`, `STORY_SCENES_SYSTEM`,
`STORY_SCENES_GRAMMAR`, `STORY_SHOTS_SYSTEM`, `STORY_SHOTS_GRAMMAR`,
`STORY_BEATS_SYSTEM`, `STORY_BEATS_GRAMMAR`, plus user-prompt templates.
Python-side accessors in `metascan/core/storyboard_story.py` follow the
`vlm_prompts.__getattr__` pattern. System prompts carry 1–2 few-shot
exemplars each (compact, budget-conscious) and the uncensored directive
already used by `TAGGING_SYSTEM_PROMPT` so NSFW premises are not refused.
Each stage prompt instructs cinematic storytelling craft: scenes advance the
arc visually, shots follow coverage grammar (establish → medium → close),
beats escalate within the shot.

## 5. Backend API

New routes in `backend/api/storyboard.py` (service methods in
`StoryboardService`, DB via `asyncio.to_thread`, per project rules):

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/storyboard/{id}/compose` (202) | `{stages?, scene_ids?, panel_ids?, confirm?}` | `{"status":"started"}` — fire-and-forget like `/synthesize` |
| POST | `/api/storyboard/panels/{panel_id}/beats` | `BeatCreate` | `{"id": int}` |
| PATCH | `/api/storyboard/beats/{beat_id}` | `BeatPatch` | updated beat dict |
| DELETE | `/api/storyboard/beats/{beat_id}` | — | `{"status":"deleted"}` |

- `PATCH /api/storyboard/{id}` gains `outline` as a patchable nullable field
  (the outline editor saves through it).
- `PATCH /api/storyboard/panels/{panel_id}` gains `duration_s`
  (NOT-nullable set extended).
- `PATCH /api/storyboard/subjects/{subject_id}` gains `voice` (nullable).
- Beat routes follow the panel routes' shape: `exclude_unset`,
  `_reject_null_for_required` with `_BEAT_NOT_NULLABLE = {"sort_order",
  "duration_s", "action", "is_cut", "dialog"}`, 404 on unknown ids,
  `ParentNotFoundError` → 404 on create.
- 409 `{code:"confirm_required"}` from `ConfirmRequiredError`, matching
  `parse`.

## 6. Frontend

- **Types** (`src/types/storyboard.ts`): `Beat` interface, `Panel` gains
  `duration_s` and `beats: Beat[]`, `Subject` gains `voice`,
  `StoryboardTree` gains `outline`. `CAMERA_MOTIONS` / amplitude / speed
  constants mirrored for the selects.
- **Store** (`src/stores/storyboard.ts`): `story` progress state alongside
  `synthesis` (running/stage/done/total/error), fed by the `story_*` WS
  events under the same `storyboard_id === tree.id` guard; `composeStory()`,
  beat CRUD actions (`addBeat`, `patchBeatFields`, `removeBeat`) following
  the optimistic-patch precedent; `refresh()` untouched (beats ride the tree
  payload).
- **StoryboardView**: a "Compose story" action beside "Import text". Flow:
  premise text (reuses `ImportTextDialog`'s textarea or `source_text`) →
  stage 1 → an **OutlineDialog** presents the outline for editing → user
  confirms → stages 2–4 cascade. A per-stage picker in the dialog also
  drives partial re-runs.
- **PanelDetail**: a beats section under the existing fields — ordered beat
  rows (duration, action, camera selects, cut toggle, sound, dialog lines
  editor), each using the local-ref + snapshot resync pattern keyed on
  `[beat.id, beat.updated_at]` (`PanelDetail.vue:169-225` precedent);
  add/remove/reorder; a "Re-beat" button calling
  `composeStory({stages:['beats'], panel_ids:[id]})`. Duration-sum warning
  chip when beats exceed `duration_s` or 15 s.
- Confirm gates surface through the existing `DeleteImagesDialog`-style
  confirm modal (text-only variant — no image purge is involved in V1
  except scene replacement, which reuses the existing keep/unhide flow).

## 7. Error handling

- Stage-call failures (`VlmError`, JSON validation): the stage aborts, prior
  completed stages' writes stand, `story_error` names the stage. Partial
  scene/shot writes within a failed stage are rolled back (each stage's DB
  write is one transaction: `replace_*` helpers).
- Grammar-valid but semantically bad output (empty arrays, zero scenes):
  validators raise `ParseError`-style errors with the raw text logged at
  DEBUG, surfaced as `story_error`.
- Beat duration arithmetic is always code-side (§4.4) — no LLM math.

## 8. Testing

Following the no-real-model rule (`tests/` never load CLIP/VLM/Comfy):

- `tests/test_storyboard_story.py`: grammar round-trips (every grammar
  compiled by a pure-Python GBNF sanity checker at minimum: balanced rules,
  no `\-` escapes), validator behavior on malformed/edge outputs, duration
  rescaling, camera-enum validation, subject name-matching rules.
- `tests/test_storyboard_beats_db.py`: beat CRUD + `replace_panel_beats`
  transactionality + cascade delete + tree embedding, against a temp DB.
- `tests/test_storyboard_compose_api.py`: route contracts (202, confirm 409,
  404s, not-nullable 400s) with a stubbed runner, `TestClient`, mirroring
  `tests/test_folders_api.py`.
- Runner staging tests with a fake VLM object (canned `generate_text`
  returns), mirroring the existing `synthesize` tests' approach.

## 9. Open questions (resolved defaults)

- **Panels stay "panels".** UI may label the section "Shots"; no rename.
- **Outline stored as TEXT JSON**, not normalized tables — it's a document
  the user edits as a whole; scenes/panels/beats are the normalized truth
  after stage 2+.
- **Beat dialog as JSON column**, not a table — dialog lines are only ever
  read/written with their beat; no cross-beat queries exist.
