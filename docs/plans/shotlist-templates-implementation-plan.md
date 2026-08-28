# Implementation plan — shotlist templates (refactor-spec-visual-story-quality)

Branch: `worktree-shotlist-templates`. Implements Phases A–E of
`refactor-spec-visual-story-quality.md` so the `two_party_negotiation_18`
template can be applied to Weekend Getaway scene 0 and measured.

## Verified against the code (corrections to the spec)

- `render_retention_analysis` (h3_compiler.py:616) really is panel-scoped:
  every subject gets the full `timeline.shots` list. Confirmed.
- `replace_scene_panels` inserts only `action/duration_s/is_turn/subtext`;
  beats go in via `replace_panel_beats(panel_id, beats)`. The template
  writer must call both (one panel row, then its beats). Neither sets
  `subject_type` etc. — fine.
- There is no compile timestamp on panels today, only
  `video_compiled_anchor`. A2 needs a new `panels.video_compiled_at`.
- `beats` has no `kind` column. Phase E stores it (nullable `beats.kind`),
  since conformance asserts it and the UI may show it.
- `shotlist_dialogue_confession.md` is not on disk anywhere. The template
  is authored from the spec's §Phase E table + example slot shapes.
- The roster in the Weekend Getaway dump includes `College` (id 34) and
  `Cabin` as subjects — Phase C1's target.
- Camera enums are lowercase in code (`ANGLE_VALUES = ("eye", … "ots")`,
  `COMPOSITION_VALUES = ("thirds_left", …)`, `CAMERA_MOTION_VALUES`
  includes `"static"`). Template JSON uses the code vocabulary directly;
  the loader validates every value against the real constants and raises
  at load time (spec open decision 5).

## Decisions taken (spec "open decisions")

1. Template bypasses the beats stage; dedicated fill prompt + per-panel
   grammar whose slot count and dialog-nullability are baked in (a slot
   with `dialog_slot` gets `string`, not `nullable`, so an unfilled line
   is structurally impossible).
2. Role binding is a dedicated grammar-constrained VLM call; grammar alts
   are the scene's castable character names.
3. Scenes whose function has no template keep the shots+beats path; the
   template is applied explicitly per scene via a new endpoint.
4. `is_cut` follows the template (first slot of each section false, rest
   true).
5. Templates live at `data/templates/*.json`, loaded lazily + cached,
   fail-loud validation.

## Work items

### A — compile correctness
- A1 `h3.render_retention_analysis(refplan, subjects, scene, timeline, beats=None)`.
  With `beats` and non-POV: per-subject shot list from
  `shot.beat_indices → beats[i].subject_ids`; dialog-only speakers fall
  back to beats whose dialog names them; a subject in neither gets the
  whole list (keeps "appears in" non-empty). POV keeps whole list.
  Runner passes `beats`.
- A2 `panels.video_compiled_at TEXT` (idempotent add). `_compile_locked`
  writes `datetime('now')`-style UTC ISO on every successful compile.
  Frontend chip "beats changed since compile" when
  `max(beats.updated_at) > video_compiled_at`.

### B — dialogue presence
- B1 paragraph in `STORY_BEATS_SYSTEM`; B3 one sentence in
  `STORY_SHOTS_SYSTEM` (the action may name that a line is spoken).
- B2 `story.lint_scene_dialogue(panels_beats, subjects, scene_function)`
  → warnings; run once per scene after its beats stage completes (only
  for `VERBAL_FUNCTIONS = negotiation|confession|confrontation`, ≥2
  character subjects). Thresholds: ≥40% beats with dialog, every
  character present in ≥3 beats speaks, no speaker >70%.

### C — roster hygiene
- C1 `storyboard_subjects.subject_type TEXT NOT NULL DEFAULT 'character'`
  (`SUBJECT_TYPE_VALUES = character|location|prop`). Migration
  `user_version = 4`: infer `location`/`prop` from name+description
  keywords, default character. Runner's beats roster = characters only
  (validator already drops unknown names with a warning). API:
  `SubjectCreate/Patch.subject_type` validated (400). UI select.
- C2 adult-floor sentence in `STORY_OUTLINE_SYSTEM` and
  `REF_DESCRIBE_SUBJECT_SYSTEM`. Description is already editable.

### D — scene function
- `scenes.function TEXT` nullable; `SCENE_FUNCTION_VALUES`; scenes grammar
  field `"function"` (nullable enum); validator; one prompt line;
  `ScenePatch.function` validated; `Scene.function` in TS; select in
  `SceneEditDialog.vue`.

### E — template pilot
- `metascan/core/shot_templates.py`: dataclasses `ShotTemplate/Section/
  Slot/Camera`, `load_templates()`, `get_template(id)`, `list_templates()`,
  `instantiate(template, role_map)`, `ROLE_BIND_GRAMMAR(names)`,
  `fill_grammar(section)`, prompt builders, `validate_fill_response`,
  `conform(template, panels)` raising `TemplateConformanceError`.
- `data/templates/two_party_negotiation_18.json`.
- Runner `apply_template(storyboard_id, scene_id, template_id, confirm)`:
  gates via `check_compose_gates(stages=("shots","beats"), scene_ids=[scene])`;
  under `_synth_lock`; bind → instantiate → fill per section → conform →
  `replace_scene_panels` + `replace_panel_beats`; emits `story_progress`
  (stage `"template"`), `story_stage_complete`, `story_complete` /
  `story_error`.
- API: `GET /api/storyboard/templates` → `[{id, function, roles, sections, slot_count, duration_s}]`;
  `POST /api/storyboard/{id}/scenes/{scene_id}/apply-template`
  body `{template_id, confirm}` → 202 `{status:"started"}`, 409
  `confirm_required`, 400 on unknown template / too few characters.
- Frontend: `api.listTemplates`, `api.applyTemplate`; store
  `applyTemplate(sceneId, templateId, confirm)`; SceneEditDialog gets a
  template select + Apply button (confirm on 409 via `window.confirm`).

## Order
1. A1, A2 backend (h3_compiler, db, runner) — tests.
2. C1, D schema + validators + prompts (B1/B3/C2) + B2 lint — tests.
3. E module + template JSON + runner + API — tests.
4. Frontend (runs in parallel with 2–3 against the contract above).
5. `make quality test`, `npm run build`, commit.
