# I2V Dialog Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Image to Video dialog as three panels shaped like the T2I dialog, with server-owned Batch × Count runs, a RAW / STRUCTURED prompt view, and an unsaved-changes warning in both dialogs.

**Architecture:** The backend gains an all-videos listing and a compact batch run inside `I2vRunner` that reuses `write_prompt` and `generate`. The frontend splits `I2VDialog.vue` into focused components, moves the source image into the store so the dialog can change image in place, and adds one shared unsaved-changes helper used by both dialogs.

**Tech Stack:** Python 3.11, FastAPI, SQLite, pytest; Vue 3 `<script setup>`, Pinia, TypeScript.

**Spec:** `docs/superpowers/specs/2026-10-30-i2v-dialog-rework-design.md`

## Global Constraints

- Work on branch `feat-i2v-dialog-rework` from local `main`. In a worktree, run make as `make VENV_DIR=...`.
- `prompt` stays one string everywhere; a view switch never writes it.
- Header panel: read-only, no edit control, always.
- Batch 1..20, Count 1..20, `i2v.window` default 4. Limit config keys: `i2v.max_batch_size`, `i2v.max_count_per_batch`.
- Seeds stay in `0..2^31-1`.
- Do not refactor `T2iRunner` into a shared base.
- All-videos listing prunes nothing.
- No schema change. Nothing is persisted before ingest (i2v rule).
- ★ is never a grade; a tile's ★ uses `setFavorite` only.
- Native `confirm()` for prompts. No `@click.self` close on the overlay.
- Never set `scrollbar-width` / `scrollbar-color`; use `scrollbar-gutter: stable`.
- `make quality test` and `cd frontend && npm run build` pass before each commit that touches their code. `test_file_watcher_triggers_reload` is a known flake on this box.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Switching image while replies are in flight:** a slow clip or job list for the previous image must not replace the new image's lists. Pinned in Task 10.
2. **The selected clip disappears (deleted in either list, or pruned):** the form stays as a scratch form and the next click does not raise a false warning. Pinned in Task 10.
3. **The source file disappears mid-run:** already-queued jobs stay, the run ends `batch_failed` saying how far it got. Pinned in Task 4.
4. **Two runs at once:** cancelling one leaves the other alone, and the VLM is not unloaded while another run is still writing. Pinned in Task 4.
5. **Dialog reopened after a run ended unseen, or after a server restart:** no stuck status line and no live Cancel. Pinned in Task 6 and Task 10.

---

### Task 1: Blank lines in the Prompt panel don't break the lint

**Files:**
- Modify: `metascan/core/i2v_compiler.py:218-220`
- Test: `tests/test_i2v_compiler.py`

**Interfaces:**
- Produces: `_DESCRIPTION_RE` ending at `\n\noverall_soundscape:` / `\n\nnon_diegetic_music:` / end of text. `lint_against_template` reads the same regex.

- [ ] **Step 1: Write the failing tests**

`test_blank_line_in_description_counts_all_words`: a valid document whose description is 2 paragraphs split by `\n\n`, the second holding most of the words; assert no `"words"` floor warning from `lint_i2v_prompt(text, 6)`. Build the text by taking `assemble_i2v_prompt(...)`'s output and inserting `\n\n` between two sentences of the description.

`test_blank_line_keeps_shot_scan_in_template_lint`: a templated document with `\n\n` before `[Shot 2]`; assert `lint_against_template` gives no shot-count warning.

- [ ] **Step 2: Run them**

Run: `pytest tests/test_i2v_compiler.py -k blank_line -v`
Expected: FAIL (false warnings).

- [ ] **Step 3: Change the regex**

```python
_DESCRIPTION_RE = re.compile(
    r"integrated_multimodal_description:(.*?)"
    r"(?:\n\n(?:overall_soundscape|non_diegetic_music):|\Z)",
    re.S,
)
```

- [ ] **Step 4: Run the file**

Run: `pytest tests/test_i2v_compiler.py tests/test_i2v_templates.py -v`
Expected: PASS, including `test_single_take_output_is_pinned`.

- [ ] **Step 5: Commit** — `fix(i2v): a blank line in the prompt no longer cuts the lint short`

---

### Task 2: All-videos listing

