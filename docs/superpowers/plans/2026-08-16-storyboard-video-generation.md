# Storyboard Video Generation & Review (Phase V4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive the locally-hosted MiniMax H3 ComfyUI workflow per shot — compiled prompt + reference images + optional keyframe anchor + optional voice-reference audio in, video clips out — ingested hidden as panel candidates and reviewed/rerolled in the storyboard UI.

**Architecture:** A new `ref2v` preset kind with extended MS_* bindings and `GenerationParams`; `ComfyClient` grows a generic `upload_file` and video-aware output collection; `StoryboardRunner.generate_video` mirrors `generate()` (upfront validation, VLM unload, RefPlan-ordered uploads, seed accounting) and reuses the existing ingest/WS loop and `panel_images` storage unchanged; V3's compiler gains `<Audio N>` definitions and records the compiled anchor.

**Tech Stack:** Python 3.11 / FastAPI / SQLite, ComfyUI HTTP+WS driver, FFmpeg (last-frame extraction), Vue 3 + Pinia + TypeScript.

**Spec:** `docs/superpowers/specs/2026-08-15-storyboard-video-generation-design.md`

## Global Constraints

- `make quality test` before every commit (worktree: `make VENV_DIR=/home/jk/gws/metascan/venv quality test`; known flake `test_file_watcher_triggers_reload`, full-suite only); frontend tasks also `cd frontend && npm run build`.
- Tests never talk to a real ComfyUI/VLM — `tests/_fake_comfy_server.py` and fakes only. The fake server "models all the sharp edges" and MUST keep doing so when extended.
- Reference upload order comes from V3's `assign_reference_labels` RefPlan — prompt text and uploaded pixels must be ordered by the SAME function; never re-derive ordering elsewhere.
- Upfront validation lists ALL failing panels (spec §8), and validation happens before ANY upload/submit (the `generate()` half-run-avoidance precedent).
- `panel_images` is reused unchanged for video candidates; every existing hidden/keeper/purge invariant continues to hold.
- New columns via `_idempotent_add_column`; the `workflow_presets.kind` CHECK extends via the `sqlite_master`-DDL-gated create/copy/drop/rename rebuild (the `storyboards.folder_id` precedent).
- mypy strict on `metascan/core/*`; never stage `metascan/core/meta_prompt_templates.py`; stage files explicitly.
- Out of scope (spec §10): film assembly, LTX preset, per-beat rerolls. Also ruled: fl2va's SECOND (last-frame) anchor is not auto-wired in V4 — `MS_LAST_FRAME` exists as an optional binding for hand-built workflows; the runner wires `first_frame` only.

---

### Task 1: Schema — preset-kind rebuild + video columns

**Files:**
- Modify: `metascan/core/database_sqlite.py` (workflow_presets DDL ~line 440-449 + a gated rebuild in `_init_database`; column adds next to the V3 block ~line 736-772; updatable sets)
- Test: `tests/test_storyboard_videogen_db.py` (create)

**Interfaces:**
- Consumes: `_idempotent_add_column`; the `storyboards.folder_id` INTEGER→TEXT rebuild precedent (read it — same file, search "folder_id" near `_init_database`) for the CHECK rebuild shape.
- Produces:
  - `workflow_presets.kind` CHECK becomes `('t2i','ref','ref2v')`. Rebuild runs ONLY when the live DDL (read from `sqlite_master`) lacks `'ref2v'`: `CREATE TABLE workflow_presets_new (... same columns, new CHECK ...); INSERT INTO ... SELECT ...; DROP; ALTER RENAME;` under `PRAGMA foreign_keys=OFF` inside the init transaction, mirroring the folder_id rebuild exactly. Fresh DBs get the new CHECK directly in the `CREATE TABLE IF NOT EXISTS`.
  - Columns: `storyboards.video_preset_id INTEGER REFERENCES workflow_presets(id)`; `panels.video_anchor TEXT`; `panels.video_compiled_anchor TEXT`; `storyboard_subjects.voice_ref_path TEXT` (plain filesystem path, NO media FK — audio files are not library media).
  - `_STORYBOARD_UPDATABLE` += `{"video_preset_id"}`; `_PANEL_UPDATABLE` += `{"video_anchor", "video_compiled_anchor"}`; `_SUBJECT_UPDATABLE` += `{"voice_ref_path"}`; `create_subject` gains `voice_ref_path: Optional[str] = None`.
  - Tree passthrough automatic (`SELECT *`); no decode needed (all plain TEXT).

