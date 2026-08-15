# Storyboard H3 Prompt Compiler (Phase V3) — Design

Date: 2026-08-15
Status: Draft — awaiting review
Series: Phase V3 of 4. Depends on V1 (beats, `duration_s`, `voice`) and V2
(descriptor blocks, reference paths). V4 consumes its output.

## 1. Goal

Compile each shot (panel) into a **MiniMax H3 full-reference prompt** — the
six-section format from the official guides (`subject_definitions`,
`summary`, `retention_analysis`, `detailed_description`,
`overall_soundscape`, `non_diegetic_music`) — by combining a deterministic,
model-free **compiler** for everything mechanical with **bounded Qwen3-VL
calls** for the three prose sections, followed by a **lint + one-retry**
pass. The compiled prompt is stored per panel, reviewable and editable, and
is what V4 submits to the H3 ComfyUI workflow.

Source-of-truth format references: the official guides the user provided,
vendored into the repo at `data/prompt_guides/minimax-h3/`
(`VIDEO_PROMPT_WRITING_GUIDE_base_en.md`, `…_ref_en.md`) so implementation
and exemplars never drift from a file outside the tree.

### Non-goals

- LTX-2.5 dialect (a later phase; §8 keeps the seam open).
- Submitting anything to ComfyUI (V4).
- Changing the still-image synthesis path — `panels.prompt` and
  `synthesize()` are untouched; video prompts get their own parallel fields.

## 2. Key design decision — video prompts are parallel to image prompts

A storyboard keeps its existing `target_model` (SDXL/Flux dialect) for the
still-image panel pipeline — stills remain useful as FL2VA keyframe anchors
and for fast composition preview. Video compilation is a second, orthogonal
output:

- `storyboards.video_target TEXT` — nullable; `'minimax'` is the only value
  V3 accepts (`'ltx'` reserved). NULL disables the video UI affordances.
- `panels.video_prompt TEXT`, `panels.video_prompt_locked INTEGER NOT NULL
  DEFAULT 0`, `panels.video_prompt_source TEXT` (`'compiled' | 'user'`) —
  mirroring the `prompt` / `prompt_locked` / `prompt_source` trio and its
  server-wins locking rule (a PATCH carrying `video_prompt` forces
  `video_prompt_locked=1, video_prompt_source='user'`).

All via `_idempotent_add_column`. `Architecture` stays `Literal["t2i"]` in
`meta_prompt_templates.py` — that enum belongs to the *image* meta-prompt
system; the video dialect is selected by `video_target`, not `architecture`.
(The uncommitted `minimax` prefix experiment in `TARGET_PRESETS` is
superseded by this design and can be reverted.)

## 3. The compiler — `metascan/core/h3_compiler.py`

Pure module, no I/O, following `storyboard_brief.py` / `photo_exif.py`
precedent. Everything below is deterministic and unit-testable without a
model.

### 3.1 Reference label assignment

```python
def assign_reference_labels(subjects: Sequence[Mapping], scene: Mapping)
    -> RefPlan  # dataclass
```

Deterministic `<Picture N>` numbering: each subject's `reference_path` then
`reference_path_2` in subject `sort_order`, then the scene's
`reference_path`. `<Subject N>` numbering: subjects in `sort_order`, then
the scene environment as the final Subject (environments are Subjects per
ref-guide §2.1). `RefPlan` maps `subject_id -> subject_label`,
`file_path -> picture_label`, and yields the ordered upload list **V4 uses
verbatim** — one function owns the ordering so prompt text and uploaded
image order can never disagree.

### 3.2 Deterministic sections

- `render_subject_definitions(refplan, subjects, scene) -> str` — one line
  per Subject: `"<Subject 1> is the <name> in <Picture 1> and <Picture 2>,
  <description verbatim>."` (description text is carried **verbatim**, never
  rewrapped); the environment line uses the scene's `setting` text. Subjects
  with no reference image are defined by description alone (legal — the
  label still anchors identity).