**Files:**
- Modify: `metascan/core/database_sqlite.py` (after `list_i2v_videos`), `backend/services/i2v_service.py`, `backend/api/i2v.py:306`
- Test: `tests/test_i2v_db.py`, `tests/test_i2v_api.py`

**Interfaces:**
- Produces: `db.list_all_i2v_videos(limit: int = 60, before_id: Optional[int] = None) -> List[Dict[str, Any]]`; `I2vService.list_all_videos(limit, before_id, megapixel_options)`; `GET /api/i2v/videos` with `source_path` optional, `limit` (1..200, default 60), `before_id`. Without `source_path` it answers the all-videos page.

- [ ] **Step 1: Write the failing tests**

`test_list_all_is_newest_first_and_pages`: 3 rows across 2 sources; `limit=2` gives the newest 2, then `before_id=<last id>` gives the third.
`test_list_all_never_prunes`: a row with no media row is left out, and `SELECT COUNT(*) FROM i2v_videos` is unchanged, with the guard clear.
`test_list_all_carries_grade_star_and_source`: `is_favorite`, `grade`, `source_path` present.
API: `test_get_videos_without_source_returns_all_with_form_state`, `test_get_videos_with_source_is_unchanged`.

- [ ] **Step 2: Run them**

Run: `pytest tests/test_i2v_db.py tests/test_i2v_api.py -k "list_all or get_videos" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

One inner JOIN on `media`, LEFT JOIN `media_judgments`, `ORDER BY v.id DESC LIMIT ?`, shaped by `_i2v_video_row` and `_row_grade` as `list_i2v_videos` does. No delete. The service runs `form_state_for_row` per row.

- [ ] **Step 4: Run them**

Expected: PASS.

- [ ] **Step 5: Commit** — `feat(i2v): list every video, newest first`

---

### Task 3: Config and seed planning

**Files:**
- Modify: `backend/config.py:172`, `metascan/core/i2v_runner.py`, `frontend/src/types/i2v.ts`
- Test: `tests/test_i2v_runner.py`, `tests/test_i2v_api.py`

**Interfaces:**
- Produces: `get_i2v_config` keys `max_batch_size: 20`, `max_count_per_batch: 20`, `window: 4` (ints, each clamped to ≥ 1), which reach `GET /api/i2v/config`. `I2vConfig` gains the same three fields.
- Produces: in `i2v_runner.py`, `SEED_MAX = 2**31 - 1` and

```python
def plan_i2v_seeds(
    seed: int, policy: str, batch_size: int, count: int,
    rng: Callable[[], int] = ...,  # default: random in 0..SEED_MAX
) -> Tuple[List[List[int]], Optional[int]]:
    """One seed list per batch, in render order, and the next unused seed."""
```

- [ ] **Step 1: Write the failing tests**

`test_fixed_is_one_seed_per_batch`: `plan_i2v_seeds(5, "fixed", 3, 4)` == `([[5], [5], [5]], 5)`.
`test_increment_runs_across_batches`: `(5, "increment", 2, 2)` == `([[5, 6], [7, 8]], 9)`.
`test_decrement`: `(5, "decrement", 1, 3)` == `([[5, 4, 3]], 2)`.
`test_random_uses_rng`: fake rng yields 1, 2, 3, 4; `(9, "random", 1, 2)` == `([[9, 1]], 2)`. The first seed is the box's seed, as in today's loop.
`test_last_seed_at_the_edge_has_no_next`: `(SEED_MAX, "increment", 1, 1)` == `([[SEED_MAX]], None)`.
`test_leaving_the_range_is_refused`: `(SEED_MAX, "increment", 1, 2)` raises `I2vRequestError`; so does `(0, "decrement", 2, 1)`.
`test_config_limits_default_and_clamp`: defaults 20 / 20 / 4; `{"i2v": {"window": 0}}` gives 1.

- [ ] **Step 2: Run them**

Run: `pytest tests/test_i2v_runner.py tests/test_i2v_api.py -k "seed or config_limits" -v`
Expected: FAIL.

- [ ] **Step 3: Implement** `plan_i2v_seeds` and the config keys; document them in `get_i2v_config`'s docstring.

- [ ] **Step 4: Run them**

Expected: PASS.

- [ ] **Step 5: Commit** — `feat(i2v): batch limits and a seed plan for a run`

---

### Task 4: The batch run in `I2vRunner`

**Files:**
- Modify: `metascan/core/i2v_runner.py`, `metascan/core/t2i_runner.py` (`_unload_vlm`, new `is_prompting`), `backend/services/auto_index.py:129`, `backend/main.py:561-573, 733, 741`
- Test: `tests/test_i2v_runner.py`, the existing auto-index test file

**Interfaces:**
- Consumes: `plan_i2v_seeds`, `SEED_MAX`, `write_prompt`, `generate`.
- Produces:

```python
@dataclass
class I2vBatchRequest:
    source_path: str
    prompt: str            # "" = batch 1 is written too
    idea: str
    duration_s: float
    quality: str
    seed: int
    seed_policy: str       # fixed | increment | decrement | random
    batch_size: int
    count_per_batch: int   # ignored (1) when seed_policy is fixed
    megapixels: float
    loras: List[Dict[str, Any]]
    preset_id: int
    writer: str = "base"
    steps: Optional[int] = None
    template_id: Optional[str] = None
    output_root: Optional[str] = None
    output_prefix: Optional[str] = None
    category_ids: List[int] = field(default_factory=list)
    writer_id: Optional[str] = None      # lineage of the box's draft
    draft_prompt: Optional[str] = None
    writer_model: Optional[str] = None
    window: int = 4

