# Storyboard H3 Prompt Compiler (Phase V3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compile each shot (panel) into a MiniMax H3 six-section video prompt — deterministic compiler for everything mechanical, two bounded Qwen3-VL calls for the prose sections, lint + one retry — stored per panel and surfaced in the side panel's Preview tab.

**Architecture:** A pure `metascan/core/h3_compiler.py` (reference/speaker/timeline planning, section rendering, scaffold, lint) plus a tiny `video_targets.py` (per-target duration caps); `StoryboardRunner.compile_video` mirrors `_synthesize_locked` with `compile_*` WS events; new nullable panel/storyboard columns; a `/compile` route; frontend target+mode settings, compile action, and a video-prompt section in the side panel.

**Tech Stack:** Python 3.11 / FastAPI / SQLite, llama-server GBNF via `VlmClient.generate_text`, Vue 3 + Pinia + TypeScript.

**Spec:** `docs/superpowers/specs/2026-08-15-storyboard-h3-compiler-design.md` — **including §10 Amendments** (video_mode, target+mode UX, per-target caps, side-panel prompt home). Format authority: the vendored MiniMax guides (Task 1 copies them into the repo).

## Global Constraints

- `make quality test` before every commit (worktree form: `make VENV_DIR=/home/jk/gws/metascan/venv quality test`; known WSL2 flake `test_file_watcher_triggers_reload`, full-suite only); frontend tasks also `cd frontend && npm run build`.
- Tests never load a real VLM — fakes only. GBNF: `\-` SIGSEGVs llama-server; grammars in Python via the `{{`/`}}` `.format()` idiom; prompt prose in `data/meta_prompt.yml` with module-`__getattr__` accessors (established pattern).
- All timestamp/duration arithmetic is code-side — the LLM never does math.
- Descriptor text (subject `description`, scene `setting`) is carried **verbatim** into rendered sections — never paraphrased by compiler code.
- V3 accepts `video_target` ∈ {NULL, `'minimax'`} and `video_mode` ∈ {NULL, `'t2va'`, `'i2va'`, `'fl2va'`, `'ref2va'`}; `'ltx'` is reserved (400).
- `panels.video_prompt` PATCH rule: non-null body value forces `video_prompt_locked=1, video_prompt_source='user'` server-side (mirror of the `prompt` rule); explicit null clears prompt+source and resets lock to 0.
- New columns via `_idempotent_add_column`; mypy strict on `metascan/core/*`; never stage `metascan/core/meta_prompt_templates.py`; stage files explicitly.
- The still-image pipeline (`panels.prompt`, `synthesize()`) is untouched.

---

### Task 1: Schema columns, `video_targets.py`, vendored guides

**Files:**
- Modify: `metascan/core/database_sqlite.py` (migration block next to the V1/V2 adds ~line 232-246; `_STORYBOARD_UPDATABLE` / `_PANEL_UPDATABLE` sets)
- Create: `metascan/core/video_targets.py`
- Create: `data/prompt_guides/minimax-h3/VIDEO_PROMPT_WRITING_GUIDE_base_en.md` and `…_ref_en.md` (copied verbatim from `/mnt/c/Users/jtkli/ai/prompts/guides/minimax-h3/`)
- Test: `tests/test_video_targets.py` (create), `tests/test_storyboard_video_db.py` (create)

**Interfaces:**
- Consumes: `_idempotent_add_column`; existing updatable-set idiom.
- Produces:
  - Columns (all nullable TEXT unless noted): `storyboards.video_target`, `storyboards.video_mode`, `panels.video_prompt`, `panels.video_prompt_source`, `panels.video_prompt_warnings`; plus `panels.video_prompt_locked INTEGER NOT NULL DEFAULT 0`.
  - `_STORYBOARD_UPDATABLE` += `{"video_target", "video_mode"}`; `_PANEL_UPDATABLE` += `{"video_prompt", "video_prompt_locked", "video_prompt_source", "video_prompt_warnings"}`.
  - Tree passthrough is automatic (`SELECT *` → `dict(row)`), EXCEPT `video_prompt_warnings` must be JSON-decoded to a list in the tree's panel dicts and in `get_panel` (empty list when NULL) — mirror the `subject_ids` decode there.
  - `video_targets.TARGET_CAPS: dict[str, float] = {"minimax": 15.0}`, `DEFAULT_SHOT_CAP: float = 15.0`, `shot_cap(video_target: Optional[str]) -> float` (lookup with default).
  - Guides vendored at `data/prompt_guides/minimax-h3/` — later tasks cite them as the format authority.

- [ ] **Step 1: Write the failing tests**

`tests/test_video_targets.py`:

```python
"""Per-target shot-duration caps (spec V3 §10.3)."""

from metascan.core.video_targets import DEFAULT_SHOT_CAP, TARGET_CAPS, shot_cap


def test_minimax_cap_is_15():
    assert TARGET_CAPS["minimax"] == 15.0
    assert shot_cap("minimax") == 15.0


def test_unknown_and_none_fall_back_to_default():
    assert shot_cap(None) == DEFAULT_SHOT_CAP == 15.0
    assert shot_cap("ltx") == DEFAULT_SHOT_CAP  # reserved, not yet capped
```