- `render_summary(...) -> str` — task-type prefix computed by code:
  `[reference generation]`, `+ keyframe completion` appended when V4 will
  anchor a first frame (a `keyframe: bool` compiler input). One template
  sentence naming the Subjects and shot flow (from panel `action`).
- `render_retention_analysis(...) -> str` — one formulaic line per label:
  `"<Subject N> (appears in [Shot 1], …): fully_preserved - <key features
  from the descriptor>."` Internal shot numbers come from §3.3.
- `compute_timeline(beats, duration_s) -> Timeline` — splits beats into
  internal `[Shot K]` groups at `is_cut` boundaries, allocates strictly
  increasing cut times `At MM:SS.mmm` from cumulative beat durations
  (rescaled to `duration_s` exactly — code-side arithmetic only), and
  formats the FL2VA/I2VA alignment instruction line when a keyframe anchor
  is declared (exact wording from the base guide §2.1, `S.SS` two-decimal).
- `assign_speakers(beats, subjects) -> SpeakerPlan` — global `(S1)`,
  `(S2)`… in order of first vocal event across the panel's dialog lines
  (ref-guide §5.4); maps each dialog line to its ID and carries the
  subject's `voice` description for first-appearance establishment.
- `render_camera(motion, amplitude, speed) -> str` — enum triple → guide
  vocabulary phrase ("pushes in with small amplitude at slow speed"), for
  embedding in the scaffold given to the LLM.
- `build_scaffold(...) -> str` — the machine-readable brief handed to the
  LLM for §4: style opening sentence derived from `style_block`, then per
  internal shot: timestamp, beat actions in order, camera phrases, dialog
  lines **verbatim with language tags and speaker IDs**, sound events.
- `assemble(sections: H3Sections) -> str` — final document in guide order.

### 3.3 Lint — `lint_h3_prompt(text, expectations) -> list[LintError]`

Checked against the compiler's own expectations object (labels, timeline,
dialog lines, duration):

1. All six sections present, in order, correctly headed.
2. `[Shot 1]` has no timestamp; later shots strictly increasing
   `At MM:SS.mmm` within duration (±0.5 s tolerance on the values the
   compiler prescribed).
3. Every `<Subject/Picture N>` used is defined; none invented.
4. Every dialog line appears exactly once, verbatim, inside
   `<d>[Lang] …</d>` with its assigned `(Sx)`; no invented dialog.
5. Camera phrases use only guide vocabulary (soft check — warning, since
   the LLM may phrase motion naturally; hard error only for contradicting
   an explicitly-set beat camera).
6. `detailed_description` word count 350–500 (warning outside, error under
   150 — dialog-dense shots may exceed per the guide).
7. Retention markers are from the fixed set; `overall_soundscape` /
   `non_diegetic_music` present (`N/A` allowed for music).

Errors → retry; warnings → accepted but attached to the result for UI
display.

## 4. LLM stages

Per panel, up to two `VlmClient.generate_text` calls (no images):

1. **Body** — system prompt `H3_BODY_SYSTEM` (rules distilled from the
   guides + one full worked exemplar adapted from the ref-guide §7 example)
   + user prompt containing the scaffold. Output: `detailed_description`
   body only (style opening + shots). Temperature 0.5, max_tokens 1200. No
   grammar — the lint pass is the enforcement (a GBNF for free English prose
   with embedded tags would be brittle; lint+retry is cheaper and safer).
2. **Sound** — `H3_SOUND_SYSTEM` + beat sound events + tone: returns
   JSON `{overall_soundscape, non_diegetic_music}` (grammar-constrained,
   max_tokens 250).

