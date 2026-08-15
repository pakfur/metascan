# Storyboard Video Generation & Review (Phase V4) — Design

Date: 2026-08-15
Status: Draft — awaiting review
Series: Phase V4 of 4. Depends on V1 (beats/durations), V2 (reference
paths), V3 (compiled `video_prompt`, `assign_reference_labels`).

## 1. Goal

Drive the locally-hosted MiniMax H3 ComfyUI workflow per shot: submit the
compiled video prompt with its reference images (and optional keyframe
anchor and audio refs), collect the rendered clip over HTTP, ingest it
hidden into the library as a panel candidate, and review/select/reroll it in
the storyboard UI — the existing panel-image loop with a video payload.

The whole phase is plumbing through **existing machinery**: `ComfyClient`'s
queue/WS/interrupt handling, `Scanner.ingest_file` (already
video-capable: `.mp4/.webm/.mov`, FFmpeg thumbnails), `panel_images` (its
`media(file_path)` FK is format-agnostic), `_ingest_outputs`'s
hidden-ingest + WS flow, and the frontend candidate picker. No second job
driver.

### Non-goals

- LTX-2.5 workflows (same seam as V3 §8 — a second preset later).
- Audio *generation* control beyond what the workflow graph does natively.
- Cross-shot automatic assembly/concatenation of clips into the full film
  (a future editing phase; V4 ends at per-shot clips in the library).

## 2. Bindings — new preset kind `ref2v`

`workflow_presets.kind` CHECK constraint extends to
`('t2i','ref','ref2v')` via the standard `sqlite_master` DDL-gated
create/copy/drop/rename rebuild (the same procedure `_init_database`
already uses for the `storyboards.folder_id` type fix) — run once when the
live DDL's CHECK lacks `'ref2v'`.

`comfy_bindings.py` additions (same `_meta.title` convention,
`resolve_bindings` fails loudly listing all missing titles):

| Title | Required widgets | Required for `ref2v`? |
|---|---|---|
| `MS_POSITIVE` | `text` | yes — receives the compiled six-section prompt |
| `MS_SEED` | `seed` or `noise_seed` | yes |
| `MS_SAVE` | exists | yes — the video-save output node |
| `MS_NEGATIVE` | `text` | optional |
| `MS_REF_IMAGE`, `MS_REF_IMAGE_2`, `MS_REF_IMAGE_3` | `image` | optional |
| `MS_FIRST_FRAME`, `MS_LAST_FRAME` | `image` | optional |
| `MS_AUDIO`, `MS_AUDIO_2` | `audio` | optional |
| `MS_DURATION` | `value` (seconds) | optional |

`Bindings` gains the corresponding optional fields; `GenerationParams`
gains `ref_images: list[str]` (ordered, replaces single `ref_image` for
`ref2v` — the legacy field stays for `ref` presets), `first_frame`,
`last_frame`, `audio_refs: list[str]`, `duration_s`. `apply_overrides`
writes each supplied param and raises `BindingError` when a param arrives
without its binding — **submit-time validation therefore catches "storyboard
has 3 pictures but workflow has 2 ref slots" before anything is queued**
(the existing half-run-avoidance precedent in `generate()`).

Reference order comes from V3's `assign_reference_labels` — the same
`RefPlan` that numbered `<Picture N>` in the prompt orders the uploads into
`MS_REF_IMAGE[, _2, _3]`, so prompt text and pixels can never disagree.

## 3. Uploads

`ComfyClient.upload_image` generalizes to `upload_file(path, *,
subdir="image")` — same SHA-256 content-hash cache, same
`/upload/image` multipart endpoint (ComfyUI stores any input file it
receives there; audio and image both land in the input dir). MIME type
derived from suffix. `upload_image` remains as a thin wrapper.

First-frame anchors: `panels.video_anchor TEXT` ∈
`{NULL, 'keeper', 'prev_last'}` (nullable, `_idempotent_add_column`):

