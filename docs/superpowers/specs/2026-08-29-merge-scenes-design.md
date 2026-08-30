# Merge scenes — design

Date: 2026-08-29. Companion to `2026-08-29-user-selected-scene-templates-design.md`.

## Problem

The scenes stage splits a story into "one continuous location and time"
units, while a shot-list template is a dramatic unit (the pilot runs 63 s).
Three or four generated scenes often belong to one template. The user
needs to fold adjacent scenes into one before selecting a template.

## Decisions

1. **Merge is a user action on adjacent scenes, no VLM.** `POST
   /api/storyboard/{id}/scenes/merge {scene_ids: [...]}` (≥ 2 ids, all in
   the storyboard, contiguous by `sort_order`). Returns `{id}` of the
   surviving scene (the first by sort order).
2. **Deterministic field rules.** From the first scene: `name`, `subtitle`,
   `setting`, `location`, `time_of_day`, `mood`, `lighting`,
   `reference_path`, `function`, `charge_in`. From the last: `charge_out`.
   Joined in order (blank parts dropped, `"\n\n"` separator): `brief`,
   `notes`. `arc_beats`: ordered union in `ARC_BEAT_VALUES` order.
   `template_id` → NULL (the user picks again). `composed_from` →
   `{stage: "merge", template_id: None, outline_hash: <current>, at}` so
   provenance shows the scene is hand-shaped.
3. **Non-destructive for shots.** The absorbed scenes' panels are
   re-parented onto the survivor, sort orders continuing after the
   survivor's own panels (no beats/images/jobs touched). The absorbed
   scene rows are then deleted; remaining scenes are resequenced 0..n-1.
   Rebuilding shots afterwards goes through the normal confirm/purge gate.
4. **UI.** Compose dialog scene table: a checkbox per row and a **Merge
   selected** button (enabled for ≥ 2 contiguous checks). Outline rail
   scene ⋯ menu: **Merge with next scene** (disabled on the last scene).
   Both call `store.mergeScenes(ids)` → refresh; the merged scene raises
   the existing "Rebuild shots?" downstream prompt when it has panels.
5. **Errors.** 400 for < 2 ids, non-contiguous ids, or ids outside the
   storyboard; 404 for an unknown storyboard.

## Tests
- DB: field rules, arc union order, panel re-parenting + resequencing,
  scene resequencing, non-contiguous rejection.
- API: 200 `{id}`, 400 on bad ids, tree reflects the merge.