On lint errors: one retry with the errors prepended ("Your previous output
failed: …. Rewrite."). On second failure: the panel's compile is marked
failed with the errors and the **raw text is still stored** in
`video_prompt` with `video_prompt_source='compiled'` and the error list in
the result payload — the user can hand-fix in the editor rather than losing
the draft. Deterministic sections are always correct by construction, so a
failed body never corrupts labels/timeline.

Prompt/exemplar YAML keys: `H3_BODY_SYSTEM`, `H3_SOUND_SYSTEM`,
`H3_SOUND_GRAMMAR` in `data/meta_prompt.yml`, uncensored directive included.
Context budget: system + exemplar (~1.5 K) + scaffold (~600) + output
(~1.2 K) fits the 8192-token slot of `qwen3vl-30b-a3b`.

## 5. Runner orchestration

`StoryboardRunner.compile_video(storyboard_id, panel_ids=None, force=False)
-> Dict[str, int]` — mirrors `_synthesize_locked` exactly: under
`_synth_lock`, semaphore from `parallel_slots`, skips
`video_prompt_locked` panels unless explicitly forced, per-panel
`asyncio.gather`. Panels with **no beats** fall back to a single-shot
scaffold synthesized from the panel's `action`/`duration_s` (degraded but
functional — beats are strongly recommended, not required). No-VLM: raises
(compile requires prose generation; no deterministic fallback pretends to
be a finished prompt) — but a `deterministic_only=True` escape hatch
assembles skeleton + scaffold text as the body for offline testing.

WS events (`storyboard` channel): `compile_progress {storyboard_id,
panel_id, done, total}`, `compile_complete {storyboard_id, compiled,
failed, skipped_locked}`, `compile_error {storyboard_id, error}` — same
exactly-one-terminal-event contract as `synthesize`.

## 6. Backend API

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/storyboard/{id}/compile` (202) | `{panel_ids?, force?}` | `{"status":"started","total":int}` |

Plus: `StoryboardPatch` gains `video_target` (nullable; 400 unless
`'minimax'` or null), `PanelPatch` gains `video_prompt` (server-wins lock
rule as §2). Lint warnings from the last compile ride the tree payload as
`panels[].video_prompt_warnings` (JSON column `panels.video_prompt_warnings
TEXT` written by the runner, cleared on user edit).

## 7. Frontend

- **StoryboardSettingsDialog**: "Video target" select (None / MiniMax H3).
- **StoryboardView** header: "Compile video prompts" action (visible when
  `video_target` set), progress chip fed by `compile_*` WS events (same
  pattern as the synthesis chip).
- **PanelDetail**: a "Video prompt" section (visible when `video_target`
  set): monospace textarea with the compiled document, source/lock status
  chip + Unlock button (mirroring the image-prompt controls), per-panel
  "Compile" button, and a warnings list when
  `video_prompt_warnings` is non-empty. Local-ref + snapshot resync pattern
  applies (keyed on `[panel.id, panel.updated_at]`, joining the existing
  watcher).
- Store: `compile` progress state beside `synthesis`; `compileVideo()`
  action.

## 8. Dialect seam for LTX (future)

`compile_video` dispatches on `video_target` to a per-dialect module
(`h3_compiler` now; `ltx_compiler` later) behind one protocol:
`build(tree, scene, panel, subjects, opts) -> CompileJob` (scaffold +
deterministic sections + lint expectations). The runner, API, storage, and
UI are dialect-agnostic — adding LTX is a new module + enum value, no
schema change.

## 9. Testing

- `tests/test_h3_compiler.py` — the bulk of this phase's tests, all pure:
  reference-label assignment (0/1/2 subject refs, with/without scene ref),
  timeline math (cut grouping, rescale-to-duration, MM:SS.mmm formatting,
  alignment-instruction wording), speaker assignment (multi-speaker,
  off-screen, audio-only cue rules), camera phrase rendering, section
  assembly ordering, and an exhaustive lint suite (each rule violated
  independently against fixture documents).
- `tests/test_storyboard_compile_api.py` — route contract with a stubbed
  runner; PATCH lock semantics for `video_prompt`.
- Runner tests with a fake VLM: retry-on-lint-error path, failed-compile
  raw-text persistence, `deterministic_only` path, no-beats fallback.
