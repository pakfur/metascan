# Refactor spec — visual story quality

**Supersedes** `docs/plans/compose-performance-plan.md`. That plan was written before we had
the compiled prompts, the beat dump, or the dialogue-density numbers. Phases 1, 2, 4, and 5
of it are deferred or absorbed here; Phase 0.5 is downgraded; Phase 0 is redirected. Read it
for context, not for sequencing.

**Goal of this refactor:** get one scene to compose into a structurally sound dialogue
sequence, verified at a render, without hand-editing. Not a template library — one template,
proven or disproven.

---

## What the investigation established

| Finding | Consequence |
|---|---|
| `render_retention_analysis` is panel-scoped and asserts every subject appears in every shot | H3 is told, falsely, that 3–4 subjects are present in shots where the beats cast 1–2. Prime suspect for crowded staging and identity drift across in-clip cuts. |
| Dialogue is 7% of beats (17/249); `STORY_BEATS_SYSTEM` never asks for it | The dominant defect. Characters with nothing to say get written as characters doing nothing — the literal "remains completely motionless" text in the compiled clips. |
| Cast size per beat averages 1.77–2.78, min 1 | The casting model works. The earlier `present_subjects` proposal is **cut**. |
| `is_cut` on 10/12 beats | Nearly every clip is a 3-shot internal edit executed by H3. Interacts directly with the retention bug. |
| Locations ("College", "Cabin") are roster subjects | Category error; a building gets cast as a performer. |
| No scene `function` field exists | Templates have no selection key. `arc_beats` is position, not function. |
| `replace_scene_panels` is a reusable sink with its own gate | A template loader can replace the shots+beats stages without new plumbing. |
| Timeline has no dialog time offset | J-cuts are **deferred**. Not free, not blocking. |
| Compiled prompts go stale against edited beats, with no indicator | Affects measurement. Always recompile before comparing. |

---

## Phase A — Compile correctness

No schema, no compose changes. Ship first; it is the cleanest available experiment.

**A1. Per-beat retention analysis.** Pass `beats` into `render_retention_analysis`; compute
each subject's shot list from `timeline.shots[k].beat_indices` → `beats[i]["subject_ids"]`.
Dialog-only speakers fall back to the beat whose dialog names them. `_panel_subjects` stays
as-is — it is the correct scope for `subject_definitions` and `<Picture N>` uploads. POV mode
keeps the whole-timeline list.

**A2. Stale-compile indicator.** `panels.video_prompt` can be older than its beats with
nothing surfacing it. Add a "beats changed since compile" flag analogous to the anchor chip —
compare `max(beats.updated_at)` for the panel against the compile timestamp. Needed for
correct measurement as much as for the UI.

**Observe:** recompile Weekend Getaway scene 0 with `force`, render, compare against a
freshly compiled pre-A1 baseline. Watch specifically whether subjects stop appearing in
shots that don't cast them.

---

## Phase B — Dialogue presence

The single highest-value change. No architecture.

**B1. Prompt.** Add a paragraph to `STORY_BEATS_SYSTEM`: when a panel's `subtext` involves
something a character wants, refuses, or is trying not to say, at least one beat must carry a
spoken line; in an exchange between two characters, lines alternate rather than accumulating
on one speaker. `subtext` is already in the beats user prompt context.

**B2. Lint** (`storyboard_story.py`, compose-time, scene-scoped — the existing `_lint_dialog`
in the compiler is a fidelity check and is unrelated):
- A scene with ≥2 character subjects and a verbal `function` must have dialogue on ≥40% of
  its beats.
- Every character subject present in ≥3 beats of such a scene must speak at least once.
- No speaker may hold more than 70% of a scene's lines.

Thresholds are starting points; tune once B1 lands and the distribution moves.

**B3.** Check whether the shots stage should carry any speech signal. It currently defers
framing and casting to beats and never mentions speech; if the panel-level `action` is
written as purely visual, the beats stage inherits that framing. One line may be enough.

**Observe:** recompose Weekend Getaway scene 0 beats and check dialogue density moves from
2/12 toward 5/12 with both characters speaking.

---

## Phase C — Roster hygiene

