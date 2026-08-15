# Storyboard Reference Describe (Phase V2) — Design

Date: 2026-08-15
Status: Draft — awaiting review
Series: Phase V2 of 4. Depends on V1 only for the `subjects.voice` column
(soft dependency — V2 can ship first if V1 slips; the column move is
one-line). V3's compiler consumes the descriptor blocks this phase produces.

## 1. Goal

Let the user attach up to **two reference images per subject** and **one
setting reference per scene**, picked from the metascan library, and have
Qwen3-VL look at them and write the canonical descriptor text that every
downstream prompt carries verbatim: the subject's `description` (appearance,
wardrobe, distinguishing features) and the scene's `setting` (environment,
lighting direction, atmosphere). Descriptors are written **once**, stored on
the record, reviewed/edited by the user, then never paraphrased — the
existing anti-drift rule, which the H3 format (V3) also mandates via its
"same label keeps the same meaning across all sections" rule.

### Non-goals

- No new prompt-time image handling: at synthesis/compile time only the
  stored *text* is used. Reference images additionally flow to ComfyUI as
  pixels in V4 (`MS_REF_IMAGE_*`), not through the VLM.
- No auto-describe on attach. Describing is an explicit button — the user
  may prefer their hand-written description; the server must never
  overwrite silently.

## 2. Multi-image support in `VlmClient`

`generate_text` (`metascan/core/vlm_client.py:493`) currently accepts a
single optional `image_path` and hardcodes a two-part
`[text, image_url]` content list. Change:

```python
async def generate_text(self, *, system_prompt: str, user_prompt: str,
                        image_path: Optional[Path] = None,
                        image_paths: Optional[Sequence[Path]] = None,
                        ...) -> str
```

- `image_paths` is the new plural form; `image_path` is kept as sugar
  (`image_path=X` ≡ `image_paths=[X]`; passing both is a `ValueError`).
- The user content list becomes `[text] + [one image_url part per path]`,
  each encoded through the existing `_encode_image_b64` (1024-px resize,
  JPEG q85). llama-server's OpenAI-compatible endpoint accepts multiple
  `image_url` parts per message; order is prompt-referenced ("the first
  image", "the second image").
- Non-image paths (checked with `is_image_path`) raise `VlmError` up front —
  callers pick from an image-filtered picker, so this is a programming-error
  guard, not a user-facing path.
- Context: ~1–2 K vision tokens per image at 1024 px; two refs + prompt fits
  a 8192-token slot (the 30B-A3B per-slot budget) with room for the ~300
  token output.

## 3. Data model

Via `_idempotent_add_column`:

- `storyboard_subjects.reference_path_2 TEXT REFERENCES media(file_path)
  ON DELETE SET NULL` — second subject reference (e.g. face + full-body, per
  the H3 guide's identity-reference recommendation). Same `to_posix_path`
  normalization and `InvalidReferenceError` → 400 handling as the existing
  `reference_path` (`StoryboardService.create_subject/update_subject`
  precedent).
- `scenes.reference_path TEXT REFERENCES media(file_path) ON DELETE SET
  NULL` — the setting reference.

`get_storyboard_tree` returns the new fields (native-path converted, per the
stored-POSIX/API-native rule).

## 4. Describe endpoints

Two routes in `backend/api/storyboard.py`, both **synchronous awaits** (a
single VLM call, seconds — no 202/WS machinery needed):

| Method | Path | Behavior |
|---|---|---|
| POST | `/api/storyboard/subjects/{subject_id}/describe` | Reads the subject's 1–2 reference paths; 400 if none. Calls `generate_text` with both images and `REF_DESCRIBE_SUBJECT_SYSTEM`. Returns `{"description": str, "voice": str\|null}` — **does not write the DB.** |
| POST | `/api/storyboard/scenes/{scene_id}/describe` | Same with the scene's setting reference and `REF_DESCRIBE_SETTING_SYSTEM`; returns `{"setting": str, "lighting": str\|null, "mood": str\|null}`. |

Returning without writing keeps the user in control: the frontend shows the
generated text in the (already-editable) field, and the normal
PATCH-on-commit flow persists it. No silent overwrites, no new locking
semantics, and a re-describe is a plain re-POST.

Runner involvement: none — these are service-level calls
(`StoryboardService.describe_subject/describe_scene`) that use
`get_vlm()`/`ensure_started` via the same accessor the runner uses (503
when no VLM is configured, matching `parse`). They do **not** take
`_synth_lock`: llama-server multiplexes slots, and a describe racing a
synthesis is harmless (both are reads of the model).

## 5. Prompts

New YAML keys in `data/meta_prompt.yml`:

- `REF_DESCRIBE_SUBJECT_SYSTEM` — "You are writing a character identity
  block for video generation… Describe ONLY what is visible: age range,
  build, hair, face, wardrobe layers with colors, accessories,
  distinguishing marks. One dense sentence fragment list, no narrative, no
  name. If a voice can be inferred from apparent age/build, propose one."
  Output shape enforced by `REF_DESCRIBE_SUBJECT_GRAMMAR` (JSON
  `{description, voice}`).
- `REF_DESCRIBE_SETTING_SYSTEM` / `_GRAMMAR` — same pattern for environment:
  location type, key set-dressing, lighting direction/quality, palette,
  atmosphere; JSON `{setting, lighting, mood}`.

Both carry the uncensored directive (NSFW reference images must be described
factually, not refused) and the instruction that the text will be carried
verbatim into generation prompts (favor concrete nouns/adjectives over
prose).

## 6. Frontend

- **StoryboardSettingsDialog** (subjects section): a second reference row
  per subject (same `ReferenceImagePicker` + thumbnail + clear pattern as
  the existing one), a `voice` text input, and a **"Describe from refs"**
  button per subject — disabled when no reference is set; on response, fills
  the Description (and Voice, if returned and currently empty) inputs as
  *uncommitted edits* so the user reviews before the field's normal
  change-commit fires. Busy spinner during the call; toast on error.
- **SceneEditDialog**: setting-reference picker row + "Describe from ref"
  button filling Setting (and Lighting/Mood when those fields are empty),
  same uncommitted-edit semantics.
- **Types/API**: `Subject.reference_path_2`, `Subject.voice`,
  `Scene.reference_path`; `describeSubject(id)` / `describeScene(id)`
  fetchers in `src/api/storyboard.ts`.
- `ReferenceImagePicker` already filters out videos — unchanged.

## 7. Error handling

- No refs attached → 400 `{detail: "no reference image set"}`.
- VLM down/not configured → 503 (existing `_require_*` pattern).
- `VlmError` (timeout, crash) → 502 with the message; the client toast
  suggests retry. No retry loop server-side — the user is present.
- A reference path whose media row was deleted after attach: FK is
  `ON DELETE SET NULL`, so describe sees no ref → 400; the settings dialog's
  thumbnail `@error` fallback already signals the dangling state.

## 8. Testing

- `tests/test_vlm_multi_image.py`: `generate_text` payload construction for
  0/1/2 images (assert one `image_url` part per path, order preserved,
  `ValueError` on both params, `VlmError` on non-image path) against a
  stubbed httpx transport — no real llama-server.
- `tests/test_storyboard_describe_api.py`: route contracts (400 no-ref, 503
  no-VLM, happy path returns JSON without DB write, DB unchanged asserted)
  with a fake VLM object via `TestClient`.
- DB tests extend `tests/test_storyboard_db` coverage for the two new
  columns' POSIX normalization + `InvalidReferenceError` mapping +
  `ON DELETE SET NULL`.
