# Compose Story: Cinematic Polish — Design

**Date:** 2026-08-20
**Status:** Approved design, pre-implementation
**Source material:** `docs/scene-to-storyboard-prompt-chain.md` (external advice doc:
"Seed → Cinematic Scene → Storyboard Panels", a four-stage prompt chain for a
local ~30B model). This spec adapts its ideas onto metascan's existing
`outline → scenes → shots → beats` compose pipeline rather than replacing it.

## 1. Problem

Composed storyboards read as a list of pictures, not a film. Three symptoms
(user-confirmed, in priority order):

1. **No dramatic build.** The outline's arc (`setup`/`rising`/`turn`/`climax`/
   `resolution`) dies at the outline — the shots and beats stages see only a
   logline and a scene mood, so nothing downstream knows where the story turns.
   Framing choices are decorative rather than motivated.
2. **Flat, samey framing.** Beats repeat shot sizes; close-ups land on
   exposition as often as on the climax; camera moves are unmotivated. No
   sequence-level rules exist — validators only coerce enum values.
3. **Continuity drift.** `build_beats_user_prompt` drops the scene's setting,
   lighting, and time_of_day, the story tone, and the previous panel's
   framing, so beat-to-beat visual continuity is a context-window bet.

What the pipeline already does right (and this spec keeps): grammar-constrained
decoding at every stage, subject roster reused verbatim, style block
concatenated after synthesis, duration math in code
(`rescale_beat_durations`), small per-scene/per-panel calls.

## 2. Approach

**Enrich the four existing stages in place** (approach "A"). No new pipeline
stage, no new tables, no stage-picker or compose-gate changes. The advice
doc's machinery threads through the existing stages:

- The dramatic spine (value charges, one turn, subtext) lives on **scenes**
  and **panels**, authored by the scenes and shots stages.
- The visual grammar (composition, light quality, emotional intent, reveals,
  movement motivation) lives on **beats**.
- A pacing model replaces the hardcoded "1–4 shots of 5–15s".
- Full bible re-injection into downstream calls fixes continuity drift.
- Pure lint functions + one targeted re-roll per stage call enforce the
  cinematic rules; style violations never hard-fail a compose.

### Non-goals (explicit deferrals)

- **Axis / eyeline discipline** (advice doc rules 1–2: `screen_side`,
  `screen_dir`, eyeline reciprocity). Real value concentrated in two-person
  dialog coverage; needs subject-level and beat-level fields plus its own
  validator family. Layers on cleanly later.
- **`frenetic` pacing.** The doc treats it as "board key frames only"; with
  H3's ~15s clip cap it has no sensible mapping to metascan's generation
  units. Three pacing values only.
- **`h3_compiler` changes.** The video body stays deterministic from
  action/camera/dialog/sound. Feeding `emotional_intent` into the H3 body is
  a possible follow-up, not part of this.
- **Persisting compose warnings.** Lint leftovers go to the log + a WS
  payload field (§6). Stale per-panel warnings about since-edited beats are
  worse than none; recompose regenerates them.
- **Editable charge UI.** Charges display read-only in v1 (§7); hand-editing
  one scene's charge breaks the chain invariant. The steering lever is the
  outline arc + recompose.

## 3. Data model

All new columns are added via the existing `_idempotent_add_column` pattern —
nullable or defaulted, so **no `user_version` bump** is needed.

### `storyboards`

| column | type | notes |
|---|---|---|
| `pacing` | `TEXT NOT NULL DEFAULT 'standard'` | `contemplative \| standard \| propulsive`. Proposed by the outline stage, user-overridable via PATCH. |

### `scenes`

| column | type | notes |
|---|---|---|
| `arc_beats` | `TEXT NOT NULL DEFAULT '[]'` | JSON list of outline arc stage names this scene covers, e.g. `["rising","turn"]`. Every arc entry lands in exactly one scene, in story order. |
| `charge_in` | `INTEGER` (nullable) | Protagonist's-POV value charge, −5..+5, at scene open. |
| `charge_out` | `INTEGER` (nullable) | Charge at scene close. `charge_out` of scene N = `charge_in` of scene N+1 (validated). |

### `panels`

