# Image-to-Video découpage templates — design

**Date:** 2026-09-22
**Status:** approved in discussion; implementation plan to follow
**Builds on:** `2026-09-18-i2v-minimax-design.md` (the i2v flow), the
storyboard shot-list templates (`metascan/core/shot_templates.py`,
`data/templates/`), and the vendored MiniMax H3 guides
(`data/prompt_guides/minimax-h3/`).

## 1. Problem

The i2v compiler renders every clip as one continuous `[Shot 1]`. A 15 s
clip is five beats in one framing, a 20 s clip six. Beyond roughly ten
seconds that reads as a locked-off surveillance take: the action is
cramped inside the framing the source image fixed, and identity drifts
over a long continuous take.

The H3 format is built for the alternative. Its base guide §4.2 gives the
découpage form — `[Shot 2] At 00:03.500, the camera cuts to a close-up of
…` — and the rule for when to use it: "A cut should introduce new
information about the subject, space, state, viewpoint, or time. If only
the distance or a slight angle needs to change, prefer camera motion."
The storyboard side already compiles multi-shot prompts this way
(`h3_compiler.compute_timeline`). Only the i2v path stops at one shot.

## 2. Goal

Let the user pick a **cadence** for an i2v clip from a library of
structured **découpage templates**. A template owns the shot structure —
timing, shot size, angle, camera motion, how each shot begins, who is on
screen, who speaks. The VLM fills in only the prose. Code assembles the
H3 prompt and lints it against the template.

"Single take" — today's behaviour — stays the default and is unchanged.

## 3. Decisions taken in discussion

| Question | Decision |
|---|---|
| Template vs. clip duration | **A template fixes the duration.** Choosing one sets the clip length and locks the selector. No proportional rescaling. |
| How templates name people | **Role slots bound from the image.** A template declares roles; before writing prose the VLM binds each to a person it sees in the source image and describes them once. |
| Image has fewer people than roles | **The VLM invents the extra**, described like a bound role, and the compiler keeps it out of beat 1 (anchored to the picture). It may enter from its next beat. |
| Authoring | **JSON files** under `data/i2v_templates/`, validated at load; the dialog only picks. No in-app editor in this phase. |
| Edits overwrite? | Not applicable here, but note: `template_id` joins the clip's editable `form_state` (see `i2v_form.py`) so a clip remembers its cadence. |

Dropped from the illustrative examples, on purpose:

- **Aspect ratio** (`16:9`, `21:9`, `4:3`): i2v output always follows the
  source image's aspect (the image is the first frame). Templates carry
  none.
- **`[CONSTRAINTS]`** ("no extra fingers", "no watermarks"): H3 has no
  negative-prompt channel. Rendering them would be pretending.

## 4. Template format

One file per template, `data/i2v_templates/<id>.json`. Loaded lazily and
cached by a new pure module `metascan/core/i2v_templates.py`, with
`reload_i2v_templates()` for tests and a future admin route, mirroring
`shot_templates.py`.

```json
{
  "id": "dialog_ots_15",
  "name": "Dialog — over-the-shoulder",
  "description": "Two people talking: OTS track, reaction close-up, J-cut back to a two-shot, settle. Ends near the opening framing, which keeps identity stable.",
  "duration_s": 15,
  "look": "cinematic, photorealistic skin textures, fine grain",
  "soundscape_hint": "room ambience appropriate to the setting",
  "roles": [
    {"id": "A", "note": "the listener first; speaks second"},
    {"id": "B", "note": "speaks first"}
  ],
  "beats": [
    {"start_s": 0, "end_s": 4, "transition": "continuous",
     "cast": ["A", "B"], "speaker": "B",
     "camera": {"shot_size": "MS", "angle": "ots",
                "camera_motion": "truck_right", "camera_speed": "slow"},
     "note": "over A's shoulder onto B, who is speaking"},
    {"start_s": 4, "end_s": 5, "transition": "cut",
     "cast": ["B"], "speaker": null,
     "camera": {"shot_size": "CU", "angle": "eye", "camera_motion": "static"},
     "note": "B's face; a reaction, not a line"},
    {"start_s": 5, "end_s": 10, "transition": "j_cut",
     "cast": ["A", "B"], "speaker": "A",
     "camera": {"shot_size": "MS", "angle": "eye",
                "camera_motion": "pull_out", "camera_speed": "slow"},
     "note": "A's line starts before we see A; dolly back to a two-shot"},
    {"start_s": 10, "end_s": 15, "transition": "continuous",
     "cast": ["A", "B"], "speaker": null,
     "camera": {"shot_size": "MS", "angle": "eye",
                "camera_motion": "arc", "camera_amplitude": "small"},
     "note": "B smiles and nods"}
  ]
}
```