- [ ] **Step 1: Write the failing tests** (reuse `tests/test_storyboard_db.py`'s fixture):

```python
"""V4 schema: ref2v preset kind + video-generation columns."""

import pytest

from metascan.core.database_sqlite import DatabaseManager


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


def test_ref2v_preset_kind_accepted(db):
    pid = db.create_workflow_preset("h3", "ref2v", "{}", "{}")
    assert db.get_workflow_preset(pid)["kind"] == "ref2v"


def test_bad_kind_still_rejected(db):
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        db.create_workflow_preset("x", "bogus", "{}", "{}")


def test_check_rebuild_preserves_existing_presets(tmp_path):
    # Simulate an OLD database: create it with the pre-V4 CHECK, insert a
    # row, close, then reopen through DatabaseManager (which must rebuild).
    import sqlite3
    dbdir = tmp_path / "db"
    dbdir.mkdir()
    dbfile = dbdir / "metascan.db"  # match DatabaseManager's actual filename
    # BINDING DIRECTIVE: open tests/test_storyboard_db.py or DatabaseManager
    # to confirm the on-disk filename/layout the fixture produces, and
    # mirror it here so DatabaseManager(dbdir) reopens THIS file.
    conn = sqlite3.connect(dbfile)
    conn.execute(
        "CREATE TABLE workflow_presets ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, "
        "kind TEXT NOT NULL CHECK(kind IN ('t2i','ref')), "
        "workflow_json TEXT NOT NULL, bindings TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.execute(
        "INSERT INTO workflow_presets (name, kind, workflow_json, bindings) "
        "VALUES ('old', 't2i', '{}', '{}')"
    )
    conn.commit()
    conn.close()
    mgr = DatabaseManager(dbdir)
    try:
        rows = mgr.list_workflow_presets()
        assert any(r["name"] == "old" and r["kind"] == "t2i" for r in rows)
        pid = mgr.create_workflow_preset("new", "ref2v", "{}", "{}")
        assert mgr.get_workflow_preset(pid)["kind"] == "ref2v"
    finally:
        mgr.close()


def test_video_columns_roundtrip(db):
    sb = db.create_storyboard(
        name="B", target_model="sd", architecture="t2i", base_seed=1
    )
    scene = db.create_scene(sb, name="S")
    panel = db.create_panel(scene, action="a")
    pid = db.create_workflow_preset("h3", "ref2v", "{}", "{}")
    db.update_storyboard(sb, video_preset_id=pid)
    db.update_panel(panel, video_anchor="keeper", video_compiled_anchor="keeper")
    subj = db.create_subject(sb, name="M", description="d",
                             voice_ref_path="/audio/maya.wav")
    assert db.get_storyboard(sb)["video_preset_id"] == pid
    got = db.get_panel(panel)
    assert got["video_anchor"] == "keeper"
    assert got["video_compiled_anchor"] == "keeper"
    assert db.get_subject(subj)["voice_ref_path"] == "/audio/maya.wav"
```

- [ ] **Step 2: Run to verify failure** — `venv/bin/pytest tests/test_storyboard_videogen_db.py -v` → FAIL.
- [ ] **Step 3: Implement** per Produces (rebuild helper reads `sqlite_master.sql` for `workflow_presets`, triggers only when `'ref2v'` absent; copy column list explicitly, not `SELECT *`, to survive column-order drift).
- [ ] **Step 4: Run** — new file + `tests/test_comfy_db.py tests/test_storyboard_db.py` → all PASS.
- [ ] **Step 5: Quality gate + commit**

```bash
git add metascan/core/database_sqlite.py tests/test_storyboard_videogen_db.py
git commit -m "feat(storyboard): ref2v preset kind + video generation columns"
```

---

### Task 2: `comfy_bindings` — ref2v titles, params, overrides

**Files:**
- Modify: `metascan/core/comfy_bindings.py`
- Test: `tests/test_comfy_bindings_ref2v.py` (create)

**Interfaces:**
- Consumes: current shapes — `Bindings(positive, seed, seed_widget, latent, save, negative=None, lora=None, ref_image=None)` (comfy_bindings.py:44-62), `GenerationParams(positive, seed, width, height, batch_size, negative=None, lora_name=None, lora_strength=None, ref_image=None)` (:158-173), `_REQUIRED_WIDGETS`/`_REQUIRED_TITLES` (:20-33), `apply_overrides` (:183-232).
- Produces:
  - `_REQUIRED_TITLES["ref2v"] = ("MS_POSITIVE", "MS_SEED", "MS_SAVE")` (no MS_LATENT).
  - `_REQUIRED_WIDGETS` additions: `MS_REF_IMAGE_2`/`MS_REF_IMAGE_3` → `("image",)`; `MS_FIRST_FRAME`/`MS_LAST_FRAME` → `("image",)`; `MS_AUDIO`/`MS_AUDIO_2` → `("audio",)`; `MS_DURATION` → `("value",)`.
  - `Bindings` gains `ref_image_2: Optional[str] = None`, `ref_image_3: Optional[str] = None`, `first_frame: Optional[str] = None`, `last_frame: Optional[str] = None`, `audio: Optional[str] = None`, `audio_2: Optional[str] = None`, `duration: Optional[str] = None`; **`latent` becomes `Optional[str]`** (no default — keeps field order; `resolve_bindings` passes `None` when the workflow has no `MS_LATENT`, which stays REQUIRED for `t2i`/`ref` via `_REQUIRED_TITLES` so those kinds never see None). `from_json` backward compatibility: old stored JSON lacks the new keys — construct via `cls(**{**{f.name: None for f in fields(cls) if f.default is None or f.default is not MISSING}, **json.loads(raw)})` or simpler: give every NEW field a `None` default and only `latent` changes type; verify `Bindings.from_json` on a pre-V4 JSON blob still works (test below).
  - `GenerationParams` gains `ref_images: List[str] = field(default_factory=list)`, `first_frame: Optional[str] = None`, `last_frame: Optional[str] = None`, `audio_refs: List[str] = field(default_factory=list)`, `duration_s: Optional[float] = None` (all after the existing optionals; `from_json` on old blobs keeps working via defaults).
  - `resolve_bindings(workflow, "ref2v")` resolves every optional title present.
  - `apply_overrides` additions (each raising `BindingError` with an actionable message when supplied-without-binding, matching the existing style):
    - `latent` writes become conditional: `if bindings.latent is not None: write(width/height/batch_size)`.
    - `params.ref_images`: slots = `[b for b in (bindings.ref_image, bindings.ref_image_2, bindings.ref_image_3) if b is not None]`; `len(ref_images) > len(slots)` → `BindingError(f"{len(ref_images)} reference images supplied but the workflow has only {len(slots)} MS_REF_IMAGE slot(s)")`; else write each in order to widget `"image"`. (`params.ref_image` singular keeps its existing behavior for `ref` presets; supplying both singular and list is a `BindingError`.)
    - `first_frame`/`last_frame` → widget `"image"` on their bindings.
    - `audio_refs`: same slot pattern over `(bindings.audio, bindings.audio_2)`, widget `"audio"`.
    - `duration_s` → widget `"value"` on `bindings.duration`.

- [ ] **Step 1: Write the failing tests** — cases (concrete minimal workflow dicts with `_meta.title` + `inputs`, following `tests/test_comfy_bindings.py`'s fixtures — read that file first):

```python
def test_ref2v_requires_only_positive_seed_save(): ...
    # workflow with just those three -> resolves; latent is None
def test_ref2v_resolves_all_optional_titles(): ...
def test_t2i_still_requires_latent(): ...
def test_bindings_from_json_pre_v4_blob(): ...
    # json without the new keys -> loads, new fields None
def test_apply_overrides_ref_images_in_order(): ...
def test_apply_overrides_too_many_refs_raises(): ...
def test_apply_overrides_ref_images_and_singular_ref_image_conflict(): ...
def test_apply_overrides_first_frame_and_audio_and_duration(): ...
def test_apply_overrides_param_without_binding_raises_each(): ...
    # parametrized over first_frame/last_frame/audio_refs/duration_s
def test_apply_overrides_no_latent_skips_dimension_writes(): ...
```

(BINDING case list — write each with real workflow dicts and exact assertions on the returned graph's `inputs`.)

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — new file + `tests/test_comfy_bindings.py tests/test_comfy_client.py tests/test_comfy_api.py` → all PASS (no regression to t2i/ref).
- [ ] **Step 5: Quality gate + commit**

```bash
git add metascan/core/comfy_bindings.py tests/test_comfy_bindings_ref2v.py
git commit -m "feat(comfy): ref2v binding kind with multi-ref/keyframe/audio/duration params"
```

---

### Task 3: `ComfyClient` — `upload_file` + video-aware output collection

**Files:**
- Modify: `metascan/core/comfy_client.py` (`upload_image` ~line 357-394; `collect_outputs` ~line 1011-1078; `_download_image` ~line 996)
- Modify: `tests/_fake_comfy_server.py` (video-output mode)
- Test: `tests/test_comfy_video_outputs.py` (create)

**Interfaces:**
- Consumes: existing SHA-256 `_upload_cache`, `/upload/image` multipart, `_await_history`, `bindings.save`, `Scanner.SUPPORTED_EXTENSIONS` (scanner.py:52-63 — images + `.mp4/.webm/.mov`).
- Produces:
  - `async def upload_file(self, path: Path) -> str` — generalizes `upload_image`: same content-hash cache and `/upload/image` endpoint (ComfyUI stores any input file), MIME from suffix via `mimetypes.guess_type` (fallback `application/octet-stream`), preserves the real suffix in the uploaded name (`metascan_{digest[:16]}{suffix}`). `upload_image = upload_file` (kept as an alias or thin wrapper — callers unchanged).
  - `collect_outputs` iterates the save node's output entry across keys `("images", "gifs", "videos", "video", "audio")`, collecting every element that is a dict carrying `"filename"`; downloads each via the renamed `_download_output` (identical `/view` logic); files whose suffix is NOT in `Scanner.SUPPORTED_EXTENSIONS` are still downloaded to the output dir but excluded from the returned/ingested list (log at INFO naming them). Ingest (`scanner.ingest_file`) and the `job_outputs` payload cover only the supported files.
  - Fake server: a per-test switch (e.g. `server.video_mode = True` or a constructor/prompt flag — follow the file's existing configuration style) making the history entry's save-node output `{"gifs": [{"filename": "clip_00001.mp4", "subfolder": "", "type": "output"}]}` (plus optionally a sidecar `{"videos": [...]}` variant) and serving those bytes on `/view`.

- [ ] **Step 1: Write the failing tests** — read `tests/test_comfy_client.py`'s fake-server usage first; cases (binding):

```python
async def test_upload_file_mime_and_cache(): ...
    # .wav uploads once for identical bytes (cache hit), name keeps .wav
async def test_collect_outputs_gifs_key_mp4_ingested(): ...
    # fake server video mode -> file downloaded, returned, scanner.ingest_file called
async def test_collect_outputs_mixed_keys_and_sidecar_filtered(): ...
    # images + videos + a .json sidecar entry -> sidecar downloaded but not returned
async def test_collect_outputs_images_only_unchanged(): ...
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — new file + `tests/test_comfy_client.py tests/test_fake_comfy_server.py tests/test_comfy_api.py` → all PASS.
- [ ] **Step 5: Quality gate + commit**

```bash
git add metascan/core/comfy_client.py tests/_fake_comfy_server.py tests/test_comfy_video_outputs.py
git commit -m "feat(comfy): generic input upload + video-aware output collection"
```

---

### Task 4: Compiler — `<Audio N>` voice references + compiled-anchor recording

**Files:**
- Modify: `metascan/core/h3_compiler.py` (RefPlan + new renderers)
- Modify: `metascan/core/storyboard_runner.py` (`_compile_panel` + the per-panel DB write in `_compile_locked`)
- Test: `tests/test_h3_compiler.py` + `tests/test_storyboard_compile.py` (extend)

**Interfaces:**
- Consumes: `RefPlan` (frozen dataclass), `SpeakerPlan`, `assign_reference_labels(subjects, scene)`, the existing subject-definition/retention renderers; format authority `data/prompt_guides/minimax-h3/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md` §2.4 (`<Audio 1> is the voice-timbre reference for <Subject 1> (S1).`) and §4.2 (`<Audio 2>: reference - the target speaker follows <Audio 2>'s voice timbre ... without copying the original signal.`).
- Produces:
  - `RefPlan` gains `audio_labels: Tuple[Tuple[int, str], ...] = ()` — `(subject_id, "Audio N")`, numbered 1.. in subject `sort_order`, assigned ONLY for subjects with a truthy `voice_ref_path`. (`assign_reference_labels` reads `s.get("voice_ref_path")`; default keeps every existing test passing.)
  - `render_audio_definition_lines(refplan: RefPlan, speakers: SpeakerPlan, subjects) -> List[str]` — one line per audio label whose subject actually SPEAKS in this panel (its subject label appears in `speakers.lines`): `"<Audio 1> is the voice-timbre reference for <Subject 2> (S1)."` (speaker id from the plan). Non-speaking subjects' audio labels yield no line AND their `(subject_id, label)` should be excluded from what the runner uploads — expose `def active_audio_refs(refplan, speakers, subjects) -> List[Tuple[str, str]]` returning `[(voice_ref_path, audio_label), ...]` for speaking subjects only, in label order (the runner's upload order).
  - `render_audio_retention_lines(refplan, speakers, subjects) -> List[str]` — `"<Audio 1>: reference - the target speaker follows <Audio 1>'s voice timbre and delivery without copying the original signal."` for the same active set.
  - `_compile_panel` appends the definition lines to the `subject_definitions` section and the retention lines to `retention_analysis` (each newline-joined after the existing content, only when non-empty).
  - The per-panel success write in `_compile_locked` adds `video_compiled_anchor=panel.get("video_anchor")` (records what anchor the prompt was compiled against; the exception path leaves it untouched).
  - Lint: `<Audio N>` spans are invisible to `unknown_label` (its regex matches only Subject|Picture) — leave lint unchanged; note this in the module docstring where audio labels are introduced.

- [ ] **Step 1: Write the failing tests** (binding cases; concrete fixtures):

```python
def test_audio_labels_assigned_only_for_voice_ref_subjects(): ...
def test_audio_definition_and_retention_for_speaking_subject_only(): ...
    # subject A (voice_ref, speaks) -> lines present with A's (Sx);
    # subject B (voice_ref, silent) -> no lines, excluded from active_audio_refs
def test_active_audio_refs_order_and_paths(): ...
def test_compile_appends_audio_sections_and_records_anchor(): ...
    # runner-level (test_storyboard_compile.py): board with a voice_ref
    # speaking subject + panel.video_anchor="keeper" -> compiled doc
    # contains "<Audio 1> is the voice-timbre reference", retention line,
    # and db panel.video_compiled_anchor == "keeper"
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — `tests/test_h3_compiler.py tests/test_storyboard_compile.py` → all PASS.
- [ ] **Step 5: Quality gate + commit**

```bash
git add metascan/core/h3_compiler.py metascan/core/storyboard_runner.py tests/test_h3_compiler.py tests/test_storyboard_compile.py
git commit -m "feat(storyboard): H3 audio voice references + compiled-anchor recording"
```

---

### Task 5: FFmpeg last-frame helper

**Files:**
- Modify: `metascan/utils/ffmpeg_utils.py`
- Test: `tests/test_ffmpeg_last_frame.py` (create)

**Interfaces:**
- Consumes: `get_ffmpeg_path()` — read `metascan/utils/ffmpeg_utils.py` first; if the path-finder lives only in `metascan/cache/thumbnail.py:66`, import it from there (do not duplicate it).
- Produces: `def extract_last_frame(video_path: Path, out_png: Path) -> None` — runs `ffmpeg -y -sseof -0.5 -i <video> -update 1 -frames:v 1 <out_png>` via `subprocess.run(capture_output=True)`; raises `RuntimeError` naming the stderr tail (last ~300 chars) on nonzero exit or missing ffmpeg; creates parent dirs.

- [ ] **Step 1: Write the failing test** — skip-if-missing per existing FFmpeg-test precedent (grep tests/ for how FFmpeg availability gates are written and mirror):

```python
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_extract_last_frame_produces_png(tmp_path):
    # generate a 1-second test clip with ffmpeg itself (color source),
    # then extract and assert the PNG exists and is non-empty
    ...
def test_extract_last_frame_bad_input_raises(tmp_path):
    with pytest.raises(RuntimeError):
        extract_last_frame(tmp_path / "nope.mp4", tmp_path / "o.png")
```

(First test generates its own fixture clip: `ffmpeg -f lavfi -i color=c=red:s=64x64:d=1 out.mp4`.)

- [ ] **Steps 2-4: fail → implement → pass** (`venv/bin/pytest tests/test_ffmpeg_last_frame.py -v`).
- [ ] **Step 5: Quality gate + commit**

```bash
git add metascan/utils/ffmpeg_utils.py tests/test_ffmpeg_last_frame.py
git commit -m "feat(utils): ffmpeg last-frame extraction helper"
```

---

### Task 6: `StoryboardRunner.generate_video`

**Files:**
- Modify: `metascan/core/storyboard_runner.py` (new section after `generate()`; read `generate()` FULLY first — validation/folder/VLM-unload/seed/output_dir idioms are all reused)
- Test: `tests/test_storyboard_videogen.py` (create)

**Interfaces:**
- Consumes: Tasks 1-5; `generate()`'s internals — `panel_seed` + `variant_base = count_panel_images + batch * pending jobs` accounting, `db.list_generation_jobs(limit=10000)`, `latest_jobs_for_panels` for `only_failed`, `storyboard_slug` + `<output_root>/<slug>/scene_NN/panel_NN/` output-dir scheme, the `unload_vlm_during_generation` block under `_synth_lock`, lazy folder creation; `comfy.upload_file`, `comfy.submit(preset_id, params, panel_id=..., priority=False, output_dir=...)`; `resolve_bindings(json.loads(preset["workflow_json"]), "ref2v")` for slot arithmetic; `h3.assign_reference_labels` + `h3.active_audio_refs` (upload ordering); `extract_last_frame`; scratchpad-safe temp dir via `tempfile.mkdtemp` for extracted frames.
- Produces:
  - `async def generate_video(self, storyboard_id: int, panel_ids: Optional[List[int]] = None, only_failed: bool = False) -> Dict[str, Any]` — returns `{"jobs": List[int], "skipped": List[Dict[str, Any]]}` where each skipped entry is `{"panel_id": int, "error": str}` (per-panel submit-time failures such as frame extraction — see below).
  - **Upfront validation, ALL failures collected then raised as one `StoryboardError` whose message joins every `"panel {id}: {reason}"` line** (spec §8): storyboard exists; `video_target == "minimax"`; `video_preset_id` set, preset exists, `kind == "ref2v"`; per target panel: non-empty `video_prompt`; picture count (from `assign_reference_labels` over the SAME union-subjects list `_compile_panel` uses — factor that union into a small shared helper `_panel_subjects(tree, panel)` so compile and generate can never disagree) ≤ available ref slots in the preset bindings; active audio count ≤ audio slots; each active `voice_ref_path` exists on disk; mode ∈ {i2va, fl2va} requires `video_anchor` set; `video_anchor == "keeper"` requires a selected still image (selected_image_id resolves to a non-video file); `"prev_last"` requires a previous panel (board order: scene sort then panel sort) whose selected image is a video file. Anchor≠compiled-anchor mismatch is NOT a validation failure (visible in UI only).
  - VLM unload under `_synth_lock` exactly as `generate()` does.
  - Per panel (sequential submit loop like `generate()`): uploads via `comfy.upload_file` — subject/scene pictures in RefPlan `picture_labels` order → `ref_images`; active audio paths → `audio_refs`; anchor: `"keeper"` uploads the selected image's native path → `first_frame`; `"prev_last"` runs `extract_last_frame(prev_video_native_path, tmp/frame.png)` then uploads → `first_frame` — an extraction/upload failure here skips THAT panel: append `{"panel_id", "error"}` to the returned `skipped` list, log at WARNING, continue the loop (the upfront gate already caught everything statically checkable; these are the only runtime-only failures).
  - `GenerationParams(positive=panel["video_prompt"], seed=panel_seed(base_seed, sort_order, variant_base), width=w, height=h, batch_size=1, ref_images=..., first_frame=..., audio_refs=..., duration_s=panel.get("duration_s"))` with `(w, h) = bucket_dims(tree["aspect_ratio"], tree["target_model"])` (harmless when the workflow has no latent — `apply_overrides` skips dimension writes).
  - `submit(video_preset_id, params, panel_id=panel["id"], output_dir=...)`; ingest/WS entirely via the existing `handle_job_event → _ingest_outputs` path (no changes).

- [ ] **Step 1: Write the failing tests** (fake Comfy object recording `upload_file`/`submit` calls; real temp DB; binding cases):

```python
async def test_validation_lists_all_failures(): ...
    # missing prompt on panel A + keeper-without-selection on panel B ->
    # one StoryboardError naming both panel ids
async def test_ref_slot_arithmetic_validated_upfront(): ...
async def test_uploads_follow_refplan_order_and_params_shape(): ...
    # 2 subject refs + scene ref + 1 active audio -> upload_file called in
    # picture order then audio; params.ref_images order matches; seed ==
    # panel_seed(base, sort_order, images + 1*pending)
async def test_keeper_anchor_uploads_selected_still(): ...
async def test_prev_last_uses_extracted_frame(monkeypatch): ...
    # monkeypatch extract_last_frame to write a stub png; assert called
    # with the previous panel's selected video path
async def test_only_failed_filters(): ...
async def test_extraction_failure_isolates_panel(monkeypatch): ...
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — new file + `tests/test_storyboard_runner.py tests/test_storyboard_compile.py` → all PASS; full gate once.
- [ ] **Step 5: Commit**

```bash
git add metascan/core/storyboard_runner.py tests/test_storyboard_videogen.py
git commit -m "feat(storyboard): generate_video — validated ref2v submits through ComfyClient"
```

---

### Task 7: API route + PATCH extensions

**Files:**
- Modify: `backend/api/storyboard.py`
- Test: `tests/test_storyboard_videogen_api.py` (create)

**Interfaces:**
- Consumes: Task 6 `generate_video`; existing `/generate` route (read it — error mapping `StoryboardError`→400, `ComfyError`→503, `_require_runner`) and `/compile`'s validation idioms.
- Produces:
  - `POST /api/storyboard/{id}/generate-video` — body `{panel_ids?: [int], only_failed?: bool}` → `{"jobs": [int], "skipped": [{"panel_id": int, "error": str}]}` (passes the runner's return through); `StoryboardError` → 400 (multi-line detail passes through verbatim); `ComfyError` → 503; 503 no runner. Synchronous await (mirrors `/generate`, not the 202 pattern — jobs are queued, not long-running in-request).
  - `StoryboardPatch` += `video_preset_id: Optional[int] = None` (nullable-clearable; unknown preset id → the DB FK raises `sqlite3.IntegrityError` — map to 400 like `InvalidReferenceError` does, or pre-check via `ComfyService.get_preset`; mirror whichever pattern `preset_id` (stills) uses today — read its handler).
  - `PanelPatch` += `video_anchor: Optional[str] = None` — non-null must be `"keeper"` or `"prev_last"` (400 otherwise); nullable-clearable.
  - `SubjectCreate`/`SubjectPatch` += `voice_ref_path: Optional[str] = None` (nullable, no validation — plain path).

- [ ] **Step 1: Write the failing tests** (reuse `tests/test_storyboard_api.py` fixtures; stub runner gains `async generate_video(...)`):

```python
def test_generate_video_returns_jobs(): ...
def test_generate_video_400_maps_storyboard_error_detail(): ...
def test_patch_storyboard_video_preset_id_set_and_clear(): ...
def test_patch_panel_video_anchor_validation(): ...
    # "keeper" ok, "bogus" 400, null clears
def test_patch_subject_voice_ref_path(): ...
```

- [ ] **Steps 2-4: fail → implement → pass** (new file + `tests/test_storyboard_api.py tests/test_storyboard_compile_api.py`); full gate once.
- [ ] **Step 5: Commit**

```bash
git add backend/api/storyboard.py tests/test_storyboard_videogen_api.py
git commit -m "feat(storyboard): generate-video route + video preset/anchor/voice-ref PATCH"
```

---

### Task 8: Frontend

**Files:**
- Modify: `frontend/src/types/storyboard.ts`, `frontend/src/api/storyboard.ts`, `frontend/src/stores/storyboard.ts`
- Modify: `frontend/src/components/storyboard/PresetRegistrationDialog.vue`, `StoryboardSettingsDialog.vue`, `PanelSidePanel.vue`, `PanelDetail.vue`
- Modify: `frontend/src/views/StoryboardView.vue`

**Interfaces (each bullet is a requirement; read each file first):**
- Types: `StoryboardSummary` += `video_preset_id: number | null`; `Panel` += `video_anchor: string | null`, `video_compiled_anchor: string | null`; `Subject` += `voice_ref_path: string | null`; `VIDEO_ANCHORS = ['keeper', 'prev_last'] as const`.
- API: `generateVideoStoryboard(id, body: {panel_ids?: number[]; only_failed?: boolean}): Promise<{jobs: number[]; skipped: {panel_id: number; error: string}[]}>`; patch bodies gain the three new fields.
- Store: `generateVideo(panelIds?, onlyFailed?)` action — mirrors the existing `generate()` action (call API then `refreshActiveJobs()`), returns the response so callers can surface `skipped` entries (StoryboardView/side-panel show them via the existing error-surfacing mechanism); NO new WS handling (video jobs are ordinary `generation_jobs` → the existing `comfy` channel `jobToPanel` overlays and `panel_images_changed` refresh already cover them).
- **PresetRegistrationDialog**: `ref2v` added to the kind select (label "Video (ref2v)").
- **StoryboardSettingsDialog**: "Video workflow preset" select listing `kind === 'ref2v'` presets (mirror the existing stills preset select incl. its None/null-diff handling), saved as `video_preset_id`; per-subject "Voice ref (audio path)" text input committing `{ voice_ref_path: value || null }`.
- **PanelSidePanel** (video-prompt section): an "Anchor" select (None / "First frame from keeper" / "Continue from previous shot") committing `{ video_anchor: value || null }` via `patchPanelFields`; a "recompile suggested" amber chip when `panel.video_anchor !== panel.video_compiled_anchor`; a "Render video" button — enabled when `tree.video_preset_id` and `panel.video_prompt`, disabled while that panel has an active job (`store.panelJobState.get(panel.id)`) — calling `store.generateVideo([panel.id])` with errors surfaced like the Compile button's.
- **StoryboardView**: "Generate video" header action (visible when `video_target && video_preset_id`; disabled while compile/synthesis/story running) calling `store.generateVideo()`; 400 detail (the multi-line validation list) shown via the existing error-surfacing mechanism (toast or chip — whichever the Generate-all button uses).
- **PanelDetail** (`viewerMedia` computed, ~line 353-368): synthesized `Media` objects set `is_video: true` when the file extension is `.mp4/.webm/.mov` (share a tiny `isVideoPath(path)` helper in `utils/` if one doesn't exist) so `MediaViewer` routes to `VideoPlayer`; candidate thumbnails need no change (FFmpeg-backed `thumbnailUrl` already handles video).
- Build: `cd frontend && npm run build` clean.

- [ ] **Step 1: Types + API + store; build to type-check.**
- [ ] **Step 2: Component edits per the bullets.**
- [ ] **Step 3: Build check** — clean.
- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/storyboard.ts frontend/src/api/storyboard.ts frontend/src/stores/storyboard.ts frontend/src/components/storyboard/PresetRegistrationDialog.vue frontend/src/components/storyboard/StoryboardSettingsDialog.vue frontend/src/components/storyboard/PanelSidePanel.vue frontend/src/components/storyboard/PanelDetail.vue frontend/src/views/StoryboardView.vue
git commit -m "feat(storyboard): video generation UI — preset, anchor, render, video review"
```

---

### Task 9: Docs + full verification

**Files:**
- Modify: `CLAUDE.md`, `docs/features.md`

- [ ] **Step 1: CLAUDE.md bullet** (after the H3-compiler bullet; verify every claim against code): `ref2v` preset kind (CHECK rebuilt via the `sqlite_master`-gated procedure) with its required/optional MS_* titles; `Bindings.latent` now Optional (t2i/ref still require it); `upload_file`'s hash-cached any-file uploads; `collect_outputs`' multi-key video collection with sidecar filtering; `generate_video`'s all-failures-upfront validation and RefPlan-shared upload ordering (`_panel_subjects` union helper shared with compile); `panel_images` reused unchanged for clips; `video_anchor`/`video_compiled_anchor` mismatch chip semantics; `<Audio N>` lines emitted only for speaking subjects with `voice_ref_path`; fl2va's second anchor not auto-wired (recorded scope rule).
- [ ] **Step 2: docs/features.md line** — render shots to video via a local ComfyUI H3 workflow; clips land as hidden panel candidates for review/keeper selection/reroll.
- [ ] **Step 3: Full verification** — `make quality test` (worktree: VENV_DIR form) AND `cd frontend && npm run build`; scoped suite: `venv/bin/pytest tests/test_storyboard_videogen_db.py tests/test_comfy_bindings_ref2v.py tests/test_comfy_video_outputs.py tests/test_h3_compiler.py tests/test_storyboard_compile.py tests/test_ffmpeg_last_frame.py tests/test_storyboard_videogen.py tests/test_storyboard_videogen_api.py tests/test_comfy_client.py tests/test_storyboard_api.py -v` → all PASS.
- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/features.md
git commit -m "docs: video generation architecture notes and feature entry"
```