**C1.** `subjects.type` — `CHARACTER | LOCATION | PROP`. Only `CHARACTER` is castable into a
beat's `subject_ids`. Validator drops non-characters with a warning, as it already does for
unknown names. Existing rows: infer from the definition text where obvious, default
`CHARACTER`, and let the user correct in the UI.

**C2.** Age descriptors in subject definitions are currently free prose and a roster entry
already reads "Late teens female." Constrain the extraction stage to an adult floor unless
the premise explicitly requires otherwise, and make the field editable so existing rows can
be fixed. This is a correctness requirement, not a style one.

---

## Phase D — Scene function

New enum on `scenes`, emitted by the scenes stage, which already declares the dramatic work a
scene does.

```
SCENE_FUNCTION_VALUES = (
    "negotiation", "confession", "confrontation", "reveal",
    "arrival", "physical_action", "transit", "contemplation",
)
```

Column + `SCENES_GRAMMAR` + validator + one prompt line + `SceneCard.vue`. Nullable; existing
rows NULL. This is the template selection key and the B2 lint's "verbal function" test
(`negotiation | confession | confrontation` are verbal).

---

## Phase E — Template pilot (one template)

### Format

A template is scene-scoped and produces the panel breakdown, so it **replaces** the shots and
beats stages for that scene rather than slotting into them. Sections map to panels
(clip-sized, ≤ `shot_cap()`); slots map to beats.

```json
{
  "id": "two_party_negotiation_18",
  "function": "negotiation",
  "roles": [
    {"id": "A", "screen_side": "LEFT",  "note": "initiator; makes the ask"},
    {"id": "B", "screen_side": "RIGHT", "note": "responder; holds the condition"}
  ],
  "sections": [
    {
      "panel_index": 0, "duration_s": 13.0, "label": "The pitch",
      "slots": [
        {"beat_index": 0, "duration_s": 5.0, "kind": "establishing",
         "cast": ["A","B"], "dialog_slot": null, "is_cut": false,
         "camera": {"shot_size":"WS","angle":"EYE","lens":"wide",
                    "composition":"DEEP_STAGING","camera_motion":"STATIC"}},
        {"beat_index": 1, "duration_s": 3.0, "kind": "action",
         "cast": ["A"], "dialog_slot": "A", "is_cut": true,
         "camera": {"shot_size":"MS","angle":"EYE","lens":"normal",
                    "composition":"THIRDS_L","camera_motion":"STATIC"}},
        {"beat_index": 2, "duration_s": 5.0, "kind": "action",
         "cast": ["B"], "dialog_slot": "B", "is_cut": true,
         "camera": {"shot_size":"MS","angle":"EYE","lens":"normal",
                    "composition":"THIRDS_R","camera_motion":"STATIC"}}
      ]
    }
  ]
}
```

The template owns `duration_s`, `kind`, `is_cut`, `cast` (as roles), `dialog_slot`, and every
camera field. The model fills only `action`, `reveals`, `emotional_intent`, `sound`, and
`dialog[].text` — one slot at a time, with the slot's spec and the surrounding slots in
context. This is a much narrower generation than the current beats stage and should suit a
30B model considerably better.

### Pipeline

1. **Bind roles.** Map template roles to scene character subjects. Small dedicated call, or
   derive from `scenes.notes`. Fail loudly if the scene has fewer characters than the
   template has roles.
2. **Instantiate.** Expand sections into panel records and slots into beat records; resolve
   `cast` roles to `subject_ids`; leave prose fields empty.
3. **Fill.** Per panel, generate prose for its slots under a grammar restricted to the prose
   fields. `dialog_slot` is a hard requirement: a slot with `dialog_slot: "B"` must produce a
   line spoken by B.
4. **Conform.** Assert every camera field, duration, `is_cut`, `kind`, and cast matches the
   template exactly, and that every `dialog_slot` is filled. Unlike the existing lints this
   **hard-fails** — a conformance miss is a bug, not a style warning.
5. **Write** via `replace_scene_panels`, running `check_compose_gates` first so locked prompts
   and kept images survive.

`subject_ids` must be emitted per beat or `_panel_subjects` and `refplan` get an empty roster.

### The pilot template

`two_party_negotiation_18` — 18 slots, 5 sections, 63.5s, ASL 3.5s. Structurally derived from
`shotlist_dialogue_confession.md`, with one change: the release beat moves from B to A,
because in a negotiation the party who concedes is the one who made the ask.