`tests/test_storyboard_video_db.py` (reuse `tests/test_storyboard_db.py`'s fixture verbatim):

```python
"""Video-prompt columns (spec V3 §2)."""

import pytest

from metascan.core.database_sqlite import DatabaseManager


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


@pytest.fixture
def ids(db):
    sb = db.create_storyboard(
        name="B", target_model="sd", architecture="t2i", base_seed=1
    )
    scene = db.create_scene(sb, name="S")
    panel = db.create_panel(scene, action="she runs")
    return sb, panel


def test_storyboard_video_fields_roundtrip(db, ids):
    sb, _ = ids
    db.update_storyboard(sb, video_target="minimax", video_mode="ref2va")
    row = db.get_storyboard(sb)
    assert row["video_target"] == "minimax"
    assert row["video_mode"] == "ref2va"
    db.update_storyboard(sb, video_target=None)
    assert db.get_storyboard(sb)["video_target"] is None


def test_panel_video_prompt_fields_and_warnings_decode(db, ids):
    sb, panel = ids
    db.update_panel(
        panel,
        video_prompt="subject_definitions: ...",
        video_prompt_source="compiled",
        video_prompt_warnings='["word count low"]',
    )
    got = db.get_panel(panel)
    assert got["video_prompt"].startswith("subject_definitions")
    assert got["video_prompt_locked"] == 0
    assert got["video_prompt_warnings"] == ["word count low"]
    tree = db.get_storyboard_tree(sb)
    tp = tree["scenes"][0]["panels"][0]
    assert tp["video_prompt_warnings"] == ["word count low"]


def test_warnings_null_decodes_to_empty_list(db, ids):
    _, panel = ids
    assert db.get_panel(panel)["video_prompt_warnings"] == []
```

- [ ] **Step 2: Run to verify failure** — `venv/bin/pytest tests/test_video_targets.py tests/test_storyboard_video_db.py -v` → FAIL.

- [ ] **Step 3: Implement**

Migration block additions:

```python
        _idempotent_add_column(
            conn, "storyboards", "video_target",
            "ALTER TABLE storyboards ADD COLUMN video_target TEXT",
        )
        _idempotent_add_column(
            conn, "storyboards", "video_mode",
            "ALTER TABLE storyboards ADD COLUMN video_mode TEXT",
        )
        _idempotent_add_column(
            conn, "panels", "video_prompt",
            "ALTER TABLE panels ADD COLUMN video_prompt TEXT",
        )
        _idempotent_add_column(
            conn, "panels", "video_prompt_locked",
            "ALTER TABLE panels ADD COLUMN video_prompt_locked "
            "INTEGER NOT NULL DEFAULT 0",
        )
        _idempotent_add_column(
            conn, "panels", "video_prompt_source",
            "ALTER TABLE panels ADD COLUMN video_prompt_source TEXT",
        )
        _idempotent_add_column(
            conn, "panels", "video_prompt_warnings",
            "ALTER TABLE panels ADD COLUMN video_prompt_warnings TEXT",
        )
```

Extend the two updatable sets per Interfaces. In `get_panel` and the tree's panel loop, decode `video_prompt_warnings` beside the existing `subject_ids` decode:

```python
            try:
                d["video_prompt_warnings"] = _json.loads(
                    d.get("video_prompt_warnings") or "[]"
                )
            except (ValueError, TypeError):
                d["video_prompt_warnings"] = []
```

`metascan/core/video_targets.py`:

```python
"""Per-video-target constants shared by story composition and compilers.

The shot-duration cap is the one deliberate coupling between story
composition and the video dialect (spec V3 §10.3): H3 generates at most
~15 s per clip, so the shots stage and the beats editor take their
duration guidance from here rather than hardcoding.
"""

from __future__ import annotations

from typing import Optional

TARGET_CAPS: dict[str, float] = {"minimax": 15.0}
DEFAULT_SHOT_CAP: float = 15.0


def shot_cap(video_target: Optional[str]) -> float:
    """Max recommended shot duration (seconds) for a video target."""
    if video_target is None:
        return DEFAULT_SHOT_CAP
    return TARGET_CAPS.get(video_target, DEFAULT_SHOT_CAP)
```

Copy both guide files verbatim: `mkdir -p data/prompt_guides/minimax-h3 && cp /mnt/c/Users/jtkli/ai/prompts/guides/minimax-h3/VIDEO_PROMPT_WRITING_GUIDE_base_en.md /mnt/c/Users/jtkli/ai/prompts/guides/minimax-h3/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md data/prompt_guides/minimax-h3/`.

- [ ] **Step 4: Run tests** — the two new files + `tests/test_storyboard_db.py tests/test_storyboard_beats_db.py` → all PASS.

- [ ] **Step 5: Quality gate + commit**

```bash
git add metascan/core/database_sqlite.py metascan/core/video_targets.py data/prompt_guides tests/test_video_targets.py tests/test_storyboard_video_db.py
git commit -m "feat(storyboard): video target/mode/prompt columns, target caps, vendored H3 guides"
```

---

### Task 2: `h3_compiler.py` — plans, timeline, deterministic sections, scaffold

**Files:**
- Create: `metascan/core/h3_compiler.py`
- Test: `tests/test_h3_compiler.py` (create)

**Interfaces:**
- Consumes: nothing runtime — pure module. Format authority: `data/prompt_guides/minimax-h3/*.md` (READ BOTH FULLY before writing a line — every rendered string below follows them).
- Produces (Tasks 3-4 rely on these exact names):

```python
@dataclass(frozen=True)
class RefPlan:
    subject_labels: Dict[int, str]        # subject_id -> "Subject 1"
    environment_label: str                # "Subject K+1" (scene environment)
    picture_labels: List[Tuple[str, str]] # [(posix_path, "Picture 1"), ...] in upload order
    keyframe_picture_label: str           # next free "Picture N" (for i2va/fl2va anchors)

@dataclass(frozen=True)
class SpeakerLine:
    beat_index: int
    line_index: int
    speaker_id: str          # "S1"
    subject_label: Optional[str]  # "<Subject 2>" or None for unnamed voices
    voice: Optional[str]
    delivery: Optional[str]
    language: str
    text: str

@dataclass(frozen=True)
class SpeakerPlan:
    lines: List[SpeakerLine]
    voice_by_id: Dict[str, str]   # "S1" -> establishing voice description

@dataclass(frozen=True)
class TimelineShot:
    number: int                   # 1-based internal [Shot N]
    start_s: float                # 0.0 for shot 1
    beat_indices: List[int]

@dataclass(frozen=True)
class Timeline:
    shots: List[TimelineShot]
    duration_s: float
    alignment_line: Optional[str] # None for t2va/ref2va

def assign_reference_labels(subjects: Sequence[Mapping[str, Any]], scene: Mapping[str, Any]) -> RefPlan
def assign_speakers(beats: Sequence[Mapping[str, Any]], subjects: Sequence[Mapping[str, Any]], refplan: RefPlan) -> SpeakerPlan
def compute_timeline(beats: Sequence[Mapping[str, Any]], duration_s: float, mode: str, refplan: RefPlan) -> Timeline
def format_timecode(seconds: float) -> str          # "MM:SS.mmm", e.g. "00:03.500"
def render_camera(motion: Optional[str], amplitude: Optional[str], speed: Optional[str]) -> Optional[str]
def render_subject_definitions(refplan: RefPlan, subjects, scene) -> str
def render_summary(refplan: RefPlan, panel, subjects, mode: str) -> str
def render_retention_analysis(refplan: RefPlan, subjects, scene, timeline: Timeline) -> str
def build_scaffold(panel, scene, storyboard, subjects, refplan, speakers, timeline) -> str
def assemble(alignment_line, subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music) -> str
```

**Rendering rules (from the vendored guides — cite the guide section when reviewing):**
- Subjects in `sort_order` become `<Subject 1>..<Subject K>`; the scene environment is always `<Subject K+1>` defined from the scene's `setting` text (ref-guide §2.1 makes environments Subjects). Pictures: each subject's `reference_path` then `reference_path_2` in subject order, then `scene["reference_path"]`, numbered `<Picture 1>..`; `keyframe_picture_label` is the next free index.
- `render_subject_definitions`: one line per subject — with pictures: `<Subject 1> is the {name} in <Picture 1> and <Picture 2>, {description verbatim}.`; without: `<Subject 1> is the {name}: {description verbatim}.`; environment line: `<Subject K+1> is the {scene name} environment: {setting verbatim}.` (fall back to scene `location` or name when `setting` is empty).
- `render_summary`: `[reference generation]` prefix, `+ keyframe completion` appended when mode ∈ {`i2va`, `fl2va`}; one sentence: `The target video shows {subject labels joined} in <Subject K+1>: {panel action}.`
- `render_retention_analysis`: for every Subject label, `"{label} (appears in {[Shot n] list}): fully_preserved - {first ~12 words of its descriptor}."`; a standalone picture entry ONLY for the keyframe modes: `"{keyframe_picture_label} ([Shot 1] first frame): fully_preserved - the shot begins from this frame."` (i2va) / analogous last-frame wording for fl2va. Environment appears in all shots.
- `compute_timeline`: rescale beat durations proportionally so they sum EXACTLY to `duration_s`; group beats into internal shots starting a new group at each beat with `is_cut == 1` (beat 0 always starts shot 1); `start_s` = cumulative sum; alignment lines (base-guide §2.1 wording, verbatim): i2va → `For the target video, at 0.00 seconds into the target video, <{kf}> (from [Shot 1]) is fully referenced.`; fl2va → `How the reference pictures align with the target video — {kf} (from Shot 1) aligns with the 0.00-second mark of the target video; {kf2} (from Shot {last}) aligns with the {duration:.2f}-second mark of the target video.` where `kf2` is the index after `kf` (fl2va consumes two anchor pictures); t2va/ref2va → None.
- `render_camera`: `"{motion phrase}, {amplitude} amplitude, {speed}"` dropping absent parts; motion phrases use the base-guide §4.3 vocabulary (`push in`, `truck left`, `static shot`, `POV`, …) — map every one of the 20 stored enum values.
- `build_scaffold`: machine-readable brief for the LLM. Format:

```
STYLE: {storyboard.style_block or "cinematic, live-action"}
SUBJECTS PRESENT: <Subject 1> {name} — {description}; ...
DURATION: {duration_s:.1f}s
[Shot 1] starts 00:00.000
- action: {beat action}
- camera: {render_camera(...) or "unspecified"}
- dialog: <Subject 2> (S1) [whispered] (English): "Hello, old girl."
- sound: {beat sound}
[Shot 2] At {format_timecode(start_s)}, cut.
- ...
```

One `- action/camera/dialog/sound` group per beat (omit absent camera/dialog/sound lines); dialog lines use SpeakerPlan (unnamed speakers render as `the {voice} (S2)`).
- `assemble`: joins the document in guide order, section headers exactly `subject_definitions:`, `summary:`, `retention_analysis:`, `detailed_description:`, `overall_soundscape:`, `non_diegetic_music:`, each section separated by a blank line; when `alignment_line` is not None it goes first, followed by a blank line.
- `assign_speakers`: iterate beats/lines in order; key = `subject_id` when set else the normalized voice string (same key ⇒ same `(Sx)`); IDs assigned in first-vocal-event order (ref-guide §5.4); `voice_by_id` prefers the subject's `voice` column, else the line's `voice`, else `"a voice"`.

- [ ] **Step 1: Write the failing tests** — cover, with concrete fixture dicts (subjects/scene/panel/beats shaped exactly like the DB rows: subjects have `id,name,description,voice,reference_path,reference_path_2,sort_order`; beats have `duration_s,action,camera_motion,camera_amplitude,camera_speed,is_cut,dialog,sound`):

```python
def test_refplan_numbers_subjects_pictures_and_keyframe(): ...
    # 2 subjects (first with 2 refs, second with none) + scene ref:
    # subject_labels {7:"Subject 1", 9:"Subject 2"}, environment "Subject 3",
    # pictures [(p1,"Picture 1"),(p2,"Picture 2"),(scene,"Picture 3")],
    # keyframe "Picture 4"

def test_timeline_rescales_groups_and_formats(): ...
    # beats 2/2/2s with is_cut on beat 2, duration_s=12 ->
    # shots [1: beats 0-1 start 0.0][2: beat 2 start 8.0], timecode "00:08.000"

def test_alignment_lines_per_mode(): ...
    # i2va contains "at 0.00 seconds" + keyframe label; fl2va contains both
    # marks incl. f"{12.00:.2f}"; ref2va/t2va -> None

def test_assign_speakers_stable_ids_and_voice_fallbacks(): ...
    # subject line -> S1 reused across beats; unnamed "gravelly voice" -> S2;
    # same unnamed voice string later -> still S2

def test_render_camera_full_partial_none(): ...
def test_subject_definitions_verbatim_and_env(): ...
    # description text appears verbatim; env line uses setting
def test_summary_prefix_modes(): ...
def test_retention_lines_and_keyframe_entry(): ...
def test_scaffold_contains_beats_dialog_and_timecodes(): ...
def test_assemble_order_and_alignment_first(): ...
```

Write each with real assertions (substring + exact-value checks); ~10 tests minimum.

- [ ] **Step 2: Run to verify failure** — module missing.
- [ ] **Step 3: Implement** `metascan/core/h3_compiler.py` per the rules above (module docstring cites the vendored guides; pure — imports only `dataclasses`/`typing`/`json`; the camera-phrase map lives here, keyed by the same 20 enum values as `storyboard_story.CAMERA_MOTION_VALUES` — import that tuple and raise a module-load `AssertionError` if the map's keys don't equal it, so vocabulary drift is impossible).
- [ ] **Step 4: Run tests** — all PASS.
- [ ] **Step 5: Quality gate + commit**

```bash
git add metascan/core/h3_compiler.py tests/test_h3_compiler.py
git commit -m "feat(storyboard): H3 compiler — plans, timeline, deterministic sections, scaffold"
```

---

### Task 3: `h3_compiler.py` — lint

**Files:**
- Modify: `metascan/core/h3_compiler.py`
- Test: `tests/test_h3_compiler.py` (extend)

**Interfaces:**
- Consumes: Task 2's dataclasses.
- Produces:

```python
@dataclass(frozen=True)
class LintError:
    code: str        # e.g. "missing_section", "timestamp_order", "dialog_missing"
    severity: str    # "error" | "warning"
    message: str

@dataclass(frozen=True)
class LintExpectations:
    subject_labels: FrozenSet[str]     # {"Subject 1", ...} incl. environment
    picture_labels: FrozenSet[str]     # incl. keyframe label(s) when mode uses them
    dialog_lines: Tuple[SpeakerLine, ...]
    timeline: Timeline
    duration_s: float

def build_expectations(refplan, speakers, timeline, mode) -> LintExpectations
def lint_h3_prompt(text: str, expect: LintExpectations) -> List[LintError]
```

**Lint rules (spec §3.3, exact severities):**
1. `missing_section` (error): each of the six `^\w+:$`-style headers present, in order.
2. `timestamp_order` (error): `[Shot 1]` in detailed_description has no `At MM:SS` timestamp; each later `[Shot N] At MM:SS.mmm` strictly increasing and within `duration_s + 0.5`; timestamps within ±0.5 s of `expect.timeline`'s prescribed starts.
3. `unknown_label` (error): every `<Subject N>`/`<Picture N>` occurring in the text is in the expectation sets.
4. `dialog_missing` / `dialog_mutated` (error): every expected dialog line's exact `text` appears inside a `<d>[{language}] ... </d>` span exactly once, adjacent (same line) to its `({speaker_id})`; `dialog_invented` (error): a `<d>` span whose inner text matches no expected line.
5. `camera_vocab` (warning): free-text camera phrasing check — flag `detailed_description` sentences containing "zoom"/"pan"/"truck"/"tilt"/"push"/"pull" that contradict an explicitly-set beat camera (hard error only when the beat's motion word is absent AND a conflicting motion word from the vocabulary is present in that shot's text — keep the check shot-scoped and conservative).
6. `word_count` (warning outside 350–500; error below 150) counted on the `detailed_description` body only.
7. `retention_marker` (error): retention_analysis lines use only `fully_preserved|partially_preserved|attribute_transfer|weak_reference`; `soundscape_missing` (error): `overall_soundscape` non-empty; `music_missing` (error): `non_diegetic_music` non-empty (`N/A` allowed).

- [ ] **Step 1: Write the failing tests** — build one known-good document via Task 2's renderers + a hand-written valid body, assert `lint == []`; then one test per rule mutating that document to violate exactly that rule and asserting the code + severity surfaces (≥ 10 new tests; include the boundary cases: word count 150/350/500, timestamp exactly at prescribed+0.5).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** (regex-based, module stays pure; compile regexes at module level).
- [ ] **Step 4: Run** — all PASS (`tests/test_h3_compiler.py` full file).
- [ ] **Step 5: Quality gate + commit**

```bash
git add metascan/core/h3_compiler.py tests/test_h3_compiler.py
git commit -m "feat(storyboard): H3 prompt lint with expectation-driven rules"
```

---

### Task 4: LLM stages + `StoryboardRunner.compile_video` + shots-cap coupling

**Files:**
- Modify: `metascan/core/h3_compiler.py` (add `SOUND_GRAMMAR`, `validate_sound_response`, prompt-key `__getattr__` for `H3_BODY_SYSTEM`/`H3_SOUND_SYSTEM`, `build_body_user_prompt(scaffold: str) -> str`, `build_retry_user_prompt(scaffold: str, errors: List[LintError]) -> str`, `build_sound_user_prompt(beats, tone: Optional[str]) -> str`)
- Modify: `data/meta_prompt.yml` (two keys)
- Modify: `metascan/core/storyboard_runner.py` (new `compile_video` section; shots stage passes the cap)
- Modify: `metascan/core/storyboard_story.py` (`build_shots_user_prompt` gains `max_shot_s: float = 15.0`, appended guidance line `f"Each shot must be at most {max_shot_s:.0f} seconds long."`; the `STORY_SHOTS_SYSTEM` "5-15 seconds" prose is left as-is — the user-prompt line is the per-target override)
- Test: `tests/test_storyboard_compile.py` (create), `tests/test_storyboard_story.py` (extend: one test that `build_shots_user_prompt(..., max_shot_s=10.0)` contains "at most 10 seconds")

**Interfaces:**
- Consumes: Tasks 1-3; runner internals (`_synth_lock`, `_emit`, `get_vlm`, `_pick_vlm_model`, `REGISTRY[...].parallel_slots` semaphore idiom — all as in `_synthesize_locked`); `video_targets.shot_cap`.
- Produces:
  - `StoryboardRunner.compile_video(storyboard_id: int, panel_ids: Optional[List[int]] = None, force: bool = False, deterministic_only: bool = False) -> Dict[str, int]` returning `{"compiled": n, "failed": n, "skipped_locked": n}`.
  - WS events (`storyboard` channel): `compile_progress {storyboard_id, panel_id, done, total}`, `compile_complete {storyboard_id, compiled, failed, skipped_locked}`, `compile_error {storyboard_id, error}` — exactly one terminal event, error re-raised (the `synthesize` contract; on error stamp nothing — compile has no stages).
  - Behavior: raises `StoryboardError` when `video_target` is not `'minimax'` or (no VLM and not `deterministic_only`); skips `video_prompt_locked` panels unless their id is explicitly in `panel_ids` AND `force`; panels with no beats use a single-shot scaffold synthesized from `action`/`duration_s` (one beat-equivalent group); per-panel writes `db.update_panel(pid, video_prompt=doc, video_prompt_source="compiled", video_prompt_locked=0, video_prompt_warnings=json.dumps([e.message for e in issues]))`.
  - Per panel: body call (`story`-style module attr `h3.H3_BODY_SYSTEM`, user = `build_body_user_prompt(scaffold)`, temperature 0.5, max_tokens 1200, timeout 300, NO grammar) → sound call (`h3.H3_SOUND_SYSTEM`, `build_sound_user_prompt`, grammar `SOUND_GRAMMAR`, temperature 0.4, max_tokens 250, timeout 120) → assemble → lint → if any severity=="error": ONE retry of the body call with `build_retry_user_prompt` → re-assemble/re-lint → store regardless; `failed` counts panels whose final lint still has errors (their errors land in warnings storage). `VlmError`/`TimeoutError`/`RuntimeError` on a panel → that panel `failed` with the message as its single warning; other panels proceed.
  - `validate_sound_response(raw) -> Dict[str, str]` — `{"overall_soundscape": str, "non_diegetic_music": str}` (empty/absent music → `"N/A"`); raises `H3Error(ValueError)` on bad JSON/empty soundscape.
  - `mode` for compilation = `storyboard.video_mode or "ref2va"`.
  - Dialect seam (spec §8): all per-panel compile work funnels through one private `async def _compile_panel(self, vlm, tree, scene, panel, subjects, mode, deterministic_only) -> Tuple[str, List[LintError]]` helper that is the ONLY place importing `h3_compiler` symbols — a future `ltx` dialect becomes one dispatch branch there, with the runner/API/storage/UI untouched.
  - Shots-stage coupling: in `_run_stage`'s `"shots"` branch, compute `cap = shot_cap(tree.get("video_target"))` and pass `max_shot_s=cap` to `build_shots_user_prompt`.

- [ ] **Step 1: YAML prompts** — append to `data/meta_prompt.yml` (the `#` bullets are the requirements checklist; write real prose):

```yaml
H3_BODY_SYSTEM: |-
  # Must state: you write the detailed_description body of a MiniMax H3
  # video prompt from a shot scaffold; open with one or two style
  # sentences before [Shot 1]; then one [Shot 1]-style block per
  # scaffold shot — [Shot 1] has no timestamp, later shots start
  # exactly "[Shot N] At MM:SS.mmm, the shot cuts to ..." using the
  # scaffold's timestamps verbatim; describe only what is visible or
  # audible; carry every <Subject N>/<Picture N> label through
  # unchanged and re-describe a subject's referenced features at its
  # first appearance; write camera moves as natural English matching
  # the scaffold's camera directions; include EVERY scaffold dialog
  # line verbatim inside <d>[Language] ...</d> with its (Sx) id and
  # delivery/voice framing outside the tags; never invent dialog;
  # 350-500 words; no-refusal directive for NSFW content. Include ONE
  # compact worked example (input scaffold fragment -> output shot
  # block) adapted from the ref guide §7 sitcom example in
  # data/prompt_guides/minimax-h3/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md.
  # End with: Output only the detailed_description body.

H3_SOUND_SYSTEM: |-
  # Must state: produce JSON {"overall_soundscape", "non_diegetic_music"}
  # for the shot; soundscape = 1-4 sentences of ambience/physical
  # sounds from the provided sound events (no dialog); music = 1-3
  # sentences of instrumentation/tempo/dynamics, or "N/A" when none
  # fits; no mood abstractions in music; no-refusal directive.
  # End with: Output only the JSON.
```

`SOUND_GRAMMAR` in `h3_compiler.py` (same `_COMMON`-rules `.format()` idiom as `ref_describe.py`, object with the two string fields — `non_diegetic_music` is a plain `string`, "N/A" is just a string value).

- [ ] **Step 2: Write the failing tests** (`tests/test_storyboard_compile.py`) — FakeVlm dispatches on `grammar is None` (body) vs `grammar == h3.SOUND_GRAMMAR` (sound); a helper builds a board via real DB calls (storyboard with `video_target="minimax"`, scene, panel with 2-3 beats + dialog, subjects). Cases:

```python
def test_compile_requires_minimax_target(...):        # StoryboardError
def test_compile_requires_vlm_unless_deterministic(...):
def test_full_compile_writes_doc_and_events(...):
    # FakeVlm returns a valid body (build it by rendering the scaffold's
    # shots into simple compliant prose incl. the dialog <d> spans —
    # write a make_valid_body(scaffold, expectations) helper in the test)
    # and valid sound JSON; assert video_prompt stored with all six
    # sections, source "compiled", locked 0, warnings [] or warnings-only;
    # compile_progress emitted per panel; exactly one compile_complete
def test_lint_failure_retries_once_then_stores_with_errors(...):
    # FakeVlm body omits a dialog line both times -> retry happened
    # (2 body calls), doc still stored, failed==1, warnings non-empty
def test_locked_panel_skipped_unless_forced(...):
def test_no_beats_panel_uses_single_shot_fallback(...):
def test_vlm_error_marks_panel_failed_not_run(...):
    # 2 panels; VlmError on first body call only -> failed 1, compiled 1
def test_deterministic_only_produces_doc_without_vlm(...):
```

- [ ] **Step 3: Run to verify failure.**
- [ ] **Step 4: Implement** (runner section after `compose_story`; `import metascan.core.h3_compiler as h3`; semaphore/gather/progress-lock structure copied from `_synthesize_locked`; whole run under `async with self._synth_lock`).
- [ ] **Step 5: Run tests** — new file + `tests/test_storyboard_compose.py tests/test_storyboard_runner.py tests/test_storyboard_story.py` → all PASS; full gate once.
- [ ] **Step 6: Commit**

```bash
git add metascan/core/h3_compiler.py metascan/core/storyboard_runner.py metascan/core/storyboard_story.py data/meta_prompt.yml tests/test_storyboard_compile.py tests/test_storyboard_story.py
git commit -m "feat(storyboard): compile_video runner with H3 body/sound LLM stages and lint retry"
```

---

### Task 5: API routes + PATCH extensions

**Files:**
- Modify: `backend/api/storyboard.py`
- Test: `tests/test_storyboard_compile_api.py` (create)

**Interfaces:**
- Consumes: Task 4 `compile_video`; Task 1 columns; existing route idioms (`_require_runner`, `_background_tasks` + done-callback, `_reject_null_for_required`, the `/synthesize` total-count shape at `backend/api/storyboard.py:376-397`).
- Produces:
  - `POST /api/storyboard/{id}/compile` (202) — body `{panel_ids?: [int], force?: bool}` → `{"status":"started","total":int}` (total counted like `/synthesize`: targeted panels, minus locked ones unless forced — mirror its counting code); 400 `StoryboardError` (wrong/missing target) via a synchronous pre-check: the route loads the storyboard via `_service()` and 400s when `video_target != "minimax"` BEFORE creating the task; 503 no runner.
  - `StoryboardPatch` += `video_target: Optional[str]`, `video_mode: Optional[str]` — route-level validation: non-null `video_target` must be `"minimax"` (400 naming `'ltx' reserved`); non-null `video_mode` must be in `{"t2va","i2va","fl2va","ref2va"}` (400). Both nullable-clearable.
  - `PanelPatch` += `video_prompt: Optional[str]`, `video_prompt_locked: Optional[int]`. Handler rule: body carries non-null `video_prompt` → force `video_prompt_locked=1, video_prompt_source="user"` (server wins, mirror the `prompt` block at ~line 569-573); body carries explicit `video_prompt: null` → set `video_prompt=None, video_prompt_source=None, video_prompt_locked=0, video_prompt_warnings=None`. `video_prompt_locked` alone (the Unlock button) passes through; add `"video_prompt_locked"` to `_PANEL_NOT_NULLABLE`.

- [ ] **Step 1: Write the failing tests** (reuse `tests/test_storyboard_api.py` fixtures; stub runner exposes `async compile_video(...)` recording calls):

```python
def test_compile_202_and_total_counts_unlocked(...)
def test_compile_400_when_target_not_minimax(...)   # NULL and "ltx"-free db value
def test_patch_storyboard_video_target_validation(...)  # "minimax" ok, "ltx" 400, null clears
def test_patch_storyboard_video_mode_validation(...)
def test_patch_panel_video_prompt_locks_server_side(...)
    # send video_prompt + video_prompt_locked: 0 -> row has locked=1, source "user"
def test_patch_panel_video_prompt_null_clears_all(...)
def test_patch_panel_unlock_only(...)                # video_prompt_locked: 0 alone
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — new file + `tests/test_storyboard_api.py tests/test_storyboard_compose_api.py tests/test_storyboard_describe_api.py` → all PASS; full gate once.
- [ ] **Step 5: Commit**

```bash
git add backend/api/storyboard.py tests/test_storyboard_compile_api.py
git commit -m "feat(storyboard): compile route + video target/mode/prompt PATCH contract"
```

---

### Task 6: Frontend — settings, compile action, side-panel video prompt, cap wiring

**Files:**
- Modify: `frontend/src/types/storyboard.ts`, `frontend/src/api/storyboard.ts`, `frontend/src/stores/storyboard.ts`
- Modify: `frontend/src/components/storyboard/StoryboardSettingsDialog.vue`, `PanelSidePanel.vue`, `BeatsEditor.vue`, `OutlineDialog.vue`
- Modify: `frontend/src/views/StoryboardView.vue`

**Interfaces:**
- Consumes: Task 5 routes; existing store patterns (`synthesis`/`story` progress state, WS channel guard, `patchPanelFields` merge semantics — PATCH responses omit `images`/`beats`, and now also carry the new panel fields which merge fine).
- Produces (each is a requirement; read the target file before editing):
  - Types: `StoryboardSummary` (and thus `StoryboardTree`) += `video_target: string | null`, `video_mode: string | null`; `Panel` += `video_prompt: string | null`, `video_prompt_locked: 0 | 1`, `video_prompt_source: 'compiled' | 'user' | null`, `video_prompt_warnings: string[]`; constants `VIDEO_TARGETS = ['minimax'] as const`, `VIDEO_MODES = ['t2va','i2va','fl2va','ref2va'] as const`, `VIDEO_TARGET_CAPS: Record<string, number> = { minimax: 15 }`, `DEFAULT_SHOT_CAP = 15`.
  - API: `compileStoryboard(id, body: {panel_ids?: number[]; force?: boolean}): Promise<{status: string; total: number}>`; `patchStoryboard` body += `video_target?: string | null`, `video_mode?: string | null`; `patchPanel` body += `video_prompt?: string | null`, `video_prompt_locked?: 0 | 1`.
  - Store: `compile` state `{running: boolean; done: number; total: number; error: string | null}` beside `synthesis`; WS `compile_progress`/`compile_complete` (sets running false + `void refresh()`)/`compile_error` handled inside the existing guarded storyboard-channel handler; `compileVideo(panelIds?: number[], force?: boolean)` action (optimistic running=true before await, revert on throw — the `synthesize` pattern).
  - **StoryboardSettingsDialog**: "Video target" select (None/'minimax' labeled "MiniMax H3") and "Video mode" select (None + the four modes, upper-case labels) in the board-fields section, saved through the existing diff-against-original `saveFields` mechanism (nullable diffs must treat null as a real change — check how `preset_id` handles its None option and mirror it).
  - **StoryboardView**: "Compile video prompts" header button (visible when `store.tree?.video_target`), disabled while `store.compile.running || store.synthesis.running || store.story.running`; compile progress chip (`Compiling {done}/{total}`) + error chip, mirroring the existing chips.
  - **OutlineDialog**: one read-only echo line when a target is set — `Video: MiniMax H3 · {mode || 'ref2va'} (change in Settings)` (spec §10.2).
  - **PanelSidePanel** Preview tab: when `store.tree?.video_target` is set, a "Video prompt" section ABOVE the beat-script section: status chip (`compiled`/`user edited`/`—`), lock indicator + "Unlock" button (`patchPanelFields(panel.id, { video_prompt_locked: 0 })`) when locked, warnings list (amber, one line each) when `video_prompt_warnings.length`, a monospace **editable** textarea using the snapshot-resync pattern keyed on `[panel.id, panel.updated_at]` (commit on change → `patchPanelFields(panel.id, { video_prompt: value })`; empty-string commit sends `video_prompt: null`), a per-panel "Compile" button (`compileVideo([panel.id], panel.video_prompt_locked === 1)`), and a copy button. The beat-script preview stays below it. (Spec §10.4: this supersedes any PanelDetail placement.)
  - **BeatsEditor**: replace the hardcoded `> 15` with the cap from `VIDEO_TARGET_CAPS[store.tree?.video_target ?? ''] ?? DEFAULT_SHOT_CAP`, warning text `exceeds {cap}s clip cap`.

- [ ] **Step 1: Types + API + store** (one pass, then `npm run build` to type-check the surface).
- [ ] **Step 2: Dialog/view/side-panel edits** per the Produces list.
- [ ] **Step 3: Build check** — `cd frontend && npm run build` → clean.
- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/storyboard.ts frontend/src/api/storyboard.ts frontend/src/stores/storyboard.ts frontend/src/components/storyboard/StoryboardSettingsDialog.vue frontend/src/components/storyboard/PanelSidePanel.vue frontend/src/components/storyboard/BeatsEditor.vue frontend/src/components/storyboard/OutlineDialog.vue frontend/src/views/StoryboardView.vue
git commit -m "feat(storyboard): video target/mode settings, compile action, side-panel video prompt"
```

---

### Task 7: Docs + full verification

**Files:**
- Modify: `CLAUDE.md`, `docs/features.md`

- [ ] **Step 1: CLAUDE.md bullet** (after the reference-describe bullet; verify every claim against code): the H3 compiler splits work between the pure `h3_compiler.py` (reference/speaker labels, timeline math, deterministic sections, scaffold, expectation-driven lint) and two bounded VLM calls (body prose without grammar — lint+one-retry is the enforcement — and grammar-constrained sound JSON); `compile_video` runs under `_synth_lock` with `compile_*` events and stores failures' raw text + errors in `video_prompt_warnings` rather than losing the draft; the `video_prompt` PATCH server-wins lock rule; `video_target`/`video_mode` validation values; `video_targets.shot_cap` as the single composition↔dialect coupling (shots-stage guidance + beats-editor threshold); guides vendored at `data/prompt_guides/minimax-h3/`.
- [ ] **Step 2: docs/features.md line** — compile shots into MiniMax H3 video prompts (six-section format) with lint warnings, editable in the side panel.
- [ ] **Step 3: Full verification** — `make quality test` (worktree: `VENV_DIR` form) AND `cd frontend && npm run build`; scoped suite: `venv/bin/pytest tests/test_h3_compiler.py tests/test_video_targets.py tests/test_storyboard_video_db.py tests/test_storyboard_compile.py tests/test_storyboard_compile_api.py tests/test_storyboard_story.py tests/test_storyboard_compose.py tests/test_storyboard_api.py -v` → all PASS.
- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/features.md
git commit -m "docs: H3 compiler architecture notes and feature entry"
```
