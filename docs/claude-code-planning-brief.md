# Planning brief: performance and rhythm in the compose pipeline

**Your task this session is to produce a written implementation plan. Do not write
production code.** Read the codebase first, verify the claims below against what is
actually there, and tell me where I am wrong. A plan built on my description rather
than on the code is worse than no plan.

Write the plan to `docs/plans/compose-performance-plan.md`.

---

## Context to read first

- `docs/storyboard-compose-pipeline.md` — the current compose path, end to end
- `docs/scene-to-storyboard-prompt-chain.md` — an earlier design doc. Significant parts
  of it were never built. Treat it as intent, not as description.
- `metascan/core/storyboard_runner.py` — `compose_story` → `_compose_locked` → `_run_stage`
- `metascan/core/storyboard_story.py` — grammars, validators, lints, prompt builders
- `data/meta_prompt.yml` — `STORY_OUTLINE_SYSTEM`, `STORY_SCENES_SYSTEM`,
  `STORY_SHOTS_SYSTEM`, `STORY_BEATS_SYSTEM`
- `metascan/core/video_targets.py` — `shot_cap()`
- the H3 compile path (`compile_video`) — specifically which beat fields reach the prompt
- `coverage_cells_v1.json` (attached) — the cell library the plan will consume

## The problem, stated precisely

Compose output is structurally correct and dramatically inert. Scenes come back with
characters standing still, no visible reaction, no tension in expression, and too much
runtime spent before anything happens. Every scene requires hand-rewriting the beat
`action` text, the durations, and the camera fields before the H3 prompt is usable.

Once beats are hand-corrected the compiled video prompt is good. **The compile path is
not the problem. Composition is.** Do not spend plan effort downstream of `compile_video`.

Two root causes, both to be confirmed against the code:

**1. The prompt and lint layer specifies only prohibitions.** `STORY_BEATS_SYSTEM` asks
for "exactly one concrete visible event," camera-visible only, no emotion labels, no
internal narration. `lint_beats` rejects triple-repeated shot sizes, unmotivated motion,
empty `reveals`. Nothing anywhere states a positive target for the *register* of the
`action` text. "Maren enters the room" satisfies every constraint. The model settles at
the cheapest point inside the allowed region, and at 30B it cannot infer register from
prohibitions — it can only copy it from an example.

**2. There is no field describing a body.** `beats.subjects` is a list of roster names
resolved to `subject_ids`. So a two-hander reaches H3 as one event, a framing, and two
nouns. The listener — the character who actually carries a dramatic scene — is described
to the video model as an identifier. This is the schema hole the whole plan is aimed at.

A third, structural: `lint_beats` requires non-empty `reveals` on *every* beat. A reaction
shot is by definition low-information — nobody acts, nothing new is shown. The rule set is
currently incapable of producing one, so the model emits another plot beat instead. Every
lint in the system removes things; none requires anything.

---

## Workstreams, in priority order

Plan them as separable increments. Each should be shippable and observable on its own —
I evaluate by watching rendered output, not by reading JSON, so every phase needs to reach
a render.

### Phase 0 — Gold capture (do first; it feeds everything else)

Persist my hand-edits as before/after pairs. When a beat row is edited in the UI, snapshot
the pre-edit values alongside the post-edit ones. Storage can be a `beats_gold` table or
JSON on disk — pick based on what the existing schema makes cheap.

Also backfill: check whether edit history already exists anywhere (audit columns, undo
state, `prompt_source`/`prompt_locked` transitions) before building new capture.

**What the pairs are for**, in order of value — this drives what needs storing:

1. **Prioritization evidence.** The phase ordering below comes from reasoning over the two
   design docs, not from data. The edit log is data. If most edits add body-part nouns to
   `action`, Phase 2 is confirmed. If I consistently delete the first beat of every scene,
   Phase 6 moves up. If I rarely touch camera fields, Phase 5 drops in priority and the
   cell library becomes an exemplar source rather than an authority. Expect to re-rank the
   plan once a few dozen pairs exist.
2. **Regression measurement.** From Phase 2 onward, re-compose the same premises and
   compare fresh raw output against the earlier hand-fixed version. Partly scorable
   without a human: does the new raw text already contain the nouns I had to add? Did a
   reaction beat appear where I inserted one? Did setup shrink?
3. **Few-shot corpus** for the Phase 2 punch-up pass — but weight this lowest. My edited
   text is a floor, not a ceiling; I am not skilled at this and my corrections are better
   than raw output without being the target. Register exemplars should come from
   `coverage_cells_v1.json` and from an offline frontier-model-authored corpus. Use my
   pairs as diff signal, not as the style target.