@dataclass
class I2vBatchStarted:
    batch_id: str
    total_videos: int
    next_seed: Optional[int]
```

`I2vRunner.start_batch(req) -> I2vBatchStarted`; `active_batches() -> List[Dict[str, Any]]`, each `{batch_id, source_path, phase, batch, total_batches, queued, total_videos}` with `phase` `"writing"` or `"rendering"`; `cancel_batch(batch_id) -> bool`; `is_prompting() -> bool`. Constructor gains `get_all_vlms: Optional[Callable[[], Iterable[Any]]] = None`, `unload_vlm_during_generation: bool = True`. Attribute `prompting_elsewhere: Callable[[], bool] = lambda: False` on **both** runners, wired in `lifespan` to the other runner's `is_prompting`. `T2iRunner.is_prompting()` is true while any Random batch has `prompting`.

Events on `i2v`: `batch_progress`, `batch_done`, `batch_failed` (`error`), `batch_cancelled`, each carrying the `active_batches` row, and `warnings: List[str]` on `batch_progress` / `batch_done`.

- [ ] **Step 1: Write the failing tests**

Use a `CountingComfy(FakeComfy)` with unique job ids and a recorded `cancel`, and a helper that feeds `handle_job_event("job_update", {"job_id": id, "state": "done"})`.

- `test_box_text_is_batch_one_and_later_batches_are_written`: prompt `P`, Batch 3 → 2 VLM calls; submits' `positive` are `P`, then the two written texts.
- `test_empty_box_writes_every_batch`: Batch 2 → 2 VLM calls.
- `test_count_renders_one_prompt_per_seed`: Batch 1, Count 3, increment → 3 submits, same `positive`, seeds `s, s+1, s+2`.
- `test_fixed_seed_ignores_count`: Batch 2, Count 5, fixed → 2 submits, same seed.
- `test_written_clip_form_holds_its_own_prompt`: `_job_meta[job]["form"]["prompt"]` is the written text and `writer_id` / `draft_prompt` / `writer_model` its lineage; a box step keeps the request's lineage.
- `test_prompts_first_then_unload_then_submit`: record an order list; `["write", "write", "unload", "submit", ...]` with unload on.
- `test_unload_off_renders_as_each_prompt_is_ready`: `["write", "submit", "write", "submit"]`, no unload.
- `test_window_caps_unfinished_jobs`: `window=2`, Count 4 → 2 submits until a `job_update` `done` arrives, then a third. A `failed` update also frees a slot and the run still ends `batch_done`.
- `test_cancel_stops_writing_and_cancels_jobs`: `cancel_batch` cancels every unfinished job, emits one `batch_cancelled`.
- `test_a_failed_write_keeps_what_was_queued`: unload off; second write raises → job 1 not cancelled; one `batch_failed` whose `error` says `1 of 2`.
- **Review Focus 3** `test_source_file_gone_mid_run_ends_failed_keeps_jobs`: delete `self.src` after the first submit → `batch_failed`, first job not cancelled, no more submits.
- **Review Focus 4** `test_cancelling_one_run_leaves_the_other`, `test_no_unload_while_another_run_writes` (a second run blocked in `generate_text`; first run's unload skips the writer; same via `prompting_elsewhere = lambda: True`).
- `test_adapter_gone_refuses_at_start`: `writer="adapter:9"` unservable with an empty box raises `WriterAdapterUnavailable` from `start_batch`, and nothing runs.
- `test_no_writer_needed_when_batch_one_is_box_text`: `get_vlm` returns None, Batch 1 with text starts fine; with Batch 2 raises `I2vUnavailableError`.
- `test_generation_busy_counts_a_writing_i2v_run` in the auto-index tests.

- [ ] **Step 2: Run them**

Run: `pytest tests/test_i2v_runner.py -k batch -v`
Expected: FAIL.

- [ ] **Step 3: Implement the run**

`start_batch` plans the seeds, checks the writer only if a prompt must be written (`_adapter_lora`, VLM present) and the box draft's adapter if it has one, registers an `_I2vBatch`, creates the task and returns. `_run`:

```python
try:
    if self.unload_vlm_during_generation:
        prompts = [await self._step_prompt(b, i) for i in range(b.total)]  # phase "writing"
        b.phase = "rendering"
        await self._unload_vlm(b)
        for i, p in enumerate(prompts):
            await self._submit_step(b, i, p)
    else:
        for i in range(b.total):
            await self._submit_step(b, i, await self._step_prompt(b, i))
    await b.all_jobs_ended.wait()
    retire "done"