| column | type | notes |
|---|---|---|
| `is_turn` | `INTEGER NOT NULL DEFAULT 0` | Exactly one panel in the story, inside the scene whose `arc_beats` contains `"turn"`. Joins the panel `_reject_null_for_required` list. |
| `subtext` | `TEXT` (nullable) | What the shot means but doesn't show; must differ from `action`. |

### `beats`

| column | type | notes |
|---|---|---|
| `composition` | `TEXT` (nullable) | Enum: `thirds_left \| thirds_right \| centered \| symmetrical \| negative_space \| frame_in_frame \| leading_lines \| deep_staging`. |
| `light_quality` | `TEXT` (nullable) | Enum: `hard \| soft \| dappled \| practical \| window \| firelight \| ambient`. Per-beat light *direction* is deliberately omitted — scene-level `lighting` free text carries it and is re-injected (§5). |
| `emotional_intent` | `TEXT` (nullable) | Free text in the physical-evidence register: "jaw set, eyes on the floor", never "she is sad". |
| `reveals` | `TEXT` (nullable) | What this beat shows that the previous one didn't. Consumed by the lint, not the image brief. |
| `movement_motivation` | `TEXT` (nullable) | Required non-empty (by lint) whenever `camera_motion` is set and isn't `static`. |

### Plumbing

Every new field flows through: the `replace_storyboard_scenes` /
`replace_scene_panels` / `replace_panel_beats` writers,
`get_storyboard_tree`, the PATCH routes (`exclude_unset` semantics
unchanged; nullable fields clearable with explicit `null`), and the
TypeScript types. New enum constants live beside the existing ones in
`storyboard_story.py` (`COMPOSITION_VALUES`, `LIGHT_QUALITY_VALUES`,
`PACING_VALUES`) and are mirrored in `frontend/src/types/storyboard.ts`.

### `compose_brief`

Gains lines for `composition`, `light_quality`, and `emotional_intent`
(rendered as natural phrases, mirroring the existing `SHOT_SIZES`/`ANGLES`/
`LENSES` maps). `reveals`, `subtext`, and charges stay **out** of the image
brief — they are sequence/authoring metadata, not frame content.

## 4. Pacing model

`pacing_guidance(pacing, shot_cap)` in `storyboard_story.py` — pure, returns
the numbers injected into the shots and beats prompts. In metascan, **beats
are the film-shot unit** (each beat is one H3 `[Shot n]`), so the advice
doc's average-shot-length math governs *beat* durations; panels are
generation containers capped by `video_targets.shot_cap`.

| pacing | panel duration | beats per panel | beat ASL guidance |
|---|---|---|---|
| `contemplative` | 10–15s | 1–3 | ~6–8s |
| `standard` | 8–15s | 2–4 | ~4–5s |
| `propulsive` | 6–12s | 3–5 | ~2–3s |

Upper panel-duration bounds clamp to `shot_cap`. Counts are **prompt
instructions** ("produce exactly 2 to 4 shots"), enforced softly by the lint
pass — the grammar's repetition bounds stay at their current maxima.

## 5. Stage changes — grammars & system prompts

Grammars remain code-built in `storyboard_story.py` from enum constants;
system prompts remain YAML-backed in `data/meta_prompt.yml` (hot-reloaded).

### Outline

- `OUTLINE_GRAMMAR`: add `"pacing"` enum field.
- `STORY_OUTLINE_SYSTEM`: one added instruction — choose the pacing that
  serves the premise's dominant register (grief/landscape → contemplative,
  chase/panic → propulsive).
- Validator falls back to `standard` on a missing/odd value. The runner
  writes it to `storyboards.pacing`.

### Scenes — the dramatic spine

- `SCENES_GRAMMAR`: add `arc_beats` (array of arc-stage enum values),
  `charge_in`, `charge_out` (`charge ::= "-"? [0-9]`).
- `STORY_SCENES_SYSTEM` rewritten to carry the doc's Stage-1 rules at scene
  granularity:
  - every outline arc entry lands in exactly one scene's `arc_beats`, in
    story order, no gaps;
  - `charge_out` of scene N equals `charge_in` of scene N+1;
  - the scene covering `turn` swings hardest (largest
    `|charge_out − charge_in|`);
  - the story must not end on the polarity it opened on;
  - a beat where the value doesn't change is filler — cut it.
- The whole chain is authored in one response, so `lint_scene_charges`
  validates it structurally with no cross-call threading. This stage is the
  primary customer of the targeted re-roll (§6): a broken chain here poisons
  everything downstream.