### 4.1 Fields

| Field | Type | Rule |
|---|---|---|
| `id` | string | Must equal the file stem. Unique. |
| `name`, `description` | string | Shown in the picker. |
| `duration_s` | number > 0 | The clip length this template sets. Must be one of the configured `i2v.durations`, or the template is loaded but reported unavailable with the reason (so a config change never breaks the library). |
| `look` | string, optional | Rendered once, as the opener of Shot 1, in the guide's "Live-action, cinematic, …" position. |
| `soundscape_hint` | string, optional | Guidance to the VLM for `overall_soundscape`. The VLM still writes the field. |
| `roles[]` | list, may be empty | `id` (string, unique within the template), `note` (string). |
| `beats[]` | list, ≥ 1 | See below. |

Per beat:

| Field | Rule |
|---|---|
| `start_s`, `end_s` | Beats must tile `[0, duration_s]` exactly: first `start_s` is 0, each `start_s` equals the previous `end_s`, last `end_s` equals `duration_s`. Every beat ≥ 0.5 s. |
| `transition` | `continuous` \| `cut` \| `j_cut`. The first beat must be `continuous`. |
| `cast` | list of role ids from `roles`. May be empty (a detail insert). |
| `speaker` | a role id in `cast`, or `null`. |
| `camera.shot_size` | `SHOT_SIZE_VALUES` (`ECU CU MCU MS MLS WS EWS`). Required. |
| `camera.angle` | `ANGLE_VALUES` minus `pov` (`eye low high overhead dutch ots`). Required. |
| `camera.camera_motion` | `CAMERA_MOTION_VALUES` minus `pov`. Required. |
| `camera.camera_amplitude` | `small` \| `large`, optional. |
| `camera.camera_speed` | `slow` \| `fast`, optional. |
| `camera.lens` | `LENS_VALUES`, optional. Rendered as a phrase ("on a macro lens") when present. |
| `note` | string, optional. Given to the VLM as the beat's brief. |

Vocabularies are imported from `storyboard_parse.py` /
`storyboard_story.py`, never redeclared. A template written in another
vocabulary (`"EYE"`, `"STATIC"`, `"OTS"` as a shot size) raises
`I2vTemplateError` naming the file and field at load. `pov` is excluded
because i2v has no POV mode.

### 4.2 Shots vs. beats