except asyncio.CancelledError:
    raise
except Exception as exc:
    retire "failed", with how far it got
```

`_submit_step` calls `self.generate(...)` once per seed after acquiring a window slot. `handle_job_event` also takes `job_update`: on a terminal state (`done`, `failed`, `cancelled`) release the slot; a `failed` job doesn't end the run. Keep the existing `job_outputs` path unchanged. `_unload_vlm` copies T2I's; skip the writer while another I2V run writes or `prompting_elsewhere()`. Add the `prompting_elsewhere()` test to `T2iRunner._unload_vlm`'s `writer_busy`. `aclose` cancels running tasks and awaits them first.

Wire in `lifespan`: pass `get_all_vlms` and the unload flag; set both `prompting_elsewhere` after both runners exist. Extend `generation_busy(t2i_runner, comfy_client, i2v_runner=None)` with `i2v_runner.is_prompting()`, passing it at both call sites. Shutdown order is already right (`comfy_client.shutdown()` first).

- [ ] **Step 4: Run them**

Run: `pytest tests/test_i2v_runner.py tests/test_t2i_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit** — `feat(i2v): server-owned batch runs`

---

### Task 5: Batch routes

**Files:**
- Modify: `backend/api/i2v.py`
- Test: `tests/test_i2v_api.py`, and `tests/test_training_manager.py` if it lists GPU_FREE routes

**Interfaces:**
- Consumes: Task 4's request and methods.
- Produces: `POST /api/i2v/batches` → `{batch_id, total_videos, next_seed, warnings}`; `GET /api/i2v/batches` → `active_batches()`; `POST /api/i2v/batches/{id}/cancel` → `{status: "cancelled"}`.

- [ ] **Step 1: Write the failing tests**

`test_start_batch_maps_the_form`, `test_start_batch_is_gpu_free` (409 `gpu_busy_training`), `test_batch_size_outside_limits_is_400`, `test_count_outside_limits_is_400`, `test_bad_policy_is_422`, `test_seed_range_refusal_is_400`, `test_adapter_gone_is_409_with_code`, `test_unknown_category_is_404`, `test_warnings_lint_the_box_prompt`, `test_cancel_unknown_batch_is_404`, `test_cancel_ended_batch_is_cancelled`, `test_list_batches`.

- [ ] **Step 2: Run them**