- `'keeper'` — the panel's selected still image (the existing SDXL/Flux
  panel pipeline's keeper) uploads into `MS_FIRST_FRAME`, and V3's compiler
  is told `keyframe=True` (it emits the I2VA alignment line +
  `keyframe completion` task type).
- `'prev_last'` — the previous panel's selected *video*'s last frame,
  extracted via FFmpeg (`extract_last_frame(video_path, out_png)` helper in
  `metascan/utils/ffmpeg_utils.py`, using the existing `get_ffmpeg_path`)
  into the scratch dir, then uploaded. Submit-time validation requires the
  previous panel to have a selected video; otherwise the panel fails
  validation upfront with a clear message.
- `NULL` — pure reference generation, no anchor.

Anchor changes affect the compiled prompt (alignment line, task type), so
the mismatch must be visible: `compile_video` records the anchor it
compiled against in `panels.video_compiled_anchor TEXT` (nullable, written
alongside `video_prompt`), and the UI shows a "recompile suggested" chip on
any panel where `video_anchor ≠ video_compiled_anchor`. Nothing is blocked
— generate-video submits whatever `video_prompt` holds — but the mismatch
is never silent.

Audio voice references: `storyboard_subjects.voice_ref_path TEXT` — plain
filesystem path (audio files are not library media; no FK). When set, V3's
compiler emits the `<Audio N>` definition + `reference` retention line for
that subject's `(Sx)` (this is the one V3 touch-point implemented in V4; the
compiler's `RefPlan` already reserves the numbering seam), and the file
uploads into `MS_AUDIO[_2]`.

## 4. Output collection — video-aware

`collect_outputs` (`comfy_client.py:1011`) currently reads only
`entry["outputs"][bindings.save]["images"]`. It generalizes to iterate the
save node's output arrays across the keys ComfyUI video-save nodes use —
`images`, `gifs`, `videos`, `video`, `audio` — collecting every element
carrying a `filename`, downloading each via the same `/view` fetch
(`_download_image` renamed `_download_output`; identical logic). Non-media
sidecar files (e.g. workflow JSON dumps) are filtered by extension against
`Scanner.SUPPORTED_EXTENSIONS` before ingest; unknown extensions are still
downloaded to `output_dir` but not ingested (logged).

`tests/_fake_comfy_server.py` gains a video-output mode (history entry with
a `gifs`/`videos` array) per its "models all the sharp edges" mandate.

## 5. Data model & runner

- `storyboards.video_preset_id INTEGER REFERENCES workflow_presets(id)` —
  the `ref2v` preset, separate from the stills `preset_id`. Same no-cascade
  delete-protection semantics (`PresetInUseError`/409 path already keys off
  `generation_jobs.preset_id`, which video jobs also carry).
- `panel_images` reused unchanged for video candidates — rows point at
  `.mp4` media; `seed`, `variant_index`, `prompt_used` (the compiled video
  prompt), `preset_id`, `comfy_prompt_id` all apply; keeper selection and
  hidden-media semantics are identical, including every `_release_panels` /
  purge path (media rows for videos behave the same).

`StoryboardRunner.generate_video(storyboard_id, panel_ids=None,
only_failed=False) -> List[int]` mirrors `generate()`:

1. Requires `video_target`, `video_preset_id`, and non-empty
   `video_prompt` on every target panel — all validated upfront
   (half-run avoidance), including ref-slot arithmetic (§2) and anchor
   prerequisites (§3).
2. VLM unload under `_synth_lock` (`unload_vlm_during_generation`) — H3 in
   ComfyUI wants the VRAM even more than SDXL does.
3. Per panel: uploads per `RefPlan` (content-hash cache dedups repeats
   across panels), seed via the same `panel_seed` +
   in-flight-aware `variant_base` accounting, `output_dir` =
   `<output_root>/<slug>/scene_NN/panel_NN/` (existing scheme),
   `comfy.submit(video_preset_id, params, panel_id=…, output_dir=…)`.