**Capture context, not just the text pair.** A diff with no surroundings is close to
useless for (1) and (2). Store alongside each pair:

- the shot's `function` and `is_turn`
- the scene's arc position and index within the story
- the beat's `kind` and its position within the shot
- which fields changed, individually
- **whether I re-rendered and accepted the result** — without this flag, "the fix worked"
  and "I gave up on this one" are indistinguishable in the data

**Define the pair boundary.** Editing is iterative — tweak, render, tweak again. Capture
**first-raw against final-accepted**, not every intermediate save. Intermediates will
swamp the signal and make edit-volume metrics meaningless. Decide how to detect
"final" (idle timeout, explicit accept action, render-and-keep) and say which you chose
and why.

**This is a permanent instrument, not scaffolding.** Edit volume per composed scene,
broken down by field, is the only objective success measure available to someone who
evaluates by watching renders. It stays after every phase ships: if I am still rewriting
every `action` line after Phase 2, Phase 2 failed regardless of how good any individual
output looks.

Nothing else should ship before this is capturing.

### Phase 1 — Per-subject performance

Change `beats.subjects` from a name list to a list of objects:

```
{"name": ..., "performance": ..., "gaze": ...}
```

- `performance` — a description of a **change** in the body, per subject, including
  subjects who are not acting. Not a state. "She looks upset" renders as a still frame;
  "the smile goes out of her face" renders as motion.
- `gaze` — `LOOK_L | LOOK_R | TO_CAMERA | DOWN | UP | OFF_L | OFF_R | CLOSED`

Requires: DB migration, grammar change (`BEATS_GRAMMAR`), validator update (the existing
name→`subject_ids` mapping must survive and still drop unknown names without inventing
ids), prompt change, compile change so both fields reach the H3 prompt, and a UI change in
the beat editor.

**Verify before designing:** does `compile_video` currently emit `action`,
`emotional_intent`, and `reveals` into the H3 prompt, or only camera/dialog/timestamps?
The compose doc lists only the latter. If performance signal is already being computed and
dropped at the compile boundary, say so — it changes this phase from a migration to a
one-line fix and it changes the whole plan's ordering.

**Note:** `gaze` gives us eyeline data for free. Screen-direction and line-discipline
lints become derivable from it later. Do not build a separate axis/`screen_side` layer —
it is deferred, possibly permanently.

### Phase 2 — Punch-up pass and the register lint

A second VLM call after the beats stage whose only job is rewriting `action` and
`subjects[].performance` against register exemplars. Exemplars come from
`coverage_cells_v1.json` and (later) an offline frontier-model-authored corpus — **not**
from the Phase 0 pairs, for the reason given there. **Camera, duration, structure, and
`is_cut` are frozen inputs** — the pass may not change them. Narrow-scope rewrite is the
thing a 30B model does well, and I have direct evidence this specific intervention works
because it is the first thing I do by hand.

Pair it with a crude lint: reject any `action` or `performance` string containing no
body-part noun (`hand / hands / jaw / shoulders / eyes / breath / knuckles / spine /
throat / mouth / chin / fingers / chest / head / face`). Regex is fine. This fits the
existing lint-plus-targeted-re-roll ladder.

Consider whether the punch-up pass should reuse `generate_validated` or needs its own
retry shape, given it consumes valid input rather than producing a new structure.

### Phase 3 — Beat `kind`

Add `kind`: `action | reaction | establishing | insert`.

- Exempt `reaction` beats from the non-empty `reveals` requirement.
- Add the first *positive* lint in the system: any beat carrying dialog with subtext, and
  any beat immediately following the turn beat, must be followed by a `reaction` beat.
- Audit the `shot_size` distribution across existing composes. There is currently no rule
  requiring a tight frame at an emotional peak — only "the turn's tightest must not be
  beat 1," which is a negative constraint. If output skews WS/MS, faces are too small for
  H3 to render expression at all and Phases 1–2 will underdeliver. Report the actual
  distribution in the plan.

### Phase 4 — Durations computed, not generated

Stop asking the model for `duration_s`. Derive it: `kind` + dialog syllable count at
~2.5 words/sec + charge delta. Reaction 1–2s, dialogue = the length of the line,
post-turn hold 4–6s.