Run: `pytest tests/test_i2v_api.py -k batch -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`BatchRequestBody` = `GenerateRequest`'s fields with `prompt: str = ""`, plus `seed_policy: Literal[...]`, `batch_size: int`, `count_per_batch: int`, `writer` (validated by `_writer_choice`). Move `/generate`'s validation and preset choice into a helper both routes call. Keep `/prompt`'s error mapping for writer failures. A batch that ended stays known (bounded) so its cancel answers `cancelled`.

Then check `/i2v/generate`'s callers: `frontend/src/api/i2v.ts::generateVideo` is the dialog's. Storyboard `generateVideo` uses are a different function; confirm with `grep`. If only the dialog calls it, remove it in Task 10 once the dialog has moved; if anything else does, keep it.

- [ ] **Step 4: Run them**

Expected: PASS.

- [ ] **Step 5: Commit** — `feat(i2v): routes for batch runs`

---

### Task 6: Store and API client

**Files:**
- Modify: `frontend/src/api/i2v.ts`, `frontend/src/types/i2v.ts`, `frontend/src/stores/i2v.ts`

**Interfaces:**
- Consumes: Tasks 2, 3, 5.
- Produces (api): `listAllI2vVideos(limit: number, beforeId: number | null): Promise<I2vVideo[]>`; `startI2vBatch(body: I2vBatchBody): Promise<{batch_id: string; total_videos: number; next_seed: number | null; warnings: string[]}>`; `listI2vBatches(): Promise<I2vBatch[]>`; `cancelI2vBatch(id: string)`. Type `I2vBatch = {batch_id, source_path, phase: 'writing' | 'rendering', batch, total_batches, queued, total_videos}`.
- Produces (store): `sourcePath: Ref<string | null>`; `source: Ref<Media | null>` (null = its image was deleted); `switchSource(path: string, media: Media | null): void`; `allVideos`, `allVideosLoaded`, `hasMoreAll`, `refreshAllVideos()`, `loadOlderVideos()`, `removeVideo(id: number)`; `batches: Ref<I2vBatch[]>` (open source only, via `sourceBatches` computed), `warnings` from events, `refreshBatches()`, `trackBatch(row)`, `cancelSourceRuns()`.

- [ ] **Step 1: Pin the merge logic**

Add `frontend/src/utils/i2vLists.ts` with pure `mergeNewestPage(page: I2vVideo[], held: I2vVideo[]): I2vVideo[]` (new page, then held rows older than its oldest id; a held row inside the page's id range and absent from it is gone). Unit-test it in scratch with Node 22 type stripping (`reference_metascan_frontend_unit_tests`): newest page replaces, older rows stay, a deleted row inside the range drops.

- [ ] **Step 2: Implement the store**

`open()` loads the first all-videos page and batches beside today's calls. `switchSource` bumps `videosSeq` and a new `jobsSeq`, clears jobs and batches on show, then refreshes clips, jobs, batches. `refreshActiveJobs` and `refreshBatches` drop a reply a newer switch superseded. `handleI2vEvent`: `i2v_videos_changed` refreshes `allVideos` always, left list only for the open source; `batch_*` updates `batches` for any source and drops the row on `batch_done` / `batch_failed` / `batch_cancelled`, and sets a status note from `error`. `refreshBatches` replaces the list wholesale, so a run that ended unseen leaves nothing (**Review Focus 5**). `applyJudgment`, `applyFavoriteFlag`, `toggleFavorite` and `saveFormState` update a clip in both lists; `rowNotes` settle on both refreshes.

`cancelSourceRuns` cancels each of the source's runs, then `cancelAllJobs()`; every attempt runs and the first failure is rethrown.

- [ ] **Step 3: Verify**

Run: `cd frontend && npm run build`
Expected: PASS. The dialog still reads `props.media`, so nothing breaks yet; keep `source` set by `open`.

- [ ] **Step 4: Commit** — `feat(i2v): the store holds the source, both clip lists and runs`

---

### Task 7: Unsaved-changes warning, in both dialogs' shared parts and T2I

**Files:**
- Create: `frontend/src/utils/unsavedForm.ts`, `frontend/src/composables/useDelayedClick.ts`
- Modify: `frontend/src/components/generation/ThumbTile.vue:38-43`, `frontend/src/components/dialogs/T2IDialog.vue`

**Interfaces:**
- Produces:

```ts
export type LeaveDecision = 'go' | 'ask-unsaved' | 'ask-save-failed'
export function leaveDecision(s: {
  selected: boolean; saveFailed: boolean; baseline: string; current: string
}): LeaveDecision
export type LeaveKind = 'clip' | 'image' | 'close'
export function leaveText(d: LeaveDecision, kind: LeaveKind): string
export function confirmLeave(s: Parameters<typeof leaveDecision>[0], kind: LeaveKind): boolean
```
`leaveText('ask-unsaved', 'clip')` = `You have changes that haven't been generated. Load this clip and lose them?`; `'image'` says `image`; `'close'` = `You have changes that haven't been generated. Close and lose them?`. `leaveText('ask-save-failed', 'clip')` = `Your changes to this clip couldn't be saved. Load this clip and lose them?` (`close` and `image` likewise).
`useDelayedClick(onSingle: (e: MouseEvent) => void, onDouble: () => void, delayMs = 250)` returns `{ onClick, onDblclick }`; a double-click cancels the pending single click; the timer is cleared on scope dispose.