4. Ingest: the existing `handle_job_event` → `_ingest_outputs` path works
   unchanged (files → `set_media_hidden` → `create_panel_image` → folder
   add → `panel_images_changed` + `folder_items_changed`).

Job progress, cancellation (`_stop_prompt` with prompt_id, interrupted-as-
terminal), rehydration at startup: all inherited from `ComfyClient`
untouched.

## 6. Backend API

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/storyboard/{id}/generate-video` | `{panel_ids?, only_failed?}` | `{"jobs": [int]}` — errors 400 (validation), 503 (Comfy down), mirroring `/generate` |

Plus `StoryboardPatch` gains `video_preset_id` (nullable),
`PanelPatch` gains `video_anchor` (nullable, validated enum),
`SubjectPatch`/`SubjectCreate` gain `voice_ref_path` (nullable).
Preset registration reuses `POST /api/comfy/presets` with
`kind: "ref2v"` — no new route.

## 7. Frontend

- **PresetRegistrationDialog**: `ref2v` in the kind select.
- **StoryboardSettingsDialog**: video preset select (of `kind==='ref2v'`
  presets) next to the video-target select; per-subject voice-ref path
  input.
- **StoryboardView** header: "Generate video" action (enabled when
  `video_target` + `video_preset_id` set); job overlays in `PanelGrid`
  already work (video jobs are `generation_jobs` keyed to panels — the
  `comfy` WS channel + `jobToPanel` need zero changes).
- **PanelDetail**: anchor select (None / First frame from keeper / Continue
  from previous shot); "Render video" per-panel button beside Reroll; the
  candidate strip shows video candidates via the existing FFmpeg-backed
  `thumbnailUrl`; the expand viewer's synthesized `Media` objects set
  `is_video` (and let `MediaViewer`'s existing `VideoPlayer` handle
  playback) by extension — replacing the current always-image placeholder
  assumption in `viewerMedia` (`PanelDetail.vue:353-368`).
- Keeper selection for videos uses the existing `selectImage()` →
  `/select` route — unchanged; "keeper video" is what `'prev_last'`
  chaining and the eventual assembly phase read.

## 8. Error handling

- Upfront validation failures name every failing panel and reason in the
  400 detail (list, not first-only), so a 12-shot run doesn't fail
  piecemeal.
- ComfyUI `execution_error` / `execution_interrupted` → job `failed` /
  `cancelled` via existing paths; `only_failed=True` re-runs exactly those
  (existing `latest_jobs_for_panels` mechanism).
- FFmpeg last-frame extraction failure → that panel fails validation with
  the FFmpeg stderr tail; other panels proceed (extraction happens during
  submit loop, after upfront checks — the one per-panel step that can't be
  fully pre-validated cheaply; failure marks the job failed before submit).

## 9. Testing

- `tests/test_comfy_bindings_ref2v.py`: required/optional title matrices,
  multi-ref `apply_overrides`, param-without-binding errors.
- `tests/test_comfy_video_outputs.py`: `collect_outputs` against the fake
  server's video mode (gifs/videos keys, sidecar filtering, `/view`
  download), `job_outputs` payload.
- `tests/test_storyboard_video_runner.py`: fake Comfy + fake DB — upfront
  validation matrix (missing prompt/preset/anchor/ref slots), RefPlan
  upload ordering, seed accounting, ingest round-trip.
- `tests/test_storyboard_video_api.py`: route contract via `TestClient`.
- FFmpeg helper test gated on `ffmpeg` availability (skip-if-missing, per
  existing FFmpeg test precedent).

## 10. Follow-ups explicitly out of scope

- Film assembly (concatenate keeper clips per scene → full cut) — natural
  Phase V5; keeper videos + `sort_order` already contain everything needed.
- LTX-2.5 preset + dialect.
- Per-beat rerolls (a beat edit currently implies recompile + re-render of
  its shot — acceptable; H3 generates whole clips).