A template lists **beats**. H3 **shots** are derived: a `cut` or `j_cut`
beat opens a new `[Shot n]`; a `continuous` beat continues the current
shot with an intra-shot `At MM:SS.mmm,` timestamp (the same device the
storyboard's POV mode uses). So:

- the dialog example above → three H3 shots (`[Shot 1]` 0–4, `[Shot 2]`
  at 4.0, `[Shot 3]` at 5.0 with an intra-shot beat at 10.0);
- a melee template (wide → push-in tracking → cut to side medium) → two
  shots;
- an intimate template (macro tilt → focus pull → settle) → one shot with
  three timed beats.

This is exactly the guide's "prefer camera motion when only the distance
changes" rule, expressed as data.

## 5. Pipeline

`I2vRunner.generate_prompt(source_path, idea, duration_s, template_id)`:

### 5.1 `template_id = null` — single take

The existing path, byte-for-byte. `_BEAT_COUNTS`, `i2v_grammar`,
`build_i2v_user_prompt`, `validate_i2v_beats`, `assemble_i2v_prompt` and
`lint_i2v_prompt` are untouched. A test pins the current output on the
existing fixtures.

### 5.2 With a template — one VLM call

One grammar-constrained call with the source image, the idea, and a
per-beat brief. The grammar is generated from the template
(`i2v_templates.fill_grammar(template)`) with the role and beat counts
baked in, the way `shot_templates.fill_grammar` does:

```json
{
  "roles": [
    {"id": "A", "bound": true,
     "description": "a woman in her thirties, dark hair, red wool coat",
     "tag": "the woman in the red coat"},
    {"id": "B", "bound": false,
     "description": "a man in his forties, grey stubble, black leather jacket",
     "tag": "the man in the leather jacket"}
  ],
  "beats": [
    {"action": "…", "line": "I told you, we shouldn't be here."},
    {"action": "…"},
    {"action": "…", "line": "It's already too late."},
    {"action": "…"}
  ],
  "overall_soundscape": "…",
  "non_diegetic_music": "…"
}
```

- `line` is a required `string` where the template's beat has a
  `speaker`, and absent from the grammar where it doesn't — a missing or
  stray line is structurally impossible.
- `bound` is the VLM's statement of whether it found this role in the
  picture. `description` is one full identity sentence; `tag` is the
  short handle used on every later mention. Both are required for every
  role, bound or not.
- `overall_soundscape` / `non_diegetic_music` keep their existing
  defaults if empty.

The system prompt is a new YAML-backed `I2V_TEMPLATE_SYSTEM` in
`data/meta_prompt.yml`, hot-reloaded through the same `PromptStore`.
The user prompt (`build_i2v_template_user_prompt`) lists the roles with
their notes, then each beat as
`Beat 3 (5.0–10.0 s, medium shot, eye level, camera pulls out slowly;
on screen: A, B; A speaks): <note>`, plus the `soundscape_hint`.

### 5.3 Validation of the VLM result

`validate_i2v_template_fill(raw, template)` parses and checks: role ids
match, beat count matches, every `speaker` beat has a non-empty line,
tags are non-empty. Failures raise `I2vError` as the single-take
validator does.

### 5.4 The off-screen rule

Shot 1 is the only shot anchored to a picture. An **unbound** role that
the template casts in beat 1 is removed from beat 1's rendered cast, and
`lint` emits `role B is not in the picture; the template casts it in the
first shot, so it enters at beat 2 instead`. Its first rendered mention
(wherever it first appears) uses the full `description`; every mention
after uses the `tag`. A bound role's full description is rendered in
Shot 1 (its first appearance) and its tag afterwards.

### 5.5 Assembly

`assemble_i2v_template_prompt(template, fill)` produces H3 form, in
code, deterministically:

```
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] Cinematic, photorealistic skin textures, fine grain. The subjects, composition, and setting shown in <Picture 1> are established at 0.00 seconds and keep their appearance, clothing, colors, and spatial relationships. An over-the-shoulder medium shot looks past a woman in her thirties, dark hair, red wool coat, onto a man in his forties, grey stubble, black leather jacket. The camera trucks right at slow speed. The man in the leather jacket (S1) says: <d>[English] I told you, we shouldn't be here.</d> [Shot 2] At 00:04.000, the shot cuts to a close-up of the man in the leather jacket. The camera holds a static shot. <action>. [Shot 3] At 00:05.000, the woman in the red coat (S2) says: <d>[English] It's already too late.</d>, the words carrying over from the previous shot, as the shot cuts to a medium shot of the woman in the red coat and the man in the leather jacket. The camera pulls out at slow speed. <action>. At 00:10.000, the camera arcs around the subject with small amplitude. <action>.

overall_soundscape: …

non_diegetic_music: …
```

Rendering rules:

- **Shot 1** opener: existing `_OPENING` sentence, preceded by `look` if
  present. No timestamp (guide §4.2).
- **Later shots**: `[Shot n] At MM:SS.mmm, the shot cuts to <shot-size
  phrase> of <cast tags>.` Shot-size phrases come from a new table in
  `i2v_templates.py` (`CU` → "a close-up", `MS` → "a medium shot", `WS`
  → "a wide shot", `ECU` → "an extreme close-up", …); `ots` angle
  renders as "an over-the-shoulder <size> looks past <first cast> onto
  <rest>"; `low`/`high`/`overhead`/`dutch` add a phrase ("from a low
  angle").
- **`j_cut`**: the shot line is rendered with the speaker's line first
  and the suffix `her/his words carrying over from the previous shot`
  (the guide's own example uses "the baker's final words carry over from
  the previous shot"). Pronoun is avoided: the tag is used.
- **Continuous beat inside a shot**: `At MM:SS.mmm, <camera sentence>
  <action>` — no `[Shot n]`, no cut phrase.
- **Camera**: `_CAMERA_PHRASES` from `h3_compiler` plus `with small/
  large amplitude` / `at slow/fast speed` in the guide's order (the
  same rendering `i2v_fixes` targets, so Apply fixes never fights it).
- **Speaker ids** `(S1)`, `(S2)` … are assigned in order of first line,
  one per role, the base guide §4.4 form the existing lint already
  understands.
- **Timestamps** `MM:SS.mmm` from `start_s`.

### 5.6 Lint

`lint_i2v_prompt(text, duration_s, template=None)` gains template-aware
checks when a template is supplied. All are **warnings**; the prompt box
stays free text and Generate sends whatever is in it.

- `[Shot n]` numbering contiguous from 1; Shot 1 carries no timestamp;
  every later shot's cut time strictly increases and is `< duration_s`.
- Each H3 shot's text contains its template beats' camera phrases (the
  `build_expectations`/`camera_vocab` approach from `h3_compiler`).
- Every beat with a `speaker` has a `<d>…</d>` line in its span.
- Every role's tag appears in the span of every beat that casts it.
- The unbound-role-in-Shot-1 message from §5.4.

The existing single-take checks still run.

## 6. API

| Route | Change |
|---|---|
| `GET /api/i2v/templates` | New. `[{id, name, description, duration_s, available, unavailable_reason, roles, beats}]`. Beats are returned in full for the dialog's cadence strip. |
| `POST /api/i2v/prompt` | Body gains `template_id: string \| null`. With a template, `duration_s` must equal `template.duration_s` (400 otherwise, naming both), and an unknown id is 404. |
| `POST /api/i2v/lint` | Body gains optional `template_id` so template-aware checks run on hand edits. Unknown id → the single-take lint, plus a warning. |
| `POST /api/i2v/generate` | Body gains optional `template_id`. It is not used for the render (the prompt text already embodies it); it is recorded into the form snapshot so the ingested clip's `form_state` carries it. |
| `PATCH /api/i2v/videos/{id}` | Accepts `template_id` (string or null) as a form field. |

`i2v_form.FORM_FIELDS` gains `template_id` with a validator (string or
null). `build_form_state` takes it; `form_state_for_row` fills it with
`null` for clips that predate it. The runner's `_job_meta["form"]`
carries it, so a clip remembers its cadence.

`I2vService.list_templates()` wraps the loader and stamps
`available`/`unavailable_reason` against the configured durations.

## 7. Dialog

`I2VDialog.vue`:

- A **Cadence** select above Idea: "Single take" first, then each
  available template by `name`, its `description` as the tooltip.
  Unavailable templates are listed disabled with the reason.
- Choosing a template sets `durationS` to its `duration_s` and disables
  the duration select, with a hint ("set by the cadence"). Choosing
  Single take re-enables it, leaving the value as is.
- A read-only **cadence strip** under the select: one chip per beat —
  `0–4s · OTS MS · truck →`, `4–5s · CU · cut`, `5–10s · MS · J-cut`,
  `10–15s · MS · arc`. Rendered by a pure `cadenceChips(template)` in
  `types/i2v.ts`. Cut/J-cut chips are visually marked.
- **Expand prompt** sends `template_id`. Lint sends it too.
- `template_id` is part of `currentForm()` and the request signature, so
  changing cadence counts as a change for the "nothing has changed"
  guard, and loading a clip restores its cadence. A clip whose
  `template_id` no longer exists loads as Single take and shows a
  one-line note.
- Duration is still sent on Generate as today; the server does not need
  the template at generate time.

## 8. Shipped templates

Converted from the discussion examples, in `data/i2v_templates/`:

- `dialog_ots_15.json` — the §4 example.
- `melee_12.json` — 0–3 `WS` low-angle static, 3–7 `MS` tracking push
  fast (continuous), 7–12 `cut` to `MS` side, `shake_slight`. Two roles.
- `intimate_15.json` — 0–5 `ECU` macro `tilt_up` slow, 5–10 `MCU`
  `push_in` slow (continuous), 10–15 `CU` static (continuous). One role.

Each `description` says whether the cadence ends near the opening
framing (the most identity-stable shape in I2VA).

## 9. Documentation

- `CLAUDE.md`: a bullet under the i2v section covering: templates live in
  `data/i2v_templates/` and are validated against the storyboard
  vocabularies (never redeclare them); beats vs. shots and the
  `transition` rule; the off-screen rule; `template_id = null` is
  byte-identical to before; lint is warnings-only; `template_id` is a
  form field.
- `docs/api-reference.md`: the new route and the changed bodies.
- `docs/features.md`: the Cadence picker.
- `docs/i2v-templates.md`: how to write a template (the field table from
  §4.1, the shots-vs-beats rule, the three shipped files as worked
  examples).

## 10. Testing

Tests are written before code (TDD), in the existing style.

- **`tests/test_i2v_templates.py`** (loader + assembly, pure):
  vocabulary errors name file and field; beats must tile the duration;
  first beat must be `continuous`; `speaker` must be in `cast`; `pov`
  rejected; `id` must equal the stem; the three shipped files load; each
  compiles to an expected H3 string (golden tests) including the j_cut
  suffix, the continuous merge and the intra-shot timestamp; the
  unbound-in-Shot-1 rule removes the role and warns; speaker ids
  increment in first-line order; `fill_grammar` requires a line exactly
  where `speaker` is set (checked by parsing the grammar's per-beat
  rules); `validate_i2v_template_fill` rejects a missing line and a
  wrong beat count.
- **`tests/test_i2v_compiler.py`**: a golden test pins today's
  single-take output — `template_id = null` must not change it.
- **`tests/test_i2v_runner.py`**: `generate_prompt` with a template
  passes the template grammar and user prompt to the VLM (`FakeVlm`
  records them) and returns the assembled text + lint; `_job_meta["form"]`
  carries `template_id`.
- **`tests/test_i2v_form.py`**: `template_id` round-trips; null accepted;
  non-string rejected; legacy rows fill `null`.
- **`tests/test_i2v_api.py`**: templates route shape and availability
  stamping; `/prompt` duration mismatch → 400, unknown id → 404;
  `/lint` with a template runs the template checks; PATCH accepts
  `template_id`.
- **Frontend**: `npm run build` (type-check). `cadenceChips` is pure so
  its behaviour is reviewable by reading; there is no frontend test
  runner in this repo.

## 11. Out of scope

- An in-app template editor.
- Proportional rescaling of a template to another duration.
- Reference pictures for invented roles (would need I2VA → ref2va).
- A negative/constraints channel — H3 has none.
- Changing the single-take compiler.