- [ ] **Step 1: Write the failing unit tests in scratch** (Node type stripping)

- selected, save fine, form differs → `go`.
- selected, `saveFailed` → `ask-save-failed`, even with equal strings.
- not selected, equal → `go`; different → `ask-unsaved`.
- `useDelayedClick` logic: split a pure `makeDelayedClick(setTimer, clearTimer, ...)` so a fake timer can prove one click fires once, a double-click fires only `onDouble`, and a second tile's click doesn't cancel the first's.

- [ ] **Step 2: Run them**

Expected: FAIL (no module).

- [ ] **Step 3: Implement the helpers; `ThumbTile` uses `useDelayedClick`**

`evt.detail > 1` stays out: `dblclick` now cancels. The overlay buttons keep `@click.stop` and `@dblclick.stop`. Check every `ThumbTile` user with `grep`.

- [ ] **Step 4: Wire T2I**

In `T2IDialog.vue`, `let cleanForm = ''`, `const snapshotForm = () => JSON.stringify(toFormState(form))`. Set it:
- `initForm`: `JSON.stringify(toFormState({ ...fresh, seed: form.seed }))`, so a restored draft compares with the defaults and its seed alone doesn't count.
- after `autosave.markLoaded()` in `onSelectTile`; in `stopEditing`; after a successful `startBatch` following the `next_seed` write; at the Random unlock patch.

In `onSelectTile`, after the two scorecard refusals and before `autosave.flush()`:

```ts
if (!confirmLeave({ selected: store.selectedId !== null, saveFailed: saveFailed.value,
  baseline: cleanForm, current: snapshotForm() }, 'image')) return
```

`close()` is unchanged.

- [ ] **Step 5: Verify**

Run: scratch unit tests, then `cd frontend && npm run build`
Expected: PASS.

- [ ] **Step 6: Commit** — `feat(t2i): ask before a tile click drops unsaved form changes`

---

### Task 8: Prompt editor component

**Files:**
- Create: `frontend/src/components/generation/I2vPromptEditor.vue`, `frontend/src/utils/i2vPromptView.ts`
- Modify: `frontend/src/components/storyboard/TextEditPopup.vue`

**Interfaces:**
- Produces: `TextEditPopup` prop `multiline?: boolean` (default false). When true, `save` emits the draft verbatim.
- Produces: `promptViewState(text: string, preferred: 'raw' | 'structured', locked: boolean): { view: 'raw' | 'structured'; structuredEnabled: boolean; note: string | null }`.
- Produces: `<I2vPromptEditor v-model="prompt" />`; emits nothing else. Holds the view choice, keyed `metascan.i2v.promptView.v1` with guarded storage, default `structured`.

- [ ] **Step 1: Write the failing unit tests in scratch**

For `promptViewState` with a valid document `V` and mangled `M`:
- `('', 'structured', false)` → raw, disabled, note null.
- `(V, 'structured', false)` → structured, enabled, null.
- `(V, 'raw', false)` → raw, enabled, null.
- `(M, 'structured', false)` → raw, disabled, note contains `can't be shown as structured`.
- `(V, 'structured', true)` → raw, enabled, null: `locked` is what keeps a fix-while-typing from flipping the view.

And `replaceI2vSection` keeps line breaks: a body with `\n` and `\n\n` round-trips through `parseI2vPrompt`.

- [ ] **Step 2: Run them**

Expected: FAIL.

- [ ] **Step 3: Implement**

Note text: `This prompt can't be shown as structured: the H3 field labels were changed or removed. It will still generate as written.`

`locked` is set when the document stops parsing while `structured` is preferred and cleared when the user presses STRUCTURED or `modelValue` is replaced by a clip load. The editor exposes `resetView()` for that load; Task 10 calls it.