| Section | Panel | Dur | Slots | Dramatic function |
|---|---|---|---|---|
| The pitch | 0 | 13.0 | WS master 5.0 / MS-A 3.0 / MS-B 5.0 | Ask, deflection held inside one shot |
| The complication | 1 | 12.5 | MCU-A 4.0 / MCU-B 3.0 / MCU-A 2.5 / OTS 3.0 | Pressure; reaction beats at 2 and 3 |
| The condition | 2 | 12.0 | CU-B 6.0 / CU-A 3.0 / CU-B 3.0 | **Turn.** First close-up in the scene |
| The concession | 3 | 14.5 | MCU-A 4.0 / CU-B 2.5 / 2-SHOT 5.0 / MS-B 3.0 | A resists, then concedes; shared frame |
| The button | 4 | 11.5 | MS-A 4.0 / INSERT 1.5 / MS-B 2.0 / CU-B 4.0 | Pattern break, dramatic irony, held reaction |

Full per-slot spec including composition, motion, and the reasoning behind each choice is in
`shotlist_dialogue_confession.md` §4; the durations and camera fields transfer unchanged.

Properties the current pipeline does not produce and this template guarantees: dialogue on 11
of 18 slots with alternating speakers; five reaction slots carrying no new information; a
1.5s–6.0s duration range against today's uniform 3–4s; no close-up before slot 8; and clip
seams falling on dramatic section boundaries rather than mid-exchange.

### Measurement

Weekend Getaway scene 0 — a two-person negotiation with existing failing output.

Baseline is a **fresh compile** of the current beats, not the stored `video_prompt` (panel
222 is stale). Render baseline and template versions of all five clips, and compare on:
dialogue beats and speaker balance; duration range within each panel; whether reaction slots
render as held expression rather than stillness; whether subject identity holds across
in-clip cuts (Phase A's target); and how many edits you make before the output is usable.

That last number is the decision. If it drops sharply, author the library. If it doesn't, the
template hypothesis is wrong and we look at the multi-shot-per-clip model instead.

---

## Deferred

- **J-cuts.** Needs `dialog[].start_offset_s`, `SpeakerLine.start_s`, and renderer work. Real
  value, not blocking, revisit after the pilot.
- **Template library.** After the pilot proves out.
- **Per-subject `performance` / `gaze`.** The compiled action text already carries body detail
  ("knuckles turn white"); the register problem is smaller than originally diagnosed.
- **Punch-up pass.** Same reason. Likely unnecessary.
- **Emitting `emotional_intent` at compile.** Still a real gap, still six lines, but its value
  dropped once we saw the action text. Ship it with Phase A if convenient.
- **Panel splitting, code-side duration derivation.** Absorbed — templates supply durations.
- **Edit capture.** Redirect to the compiled prompt rather than beat rows when it returns;
  that is where the user actually edits.

## Constraints

Unchanged from the previous brief: grammars stay in Python from shared enum constants; the
retry ladder and lint-never-hard-fails rule hold **except** for template conformance, which
hard-fails by design; the stage-subset API and skipped-panel continuity chain keep working;
`check_compose_gates`, `confirm`/purge, and `prompt_locked`/`prompt_source` are preserved;
`_synth_lock`, `parallel_slots`, and the WS event contract are untouched; every schema change
gets an idempotent migration; arithmetic stays code-side.

## Open decisions

1. **Does the template bypass the beats stage entirely, or fill through it?** Recommend
   bypass with a dedicated fill prompt — the existing beats prompt would fight the template on
   camera and duration.
2. **Role binding: dedicated call or derived?** Recommend a small dedicated call; it is the
   one place a wrong answer breaks the whole scene.
3. **What happens when a scene's function has no template?** Recommend falling through to the
   current shots+beats path, so the template layer is additive and reversible per scene.
4. **Does `is_cut` stay at 10/12?** The template keeps the current shape for the pilot so it
   isn't a confounding variable. If identity still drifts across in-clip cuts after Phase A,
   the one-beat-per-clip question becomes the next investigation.
5. **Where does the template live?** Recommend JSON on disk under `data/templates/`, loaded at
   startup with load-time enum validation that fails loudly — camera fields currently fall
   through `x if x in VALUES else None` and would silently null out on a vocabulary mismatch.