Then change `rescale_beat_durations`. Proportional rescaling to fit the panel budget
preserves ratios and destroys absolutes — the contrast between a 1.2s cut and a 4.0s hold
*is* the pacing, and scaling both by 0.83 flattens it. Replace with: derive
`panels.duration_s` from the beat sum clamped to `shot_cap()`, and **split the panel**
when the sum exceeds the cap rather than compressing it.

Splitting panels has consequences for `panel_videos`, the compose gates, and the
`panel_ids` filter. Work them out in the plan; if the blast radius is large, propose a
narrower first version.

### Phase 5 — Cell library

Load `coverage_cells_v1.json`. Selection key is a new `function` enum on the shots stage
(`dialogue_exchange | confrontation | reveal | arrival | reaction | physical_action |
transit | contemplation`) — one grammar field and one prompt line, emitted at the stage
that has the most dramatic context.

The cell is **authoritative for camera and beat count**. Copy `shot_size`, `angle`,
`lens_mm`, `composition`, `camera_motion`, amplitude, speed, `movement_motivation`,
`is_cut`, and `kind` straight into the beat rows. The model's remaining job is `action`,
per-subject `performance` and `gaze`, `reveals`, `emotional_intent`, `sound`, and `dialog`
— what happens dramatically and how bodies express it. It stops choosing things neither I
nor it can evaluate.

The `*_exemplar` fields are few-shot material for the beats prompt. They must not reach
the compiler.

Scale a cell to the shot's budget with a **single factor across all its beats**. Never
rescale beats independently.

Two conflicts to resolve in the plan:

- Cell enum values come from the older design doc's vocabulary. Map them to the real
  constants in `storyboard_story.py`. A mismatch is silently coerced to null by the beats
  validator, so add a load-time validation that fails loudly instead.
- `lint_beats` requires a scene-opening shot's *first beat* to be WS/EWS. Two cells
  (`reveal_kuleshov_4`, `reaction_ladder_3`) legitimately open tighter and are not
  scene-openers. Either constrain cell selection for scene-opening shots, or relax the
  lint to "the scene's opening shot must contain a WS/EWS beat." Recommend one.

### Phase 6 — Scene time allocation

`lint_scene_charges` requires the arc to be covered with no gaps, so `setup` is guaranteed
its own scene, and shots-per-scene comes from `pacing × scale` uniformly with no arc
awareness. Every scene gets roughly equal screen time regardless of dramatic weight —
which is why a 90-second story spends 15–20s on setup.

Two changes: allocate seconds per scene at the scenes stage weighted by arc position
(setup capped near 10–15% of target for short-form; turn and climax take the surplus),
and derive shots-per-scene from that allocation. Separately, add an outline directive to
open at the latest point the story still parses and deliver setup as embedded exposition
inside rising action — the arc grammar already permits subsets.

---

## Constraints the plan must respect

- Grammars are built in Python from the same enum constants the validators use. Keep that
  coupling; do not move grammars to YAML.
- The retry ladder stays: validator failure → one blind retry; lint violations → one
  targeted re-roll with violations appended verbatim; survivors become warnings. **Lints
  never hard-fail a stage.**
- The stage-subset API (`outline`/`scenes`/`shots`/`beats`, `scene_ids`, `panel_ids`) must
  keep working, including the continuity chain that pulls previous-beat framing across
  skipped panels.
- Destructive-recompose gates, the `confirm` purge semantics, and
  `prompt_locked`/`prompt_source` behavior must be preserved. Hand-edited prompts survive
  recompose today and must continue to.
- `_synth_lock`, the `parallel_slots` semaphore, and the WS event contract
  (`story_progress`, `story_stage_complete`, `story_complete`, `story_error`) are
  unchanged.
- Every schema change needs a migration and a story for existing rows.
- Arithmetic stays code-side. Never re-prompt to fix a number.
- Beats remain unscaled by `story_scale` — they are bounded by the clip cap, not by story
  length.

## What the plan document must contain

1. **Findings** — what you verified in the code, and specifically where my description
   above is wrong. Lead with this; I would rather be corrected than agreed with.
2. Per phase: files touched, schema/migration changes, grammar and lint changes, prompt
   changes, UI changes, and how the phase is observed end to end at a render.
3. The edit-volume metric: how it is computed from Phase 0 data, where it surfaces, and
   what the pass/fail threshold is for each subsequent phase.
4. Dependency order and what can run in parallel.
5. Rollback story per phase, particularly for the migrations in Phases 1 and 4.
6. Open questions and decision points, with a recommendation on each.
7. Explicit list of anything you think should be cut or resequenced, with reasoning.

Do not begin implementation until we have discussed the plan.