Move the panels' markup and CSS from `I2VDialog.vue` here, unchanged except: Header has no popup; all popups get `multiline`; RAW is one textarea with a popup and a copy icon (`pi pi-copy`, `copyToClipboard`, toast `Prompt copied`). Toggle sits top right, as T2I's segmented `.seg`. Update `TextEditPopup`'s header comment to name the prop.

- [ ] **Step 4: Verify**

Run: scratch tests and `cd frontend && npm run build`
Expected: PASS.

- [ ] **Step 5: Commit** — `feat(i2v): a RAW / STRUCTURED prompt editor that keeps line breaks`

---

### Task 9: Three-panel layout

**Files:**
- Create: `frontend/src/components/generation/I2vClipTile.vue`, `frontend/src/components/generation/I2vClipList.vue`
- Modify: `frontend/src/components/dialogs/I2VDialog.vue`

**Interfaces:**
- Produces: `I2vClipTile` props `{ video: I2vVideo; selected: boolean }`, emits `select(MouseEvent)`, `play`, `favorite`, `delete`, using `useDelayedClick` (▶ is immediate). `I2vClipList` props `{ videos: I2vVideo[]; selectedId: number | null; columns: 1 | 2; hasMore?: boolean; loadingMore?: boolean; emptyText: string }`, a `jobs` slot before the tiles, emits the tile events plus `more`.

- [ ] **Step 1: Move the markup, behaviour unchanged**

Card `width: min(1500px, 96vw)`, `max-height: 94vh`, `overflow: hidden`; body grid `240px minmax(0, 1fr) 400px`; side panels `height: 0; min-height: 100%` as `.t2i-gallery`, so they never make the row taller; the middle is one scroll box with `scrollbar-gutter: stable`, sticky editing bar and sticky run row, copied from `.t2i-main`.

Middle order per the spec's Layout section. Writer and Generate Prompt go under Idea. Use `SeedControls`, replacing the Seed and Seed after generate fields, and `JobTile`, replacing the inline job tile. Left list `columns=1` from `store.videos`; right list `columns=2` from `store.allVideos`, titled "Videos", with Show more. Clip details lines move into the tile via `i2vVideoDetails`. `viewerIndex` becomes `{ list: 'left' | 'right'; index: number } | null`, and `viewerMedia` builds from that list's clips: each clip's own row from `mediaStore.allMedia` by path when found, else today's spread.

- [ ] **Step 2: Verify**

Run: `cd frontend && npm run build`
Expected: PASS.

Then, in the smoke rig (`reference_metascan_fullstack_smoke`, never :8700 / :5173), screenshot at 1920×1080 and 1280×720: three panels, no horizontal scroll, side panels scroll on their own, the run row stays visible, and click, double-click, ▶, ★, ✕ work in both lists.

- [ ] **Step 3: Commit** — `feat(i2v): three-panel dialog layout`

---

### Task 10: Run row, switching image, unsaved changes

**Files:**
- Modify: `frontend/src/components/dialogs/I2VDialog.vue`, `frontend/src/api/i2v.ts` and `backend/api/i2v.py` (only if Task 5 found `/generate` removable)

**Interfaces:**
- Consumes: Tasks 5–9.

- [ ] **Step 1: Run row**

Batch and Count inputs, session-local refs, clamped to `config.max_batch_size` / `max_count_per_batch`. Count disabled with the T2I `fld-off` treatment and title while the policy is `fixed`. The button reads `Generate ×N` when `N = batch × effectiveCount > 1`. Generate is no longer disabled by an empty box; it is disabled while submitting or when `store.source` is null.

`onGenerate`: run the `requestSignature` confirm only when `prompt.trim()`, Batch is 1 and effective Count is 1. Then `flushFormState()`, `selectedId = null` **before** anything changes the form, `startI2vBatch(...)`, take `next_seed` if the seed box still equals what was sent, `markClean()`, and `store.trackBatch` / `refreshBatches`. Handle `ADAPTER_UNAVAILABLE`, unknown category and `gpu_busy` as `onGenerate` does today, and show `warnings` under the run row. Status line: `Batch k/N · writing prompt` or `Batch k/N · rendering` from `store.sourceBatches`. Footer Cancel calls `store.cancelSourceRuns()`, enabled while the source has a run or job.

