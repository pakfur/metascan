# User-selected scene templates + story traceability — design

Date: 2026-08-29. Supersedes the "Apply template" flow of
`refactor-spec-visual-story-quality.md` Phase E.

## Problem

Shot-list templates are applied *after* a scene exists, from a button in
the scene editor, and the compose pipeline never knows a scene is
template-built. The story's arc lives in an opaque JSON textarea and is
not fed to the beats stage or the template fill, so template-built scenes
read as "the template's pacing with my character names". Nothing records
what a scene or shot was derived from.

## Decisions

1. **The user selects the template per scene, before shots are built.**
   Not code, not the VLM. `scenes.template_id` is NULL (free-form) until
   the user picks one. The scenes stage's `function` is only a hint for
   ordering the dropdown.
2. **The shots stage is the single code path.** For each target scene:
   `template_id` set → bind → instantiate → fill → conform (the existing
   `apply_template` internals); NULL → free-form shots. The beats stage
   skips template-built scenes whose beats are already filled. The
   standalone `POST …/apply-template` route and the scene editor's "Apply
   template" button are removed.
3. **Selection problems block the build.** A pure
   `shot_templates.validate_assignment(template, scene, castable, share_s)
   -> (errors, warnings)` runs inside `check_compose_gates` for the shots
   stage; any error on any target scene → `StoryboardError` (400) listing
   every problem, before the 202 task exists. The same result is exposed
   read-only on the tree (`scene.template_problems`, `scene.
   template_warnings`) so the UI shows it live.
   - error: template roles > castable characters; unknown template id.
   - warning: template `function` ≠ scene `function`; template duration
     vs. the scene's share of `duration_target_s` (user's call).
4. **The arc reaches every stage.** `scenes.brief` (TEXT, emitted by the
   scenes stage: 1–3 sentences on what the scene must accomplish, derived
   from the arc entries it covers). The scenes prompt also receives the
   premise alongside the outline. The beats prompt and the template
   bind/fill prompts receive the scene's brief and the summaries of the
   arc entries in `arc_beats`.
5. **Provenance.** `scenes.composed_from` JSON
   `{stage, template_id?, outline_hash, at}` written by the scenes stage
   and rewritten by the shots stage; the rail shows a template badge and
   the outline dialog flags "outline changed since scenes were built"
   when `outline_hash` differs (same mechanism as the compile-staleness
   chip).

## Schema

- `scenes.template_id TEXT NULL`, `scenes.brief TEXT NULL`,
  `scenes.composed_from TEXT NULL` — idempotent adds. `ScenePatch` gains
  `template_id` (400 on an unknown id; `null` clears) and `brief`.
- Scenes-stage grammar/validator gain `brief` (string, required).

## UI

- **Outline dialog**: the outline renders as logline / tone / pacing /
  arc list (`stage: summary`, editable). After scenes exist, a per-scene
  table: name · arc chips · charge in→out · brief · **template select**
  (Free-form + templates, function matches first with ✓) · problems in
  red / warnings in amber. "Build shots" is disabled while any target
  scene has an error.
- **Scene editor**: template select with the same problem text; brief
  shown above setting; arc chips + charges visible. Changing template
  PATCHes and raises the existing "Rebuild shots?" downstream prompt.
- **Outline rail**: `⧉ <template_id>` badge on template-built scenes.

## Tests

- `validate_assignment` matrix (roles, unknown id, function mismatch,
  duration warning).
- `check_compose_gates` 400s with every problem listed; passes with
  Free-form.
- Shots stage branches per scene (template vs free-form) against the fake
  VLM; beats stage skips template-built scenes.
- Prompt builders include brief + arc summaries.
- DB round-trip for the three columns; PATCH validation.
