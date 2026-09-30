---
paths:
  - "frontend/src/components/storyboard/**"
  - "frontend/src/views/StoryboardView.vue"
  - "frontend/src/stores/storyboard.ts"
  - "frontend/src/api/storyboard.ts"
  - "frontend/src/types/storyboard.ts"
  - "frontend/src/utils/storyboard*.ts"
---

# Storyboard authoring UI (frontend)

Moved out of the root CLAUDE.md so it loads only when you work with the files above;
the root CLAUDE.md still applies.

- **`stores/storyboard.ts` correlates ComfyUI jobs to panels/beats
  client-side.** `refreshActiveJobs()` rebuilds `jobToPanel` and
  `jobToBeat` from `GET /api/comfy/jobs`
  (`comfyApi.listJobs('queued'|'running', 1000)`) filtered to the current
  tree's panel ids and beat ids — this is what survives a page reload
  mid-generation, since there's no other durable client record of
  in-flight jobs. Live
  updates then come off the `comfy` WS channel: `job_update` moves a panel
  in/out of `panelJobState` (`done`/`cancelled` clears it), `job_progress`
  sets `{state: 'running', value, max}`; `job_outputs` is ignored on this
  channel — image ingestion is signaled separately. The `storyboard` channel
  drives refreshes: `beat_images_changed` and `synthesis_complete` both
  trigger a full `refresh()` (no per-panel GET exists, and refresh preserves
  selection), `synthesis_progress` updates the running counter in place, and
  `synthesis_error` surfaces the message without refetching. Both handlers
  drop events whose `storyboard_id` doesn't match the loaded `tree.value.id`
  — necessary because `attachWs()` is called from `StoryboardView`'s
  `<script setup>` on every mount (no module-level "already attached"
  guard), so switching boards must not let a stale board's events leak in.
  Keeper selection always goes through `selectImage(beatId, …)` →
  `POST /api/storyboard/beats/{id}/select` — never
  `PATCH /api/storyboard/beats/{id}`,
  which has no concept of `beat_images` and cannot flip `media.hidden` on
  the old/new keeper. (No UI calls `selectImage` since the video-only UX
  pass removed the keeper strip; the rule binds any reintroduction.)
- **Storyboard UX is video-only (2026-08-20).** All image-generation UX
  was removed from the frontend: the "Generate all" / per-beat Reroll /
  Re-synth buttons, the per-beat prompt editor (+ `BeatPromptDialog.vue`,
  deleted), the candidates/keeper strip and keeper thumbnails
  (`PacingStrip.vue` segments and the outline rail no longer render
  images), the Image LoRAs editor, the subject LoRA name/strength inputs,
  and the create/settings dialog fields that only fed the image path
  (target model, stills preset, batch size, style block, negative, image
  name prefix — verified: `generate_video` reads `base_seed` but not
  `negative`/`style_block`). `PresetRegistrationDialog.vue` registers
  `ref2v` only. This was strictly a frontend pass: the backend endpoints
  (`/synthesize`, `generate()`, `/beats/{id}/select`), the store actions,
  and the DB columns all remain — `CreateStoryboardDialog.vue` silently
  sends a default `target_model` because the column is NOT NULL, and the
  `DeleteImagesDialog.vue` purge/keep flows are kept because legacy
  boards still hold generated images the delete cascades must handle.
- **Downstream-dependency prompts are store-driven, not editor-driven.**
  `frontend/src/utils/storyboardDeps.ts` is the single table of which
  editable fields feed which recompute stage — shot `action`/`subtext`/
  `is_turn` → beats compose; scene descriptors (name, subtitle, setting,
  location, mood, lighting, time_of_day, function) → shots(+beats)
  compose; every beat field the H3 compiler renders → video-prompt
  compile — with `downstreamFor(entity, changedFields)` as the pure
  query. `stores/storyboard.ts::noteDownstream` runs after every
  *successful* `patchPanelFields`/`patchSceneFields`/`patchBeatFields`
  (so every commit-on-change handler is covered without touching it),
  skips when nothing downstream exists yet (shot has no beats, scene has
  no shots, shot has no compiled `video_prompt`), and merges repeated
  edits into one prompt per `(kind, target)` key. `ShotHeader.vue`
  renders the beats/compile prompts inline (accept → the existing
  `rebeat()`/`compile()` paths, so the 409 `confirm_required` flow still
  gates destructive re-beats); `SceneEditDialog.vue` stays open after Save
  to offer "Rebuild shots" (`composeStory({stages:['shots','beats'],
  scene_ids})`). `composeStory`/`compileVideo` clear the matching prompts
  on success and `load()` clears them all on a board switch. Premise /
  story-scale edits deliberately do NOT prompt — outline rebuild is
  whole-board destructive and lives in the Compose dialog. When a stage
  starts reading a new field, add it to the table; never re-add per-editor
  ad-hoc prompts.