- [ ] **Step 2: Switching image**

`const sourceMedia = computed(() => store.source)`; every `props.media` read after mount goes through it or `store.sourcePath`. `onSelectClip(v)`:

```ts
if (v.id === selectedId.value) return
if (!confirmLeave({ selected: selectedId.value !== null, saveFailed: saveFailed.value,
  baseline: cleanForm, current: JSON.stringify(currentForm()) }, 'clip')) return
if (differs(v.source_path, store.sourcePath))
  store.switchSource(v.source_path, findSource(v.source_path))
loadClip(v)
promptEditor.value?.resetView()
```

`findSource` looks in `mediaStore.allMedia`, comparing with backslashes normalised. Set `cleanForm` after `loadClip`, on `stopEditingClip`, when defaults land in `onMounted`, and after a successful start. The list watcher that drops a vanished selected clip also sets `cleanForm` to the current form (**Review Focus 2**), and now also watches `store.allVideos`: a clip selected from the right list isn't in the left one if the image changed.

Deleted source: left panel shows the placeholder `Source image deleted`, Generate and Generate Prompt are disabled, with title and a note under the run row: `This clip's source image was deleted, so it can't be generated again.` Clips still list.

`close()` starts with the same `confirmLeave` check, kind `'close'`.

`onDeleteVideo` calls `store.removeVideo(v.id)` and refreshes both lists.

- [ ] **Step 3: Remove `/generate` if Task 5 found it unused**, with its tests, or leave it.

- [ ] **Step 4: Verify in the smoke rig**

Use the fake ComfyUI and VLM. Each must pass:

- Click a clip in the right list from another image: left panel, clips, form change; ★ and grade reach the same clip in both lists.
- **Review Focus 1:** delay `GET /api/i2v/videos` and `/api/comfy/jobs` for image A, click to image B: B's lists stay.
- **Review Focus 2:** select a clip, delete it, click another: no warning.
- **Review Focus 5:** start a run, close the dialog, let it end, reopen: no status line, Cancel disabled. Same after a backend restart mid-run.
- Scratch edit then click: confirm; Cancel keeps everything; OK loads. Generate Prompt then click: confirm.
- Selected clip, edit, click: no confirm, the edit is on that clip when reopened.
- Double-click to play with a dirty form: plays, no confirm, no switch.
- ✕ with a dirty form: confirm. T2I: draft restored, tile click confirms; ✕ does not.
- Batch 2 × Count 2, increment, empty box: 4 clips, 2 prompts; box unchanged; selected clip's form unchanged.
- Fixed seed: Count disabled. Deleted source: Generate disabled, form loads.
- Raw: break a label: note shows, Structured disabled, Generate works. Popup keeps line breaks in all four boxes. Copy icon copies the whole text.

- [ ] **Step 5: Run the full checks**

Run: `make quality test` and `cd frontend && npm run build`
Expected: PASS, the known watcher flake aside.

- [ ] **Step 6: Commit** — `feat(i2v): batch runs, switching image and unsaved-changes prompts in the dialog`

---

### Task 11: Docs and rules

**Files:**
- Modify: `.claude/rules/i2v.md`, `.claude/rules/t2i.md`, `CLAUDE.md` (WS `i2v` channel events only if it lists them), `docs/features.md`, `docs/api-reference.md`, `docs/configuration.md`
- Delete: nothing. `docs/i2va-ui-rework.md` stays untracked unless the owner says otherwise.

- [ ] **Step 1: Update the rules**

`i2v.md`: replace "`TextEditPopup` … collapses newlines … each H3 field is one logical line" with the `multiline` rule; replace the seed-policy-and-count paragraph with Batch × Count and the box rule; add the batch run (order, window, events, restart), the all-videos listing, switching image, deleted source, RAW / STRUCTURED and `I2VDialog closes only via its ✕` plus its confirm. `t2i.md`: remove "I2V does not use them"; add the unsaved-changes rule and the delayed tile click.

- [ ] **Step 2: Update user docs**

The three new routes and `GET /api/i2v/videos` parameters; the `i2v.max_batch_size`, `i2v.max_count_per_batch`, `i2v.window` keys; the feature description.

- [ ] **Step 3: Commit** — `docs(i2v): the reworked dialog, batch runs and unsaved-changes rules`