### Shots

- `SHOTS_GRAMMAR`: add `is_turn` (boolean) and `subtext` (nullable string).
- `build_shots_user_prompt` gains:
  - the scene's `charge_in → charge_out` and `arc_beats`;
  - pacing-derived explicit counts ("produce exactly 2 to 4 shots, 8–15
    seconds each"), replacing the hardcoded range;
  - **turn directive, conditional per call:** only the scene whose
    `arc_beats` contains `turn` is told "exactly one shot is the turn — mark
    it `is_turn: true`". All other scenes' prompts instruct `false`
    everywhere. The validator zeroes strays, so the parallel per-scene calls
    cannot produce two turns.
- `STORY_SHOTS_SYSTEM`: `subtext` required per shot — what the shot means
  but doesn't show, must differ from the action.

### Beats — full bible re-injection

- `BEATS_GRAMMAR`: add `composition` and `light_quality` (grammar enums),
  `emotional_intent` and `reveals` (strings), `movement_motivation`
  (nullable string).
- `build_beats_user_prompt` rebuilt to carry everything it currently drops:
  - story tone + logline;
  - scene `setting`, `lighting`, `time_of_day`, `mood`;
  - the panel's `subtext` and `is_turn` flag;
  - pacing-derived beat count and ASL guidance;
  - **the previous panel's action and its last beat's framing summary**
    ("previous beat ended on: MCU, low angle, soft light, centered") — the
    advice doc's "PRECEDING SHOT" slot.
- `STORY_BEATS_SYSTEM` rewritten to add the cinematic rulebook:
  - never the same `shot_size` three beats running;
  - the first beat of a scene's first panel establishes wide (WS/EWS)
    unless the scene deliberately withholds geography;
  - any non-static `camera_motion` needs a `movement_motivation` naming
    what in the subject's behavior or emotional state pulls the camera —
    "for drama" is not a reason;
  - each beat's `reveals` must name information the previous beat didn't
    show; a beat that reveals nothing is a duplicate;
  - in a turn-flagged panel, the tightest `shot_size` lands on the beat
    where the turn hits — push in on the turn, not on exposition;
  - `emotional_intent` is visible physical evidence only, never an emotion
    label (diffusion models render labels as stock expressions).

### Beats sequencing change

Within a scene, panels' beats compose **sequentially** (each panel's call
waits on the previous panel's beats); different scenes still run in
parallel under the existing semaphore. This is what supplies the
previous-panel framing context and lets the lint check shot-size repetition
across panel boundaries. Cost is modest: concurrency is already capped at
`parallel_slots`, and 2–6 scenes keep most of the fan-out. On a partial
recompose (explicit `panel_ids`), the previous panel's beats are read from
the tree when they already exist, so the chain works there too.

## 6. Validators & targeted re-roll

### Two classes of violation

- **Mechanical** — fixed silently in code, as today: stray `is_turn`
  outside the turn scene zeroed, `camera_amplitude`/`camera_speed` without a
  motion nulled, enum misses nulled.
- **Creative** — the model must choose differently; handled by lint +
  re-roll.

### Lint functions

Pure, in `storyboard_story.py`, each returning `List[str]` of violations
phrased as instructions (small models correct well against specific
complaints, badly against "try again"):

- `lint_scene_charges(scenes, arc)` — arc coverage complete/ordered/
  gap-free; charge chain continuous; turn scene swings hardest; closing
  polarity differs from opening. Example message: `"scene 3 charge_in=2 but
  scene 2 charge_out=-1 — the chain must be continuous"`.
- `lint_shots(panels, scene_is_turn)` — exactly one `is_turn` when the
  scene owns the turn (zero otherwise); `subtext` non-empty and not a
  restatement of `action`.
- `lint_beats(beats, panel, prev_last_beat)` — no `shot_size` three-in-a-row
  (including across the panel boundary via `prev_last_beat`); first beat of
  a scene's opening panel is WS/EWS; non-static motion has non-empty
  `movement_motivation`; `reveals` non-empty per beat; in a turn panel the
  tightest `shot_size` is not beat 1 (the code-checkable degradation of
  "the close-up sits on the turn moment").

### `generate_validated` grows a lint pass

Current flow: generate → structural validate → retry once on `StoryError`.
New flow appends: → lint → if violations, **one** re-roll with the
violations appended to the user turn:

```
Your previous response violated these rules — regenerate the full JSON,
fixing each:
- beats 2, 3, 4 all use "MCU"; vary the framing
- beat 3 has camera_motion "push_in" but empty movement_motivation
```

→ re-validate + re-lint → **accept whatever survives, carrying remaining
violations as warnings.** Style rules never hard-fail a compose. Worst-case
call budget per unit is 3 (generate + structural retry + lint re-roll); in
practice the structural retry only fires on token-limit truncation.

### Warnings surface

Remaining lint violations are logged (existing beats-warnings path) and
returned in a new `warnings` array on the `story_stage_complete` WS payload.
Not persisted (§2 non-goals).

## 7. Frontend

- **Types** (`frontend/src/types/storyboard.ts`): `Storyboard.pacing`;
  `Scene.arc_beats/charge_in/charge_out`; `Panel.is_turn/subtext`; `Beat`'s
  five new fields. `COMPOSITION_VALUES` / `LIGHT_QUALITY_VALUES` /
  `PACING_VALUES` constant arrays mirror the Python enums.
- **`StoryboardSettingsDialog`**: `pacing` as a three-option select with
  one-line descriptions ("Contemplative — long held shots, ~6–8s per
  beat").
- **`PanelDetail.vue`** (shot pane): `subtext` one-line text field;
  `is_turn` as a toggleable ★ "Turn" badge in the shot header. Both follow
  the mandatory local-ref + snapshot resync pattern (commit-on-change,
  resync on `[id, updated_at]`) since compose rewrites them server-side.
- **`BeatForm.vue`**: `composition` and `light_quality` selects alongside
  the existing shot_size/angle/lens row. `emotional_intent`, `reveals`,
  `movement_motivation` in a collapsed **"Cinematography"** disclosure
  section below the framing row — compose-authored metadata most editing
  sessions won't touch; every field still hand-editable. Same resync
  pattern.
- **Outline rail**: read-only — a small charge chip per scene row
  (`−1 → −4`, colored by direction) and a ★ marker on the turn shot's row.
  No charge inputs in v1 (§2 non-goals); backend PATCH accepts them for
  completeness.
- **Compose warnings**: the storyboard store's `story_stage_complete`
  handler reads `warnings` and raises one toast — "Beats composed with 2
  style warnings (see server log)". No new UI surface.

## 8. Testing

- **Pacing math**: `pacing_guidance` unit tests — table values, `shot_cap`
  clamping.
- **Grammar/validator round-trips**: extend the existing
  `storyboard_story` validator tests for the new fields (valid enums kept,
  invalid nulled, signed charges parsed, `arc_beats` filtered to known
  stages).
- **Lints**: direct unit tests with hand-built dicts — one passing fixture
  and one fixture per rule per lint (broken chain, two turns, triple MCU,
  unmotivated push-in, non-wide opener, empty reveals, tightest-on-beat-1).
- **Re-roll loop**: fake-VLM test (existing fake-worker precedent): first
  response violates a lint, assert the second call's user prompt contains
  the violation text; second bad response → accepted with warnings, not
  raised.
- **Sequencing**: beats stage test asserting panels within a scene compose
  in order and the second call's prompt contains the first panel's
  last-beat framing summary.
- **DB round-trip**: new columns survive `replace_*` writers →
  `get_storyboard_tree` → PATCH (including explicit-null clears).
- Frontend changes ride `vue-tsc --noEmit` + `npm run build` as usual.

## 9. Files touched (implementation map)

| area | files |
|---|---|
| grammars, prompts builders, lints, pacing | `metascan/core/storyboard_story.py` |
| system prompts | `data/meta_prompt.yml` |
| sequencing, re-roll, pacing wiring, WS warnings | `metascan/core/storyboard_runner.py` |
| columns, writers, tree, PATCH-required lists | `metascan/core/database_sqlite.py`, `backend/api/storyboard.py` |
| brief lines | `metascan/core/storyboard_brief.py` |
| UI | `frontend/src/types/storyboard.ts`, `BeatForm.vue`, `PanelDetail.vue`, `StoryboardSettingsDialog.vue`, outline rail component, `stores/storyboard.ts` |
| tests | `tests/test_storyboard_story*.py` (extend), new lint/re-roll/sequencing tests |
