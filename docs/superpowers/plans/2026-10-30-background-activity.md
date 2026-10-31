# Background Activity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One protocol every background process plugs into, a title-bar pill and grid to see and control them, always-on import, and idle library hygiene.

**Architecture:** A `ProcessRegistry` installed in the FastAPI `lifespan` owns state, holds and owner intent. Each process is a thin adapter over its existing mechanism. The frontend renders whatever `GET /api/background` returns. Import becomes a polling watcher feeding `Scanner.ingest_file`; hygiene is a list of idle checks. Tagging moves from a JSONL file to a table.

**Tech Stack:** Python 3.11, FastAPI, SQLite, Vue 3, Pinia, PrimeVue.

**Spec:** `docs/superpowers/specs/2026-10-30-background-activity-design.md` (read it first; section numbers below refer to it).

## Global Constraints

- Backend Python never imports `PyQt6` or `qt_material`; `metascan/core` never imports `backend`.
- Every GPU consumer keeps `GPU_FREE` and `gpu_reserved()`; the registry sits on top of them.
- No process is suspended with a signal. Pause is cooperative on every OS.
- `activity` text never holds prompt text or a full path. File names only.
- Every media-row delete keeps its tombstone: `delete_media`, `delete_media_batch`, `_purge_media_rows`.
- `set_grade` / `set_favorite` are never used to restore anything.
- Full clean's order and rules in `CLAUDE.md` do not change, and no code path deletes the pending file on `CancelledError`.
- `make quality test` and `cd frontend && npm run build` pass before every commit that touches their side. In a worktree use `make VENV_DIR=...`. `test_file_watcher_triggers_reload` is a known flake.
- Constants, with spec values: `INTERACTIVE_GRACE_S = 60`, `MAX_TAG_ATTEMPTS = 3`, `WATCH_INTERVAL_S = 5`, `IDLE_AFTER_S = 120`, `HYGIENE_INTERVAL_S = 1800`, `MOVE_GRACE_S = 60`, mass-removal `> 25%` and `> 100` rows, WS at most 2 per second per process, `library` at most 1 per 2 s, mtime tolerance 2 s.

## Review Focus

1. A render starts mid-drain and unloads the VLM: nothing in the tag queue is lost or counted as failed. Pinned in Task 3.
2. A drive drops during a watcher walk, so a listing is suddenly empty: no row becomes a move candidate or is cleaned. Pinned in Task 13.
3. A case-only rename on Windows or macOS (`A.PNG` to `a.png`): stays the same item, no duplicate row. Pinned in Task 16.
4. Two identical files, one moved: treated as new; the other item's ratings are untouched. Pinned in Task 16.
5. A large folder is still copying when Import starts: nothing half-copied is ingested, and an empty or growing file waits. Pinned in Task 13.

---

# Stage 1: protocol and grid

### Task 1: Registry core

**Files:**
- Create: `backend/services/background/__init__.py`, `backend/services/background/protocol.py`, `backend/services/background/registry.py`
- Test: `tests/test_background_registry.py`

**Interfaces:**
- Produces (protocol.py): `State` (str enum: idle, running, waiting, held, paused, stopped, failed); dataclasses `Progress(done: int, total: int, unit: str)`, `Capabilities(can_start, can_pause, can_stop: bool, confirm_stop: bool = False)`, `ProcessReport(state: State, progress: Optional[Progress], activity: str, error: Optional[str] = None, warnings_count: int = 0)`, `ProcessStatus` (spec 5.4; `to_dict()`); `BackgroundProcess` Protocol with `id, label, order, holds: tuple[str, ...]`, `capabilities() -> Capabilities`, `report() -> ProcessReport`, `async start/pause/resume/stop() -> None`.
- Produces (registry.py): `ProcessRegistry(state_path: Path, notify: Callable[[ProcessStatus], None], clock=time.monotonic)`; `register(p)` (raises `ValueError` on a `holds` cycle or duplicate id); `statuses() -> list[ProcessStatus]`; `status(id)`; `async command(id, action: Literal["start","pause","resume","stop"]) -> ProcessStatus` (raises `CommandRefused(code)`); `notify(id)`; `blocked(id) -> bool` (held, paused or stopped: an adapter calls this before auto-starting); `hold(targets, by) -> ContextManager`; `hold_for(targets, by, grace_s) -> ContextManager` (timer owned by registry); `async aclose()`.

- [ ] **Step 1: Write failing tests** with a `FakeProcess` recording calls:

```python
def test_hold_pauses_then_release_resumes(): ...          # fake.calls == ["pause", "resume"]; status held, held_by == ["T2I"]
def test_two_holders_resume_on_the_last_release(): ...
def test_owner_pause_survives_release(): ...              # held + command pause, release -> paused, no resume call
def test_play_while_held_stays_held_until_release(): ...
def test_stopped_ignores_holds_and_releases(): ...        # no pause/resume call, state stopped
def test_stopped_and_paused_persist_across_restart(tmp_path): ...  # new registry, register -> same state; holds do not persist
def test_held_process_holds_nothing(): ...                # declared holds drop when the holder leaves running
def test_holder_failure_releases(): ...                   # report() -> failed, target resumes
def test_cycle_in_holds_rejected(): ...
def test_hold_for_grace_releases_after_exception(): ...   # fake clock; body raises; released at grace_s
def test_failed_keeps_error_until_play(): ...
def test_notify_is_coalesced_to_two_per_second(): ...
```

- [ ] **Step 2: Run** `pytest tests/test_background_registry.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement.** The registry evaluates declared holds on every `notify`: target held while a declaring process reports `running`. Owner intent is stored per process (`none|paused|stopped`) and written atomically (`os.replace`) to `state_path`. On `register`, a persisted `paused` calls `pause()`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(background): process registry with holds and owner intent`.

### Task 2: Routes, WS channel, lifespan

**Files:**
- Create: `backend/api/background.py`
- Modify: `backend/main.py` (build registry in `lifespan`, close before `comfy_client.shutdown()`; `include_router`), `backend/dependencies.py`
- Test: `tests/test_background_api.py`

**Interfaces:**
- Consumes: `ProcessRegistry`.
- Produces: `get_registry() -> ProcessRegistry`, `set_registry(r)` in `backend/dependencies.py`. `GET /api/background` -> `{server_time, processes: [ProcessStatus]}`; `POST /api/background/{id}/{action}` -> status; unknown id 404; `CommandRefused` -> 409 `{code}`. WS: `ws_manager.broadcast_sync("background", "process_updated", status)`. State file `get_data_dir() / "background_state.json"`.

- [ ] **Step 1: Write failing tests** with `TestClient`: list shape, pause then resume round trip, 404, 409 with `code`, and that a `process_updated` broadcast fired.
- [ ] **Step 2: Run** `pytest tests/test_background_api.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(background): REST and WS transport`.

### Task 3: Durable tag queue

**Files:**
- Modify: `metascan/core/database_sqlite.py` (v8 migration, queue methods), `metascan/workers/embedding_worker.py:569-575`, `backend/services/vlm_tag_pump.py`, `backend/api/vlm.py`, `backend/api/similarity.py`
- Test: `tests/test_vlm_tag_queue.py`, update `tests/test_vlm_*` that read `vlm_pending.jsonl`

**Interfaces:**
- Produces (DB): table `vlm_tag_queue(file_path TEXT PRIMARY KEY REFERENCES media(file_path) ON DELETE CASCADE, enqueued_at TEXT, attempts INTEGER DEFAULT 0, last_error TEXT)`; `enqueue_tags(paths: Iterable[str]) -> int` (INSERT OR IGNORE); `next_tag_batch(limit: int) -> list[str]`, oldest first, `attempts < MAX_TAG_ATTEMPTS`; `finish_tag(path)`; `fail_tag(path, error)` (removes at `MAX_TAG_ATTEMPTS`, returns True when removed); `tag_queue_count() -> int`; `images_without_vlm_tags() -> list[str]`.
- Produces (pump): `VlmTagPump(client, db, *, model_id, concurrency, unless_stopped_since=None)`; `drain_once() -> int`; `pause()`, `resume()`, `progress() -> Progress`. Not-ready raises internally and ends the drain with items still queued.

- [ ] **Step 1: Write failing tests:**

```python
def test_v8_imports_leftover_lines_dedupes_and_removes_the_file(tmp_path): ...  # 3 lines, 2 same path, 1 no media row -> 1 row; file gone
def test_v8_runs_once_and_counts_line_is_debug_when_empty(): ...
def test_row_survives_until_tags_are_written(): ...           # pump killed mid-call -> row still there
def test_unload_mid_drain_keeps_queue_and_counts_no_failure(): ...  # fake client raises RuntimeError("VlmClient not ready") after 2; queue size unchanged for the rest, fail count 0
def test_missing_file_is_removed_without_a_failure(): ...
def test_third_failure_removes_row(): ...
def test_deleting_media_removes_queue_row(): ...
def test_pause_finishes_in_flight_and_takes_no_more(): ...
def test_retag_all_clip_enqueues_images_with_no_vlm_tags(): ...
def test_embedding_worker_enqueues_images_only(): ...
```

- [ ] **Step 2: Run** `pytest tests/test_vlm_tag_queue.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Add `_migrate_to_v8` after v7 at `database_sqlite.py:2468`, in one transaction with the version re-read under the write lock, as v7 does; failure raises `MigrationError`. The file path is `<index_dir>/vlm_pending.jsonl`, passed in; the worker's `db_mgr` calls `enqueue_tags`. `POST /api/vlm/retag` keeps `{job_id, total}`, enqueues, and starts a drain. Keep `DELETE /retag/{id}` returning `{status}`; it pauses nothing, only drops the rows that job added. Remove `_jobs` tracking of progress.
- [ ] **Step 4: Run** tests plus `pytest tests -k vlm -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(tagging): durable tag queue (v8), unload no longer drops work`.

### Task 4: Tagging process

**Files:**
- Create: `backend/services/background/tagging.py`
- Modify: `backend/api/similarity.py` (drain goes through the process), `backend/api/vlm.py` (`/unload` of general -> owner pause), `backend/api/chat.py`, `backend/api/prompt.py`, `backend/main.py`
- Test: `tests/test_background_tagging.py`

**Interfaces:**
- Consumes: `VlmTagPump`, `ProcessRegistry`.
- Produces: `TaggingProcess(registry, get_client, get_db, generation_busy)`, id `tagging`, order 30; `kick()` starts a drain when not `registry.blocked`. Writer prompting, chat and playground wrap requests in `registry.hold_for(["tagging"], by="Chat", grace_s=INTERACTIVE_GRACE_S)`.

- [ ] **Step 1: Write failing tests:** idle with empty queue; running shows `Progress(done, total, "images")`; pause -> state paused, `kick` does nothing; after restart the queue drains by itself; `/unload` of the general model -> `paused`, activity "Paused: model unloaded", and a later hold release does not resume; a render unload (no owner pause) resumes at release with `unless_stopped_since` re-snapshotted; chat request -> `held`, `held_by == ["Chat"]`, released 60 s after the last request; CLIP-only tier -> idle, activity says CLIP tagging is in use.
- [ ] **Step 2: Run** `pytest tests/test_background_tagging.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** `_schedule_vlm_drain_after_complete` becomes a call to `kick()`, which keeps the coalescing and `_wait_for_generation_to_settle`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(background): tagging process with pause, hold and chat grace`.

### Task 5: Embedding process

**Files:**
- Create: `backend/services/background/embedding.py`
- Modify: `backend/api/similarity.py`, `backend/services/auto_index.py`, `backend/main.py`
- Test: `tests/test_background_embedding.py`

**Interfaces:**
- Consumes: `EmbeddingQueue`, `_start_index_build`.
- Produces: `EmbeddingProcess`, id `embedding`, order 20, `holds=("tagging",)`; `similarity.embedding_progress() -> dict` (last progress, kept in memory); `kick(allow_vlm_tagging: bool)`.

- [ ] **Step 1: Write failing tests:** pause cancels the worker and state stays `paused` until `is_indexing` is false; resume starts `build_index(rebuild=False)`; a hold while indexing cancels and the release rebuilds; `AutoIndexScheduler` does not start while blocked; stop leaves `stopped` and a kick does nothing; a failed run keeps `error`; running holds tagging.
- [ ] **Step 2: Run** `pytest tests/test_background_embedding.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Add `registry.blocked("embedding")` to `AutoIndexScheduler`'s `is_busy`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(background): embedding process`.

### Task 6: T2I and I2V processes, ComfyUI source gate

**Files:**
- Create: `backend/services/background/generation.py`
- Modify: `metascan/core/comfy_client.py:620-631`, `metascan/core/t2i_runner.py`, `metascan/core/i2v_runner.py`, `backend/main.py`
- Test: `tests/test_comfy_source_gate.py`, `tests/test_background_generation.py`

**Interfaces:**
- Produces: `ComfyClient.set_source_paused(source: str, paused: bool)` with sources `t2i`, `i2v`, `storyboard`, classified from a job's `t2i_batch_id`, `i2v_source_path`, `panel_id`/`beat_id`; other jobs are never gated. A gated job is skipped in place, keeping queue order. `T2iProcess` id `t2i` order 40, `I2vProcess` id `i2v` order 50, both `holds=("tagging","embedding")`. A runner's prompting loop calls an injected `async wait_unpaused()` between steps.

- [ ] **Step 1: Write failing tests:** a gated source's queued job stays queued while another source's dispatches; running job not interrupted; order kept after ungating; `submit` still returns at once while gated; pause shows "Pausing after the current job" then paused; stop calls `cancel_batch` / `_abort`; a batch started by the owner clears `stopped`; active batch holds tagging and embedding, and submit does not wait on the hold.
- [ ] **Step 2: Run** both test files. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(background): T2I and I2V processes with a submission gate`.

### Task 7: Storyboard process

**Files:**
- Create: `backend/services/background/storyboard.py`
- Modify: `metascan/core/storyboard_runner.py`, `backend/api/storyboard.py`, `backend/main.py`
- Test: `tests/test_background_storyboard.py`

**Interfaces:**
- Produces: `StoryboardRunner.active_runs() -> list[dict]` (`{storyboard_id, kind, done, total}`), `cancel_runs()`, and a check between beats and panels raising `RunCancelled`, which ends in `synthesis_error` so the always-one-event rule holds. `StoryboardProcess` id `storyboard`, order 70, `holds=("tagging","embedding")`.

- [ ] **Step 1: Write failing tests:** synthesis, compile and compose each appear in `active_runs`; stop ends synthesis between beats and emits exactly one `synthesis_error`; pause gates submissions through `set_source_paused("storyboard", True)` and waits at the next check; resume continues.
- [ ] **Step 2: Run** `pytest tests/test_background_storyboard.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(background): storyboard process with run tracking and cancel`.

### Task 8: Upscaling, Taste and Writer training processes

**Files:**
- Create: `backend/services/background/upscale.py`, `taste.py`, `training.py`
- Modify: `backend/services/taste_service.py`, `backend/services/training_manager.py`, `backend/main.py`
- Test: `tests/test_background_services.py`

**Interfaces:**
- Produces: `UpscaleProcess` id `upscale` order 60, `holds=("tagging","embedding")`, pause and resume are `/pause-all` / `/resume-all` logic, activity notes the current file restarts. `TasteProcess` id `taste` order 90, wraps train and `reembed`; pause sets `TasteService.auto_paused`; `TasteService.cancel_training()` is new. `WriterTrainingProcess` id `training` order 100, `holds=("tagging","embedding")`, `Capabilities(can_start=False, ..., confirm_stop=True)`, aggregates briefs and scorecard; pause/resume/cancel call the manager.

- [ ] **Step 1: Write failing tests:** each maps its existing state to `ProcessReport`; a paused Taste does not auto-retrain and `POST /train` still works; Stop on training cancels; `gpu_busy` stops listing `index_build` once the embedding hold has landed; a run holds tagging and embedding.
- [ ] **Step 2: Run** `pytest tests/test_background_services.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Training start waits up to 30 s for the embedding hold, then refuses with today's `gpu_busy` reason.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(background): upscaling, taste and writer training processes`.

### Task 9: Import and Hygiene panels (wrapping)

**Files:**
- Create: `backend/services/background/import_process.py`, `backend/services/background/hygiene.py` (stub)
- Modify: `backend/api/scan.py`, `backend/main.py`
- Test: `tests/test_background_import.py`

**Interfaces:**
- Produces: `scan.scan_status() -> dict` from the existing `progress`/`file_progress` payloads. `ImportProcess` id `import`, order 10, dynamic `capabilities()`, `can_pause=False` this stage; `stop` is the existing `/cancel` path. During a Full clean: `registry.hold(["embedding","tagging","hygiene"], by="Full clean")`, released from `embedding` before the post-walk build. `HygieneProcess` id `hygiene`, order 80, idle, `can_start=False` this stage.

- [ ] **Step 1: Write failing tests:** running scan shows progress; Stop during a Full clean keeps marker and pending file and writes no tombstones (existing fixtures from `tests/test_taste_snapshot*`); hold is dropped however the run ends; a shutdown `CancelledError` deletes nothing.
- [ ] **Step 2: Run** `pytest tests/test_background_import.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** plus `pytest tests -k "scan or taste_snapshot" -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(background): import and hygiene panels`.

### Task 10: Pill and grid

**Files:**
- Create: `frontend/src/api/background.ts`, `frontend/src/stores/background.ts`, `frontend/src/components/background/ActivityPill.vue`, `ActivityGrid.vue`, `ActivityPanel.vue`
- Modify: `frontend/src/App.vue`, `frontend/src/components/layout/ContentSearchBar.vue`, `frontend/src/components/mobile/MobileShell.vue`, `frontend/src/views/StoryboardView.vue`
- Test: pure-TS unit tests of `pillSummary` by the Node 22 method in `reference_metascan_frontend_unit_tests`, in scratch; Playwright smoke by the `reference_metascan_fullstack_smoke` recipe (never :8700/:5173)

**Interfaces:**
- Produces: `useBackgroundStore()` with `processes`, `runningCount`, `anyFailed`, `command(id, action)`; refetch in `onWsConnect`; `useWebSocket('background', ...)` in `App.vue`; `pillSummary(list) -> {spinning, count, failed}`.

- [ ] **Step 1: Write failing unit tests** for `pillSummary`: spins only on `running`; count is running only; failed sets red; held, waiting and paused do not spin.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement.** Layout per `activity-grid.html` in spec 6: three columns, one on mobile, dimmed idle panels, dot colours as spec 6.2, tooltip with the full `error`, `confirm()` only when `capabilities.confirm_stop`. Nothing branches on a process id. Pill sits first in `.content-search-bar`; the training chip stays.
- [ ] **Step 4: Run** unit tests, `npm run build`, and the smoke: open pill, ten panels, pause Tagging against a fake, state updates, reload keeps it.
- [ ] **Step 5: Commit** `feat(frontend): activity pill and grid`.

### Task 11: Stage 1 documents and verification

**Files:**
- Create: `.claude/rules/background.md`, `docs/background-activity.md`
- Modify: `CLAUDE.md` (WS channel `background`, v8, drain wording), `.claude/rules/vlm-llama.md` (`vlm_pending.jsonl` references), `README.md`

- [ ] **Step 1: Write the documents.** The rule file carries how to add a process in five steps.
- [ ] **Step 2: Run** `make quality test` and `npm run build`. Expected: pass, bar the known flake.
- [ ] **Step 3: Commit** `docs: background protocol and activity grid`.

# Stage 2: always-on import

### Task 12: OS compatibility layer

**Files:**
- Create: `metascan/utils/fs_compat.py`
- Test: `tests/test_fs_compat.py`

**Interfaces:**
- Produces: `path_key(path: str, platform: str = sys.platform) -> str` (NFC on all, casefold on `win32`/`darwin`); `is_ignored_name(name: str) -> bool`; `is_partial_download(name: str) -> bool`; `can_open_for_read(path: str) -> bool` (a `PermissionError` or sharing violation is False). Lists per spec 12.

- [ ] **Step 1: Write failing tests**, parametrised on `platform`: NFD vs NFC equal on darwin; case equal on win32, different on linux; each spec 12 name, including `Thumbs.db`, `._x.png`, `.DS_Store`, `~$a.png`, `a.crdownload`, `a.part`, `a.tmp`, `a.download`.
- [ ] **Step 2: Run** `pytest tests/test_fs_compat.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Stdlib only.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(import): OS compatibility layer`.

### Task 13: Folder poller

**Files:**
- Create: `metascan/core/folder_poller.py`
- Test: `tests/test_folder_poller.py`

**Interfaces:**
- Consumes: `fs_compat`.
- Produces: `FileStamp(size: int, mtime: float)`; `PollResult(settled_new: list[str], vanished: list[str], changed: list[str], unavailable: list[str], walk_s: float)`; `FolderPoller(folders: Sequence[FolderSpec], probe: Callable[[str], str], skip_roots: Sequence[str], is_media: Callable[[str], bool])`, `seed(known: Iterable[str])`, `poll() -> PollResult`, `next_interval() -> float` = `max(WATCH_INTERVAL_S, 20 * walk_s)`.

- [ ] **Step 1: Write failing tests:**

```python
def test_new_file_settles_after_two_equal_polls(tmp_path): ...
def test_growing_file_does_not_settle(tmp_path): ...
def test_zero_byte_file_does_not_settle(tmp_path): ...
def test_unopenable_file_does_not_settle(tmp_path, monkeypatch): ...
def test_ignored_and_partial_names_skipped(tmp_path): ...
def test_paths_under_skip_roots_skipped(tmp_path): ...
def test_seed_reports_unknown_disk_files_as_new(tmp_path): ...
def test_missing_folder_reports_unavailable_and_nothing_vanishes(tmp_path): ...
def test_folder_empty_with_known_files_reports_unavailable(tmp_path): ...   # Review Focus 2
def test_listing_error_mid_walk_reports_unavailable(tmp_path, monkeypatch): ...
def test_interval_scales_with_a_slow_walk(): ...
def test_search_subfolders_false_stays_shallow(tmp_path): ...
```

- [ ] **Step 2: Run** `pytest tests/test_folder_poller.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with `os.scandir`, keyed by `path_key`, keeping the real spelling. `probe` is passed in because core cannot import `backend`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(import): folder poller`.

### Task 14: Moving a media row

**Files:**
- Modify: `metascan/core/database_sqlite.py`, `backend/services/taste_snapshot.py`
- Test: `tests/test_move_media.py`

**Interfaces:**
- Produces: `db.MOVE_PATH_COLUMNS` and `db.MOVE_EXCLUDED_COLUMNS`; `db.move_media_batch(pairs: Sequence[tuple[str, str]]) -> int`, one transaction, foreign keys deferred, skipping a pair whose target has a row; `taste_snapshot.rekey_pending(data_dir, pairs)`.
- Re-keyed: `media.file_path` and `file_path` in its JSON, `beat_images`, `folder_items`, `hindsight_briefs`, `i2v_videos.file_path`, `i2v_videos.source_path`, `generation_jobs.i2v_source_path`, `indices`, `judgment_events`, `media_categories`, `media_hashes`, `media_judgments`, `panel_videos`, `saved_prompts`, `scenes.reference_path`, `storyboard_subjects.reference_path`, `storyboard_subjects.reference_path_2`, `t2i_images`, `taste_scores`, `vlm_tag_queue`.
- Excluded: `media_tombstones.*`, `taste_models.params_path`, `training_runs.log_path`, `writer_adapters.file_path`, `storyboard_subjects.voice_ref_path` (check when implementing; re-key it if it can hold a media path), `media.file_size`.

- [ ] **Step 1: Write failing tests:**

```python
def test_every_path_column_is_listed(): ...          # walks sqlite_master for *path*/*file* columns on a fresh DB; fails on any not in either list
def test_move_keeps_grade_favorite_tags_categories_folders_lineage(tmp_path): ...
def test_move_rewrites_the_json_blob_path(tmp_path): ...
def test_target_with_a_row_is_skipped(tmp_path): ...
def test_failure_rolls_the_whole_batch_back(tmp_path): ...
def test_tombstones_are_untouched(tmp_path): ...
def test_pending_snapshot_entry_is_rekeyed(tmp_path): ...
def test_batch_of_5000_is_one_transaction(tmp_path): ...
```

- [ ] **Step 2: Run** `pytest tests/test_move_media.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** under `db.lock`, with `PRAGMA defer_foreign_keys = ON`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(library): move a media row with everything attached`.

### Task 15: FAISS renames

**Files:**
- Modify: `metascan/core/embedding_manager.py` (`FaissIndexManager`), `metascan/core/database_sqlite.py` (table in v8 if unreleased, else v9), `metascan/core/embedding_queue.py`, `metascan/workers/embedding_worker.py`, `backend/api/similarity.py`
- Test: `tests/test_faiss_renames.py`

**Interfaces:**
- Produces: `FaissIndexManager.rename(pairs) -> int`, keeps vectors, no re-embed; table `faiss_pending_renames(old_path PK, new_path, created_at)`, written by `move_media_batch`; applied by the embedding worker at start and by the queue when idle, then save and reload; `similarity.map_pending(path) -> str` for search results; `mark_tombstone_paths_rewritten` for old and new paths before the save.

- [ ] **Step 1: Write failing tests** (dim >= 32, normalised): rename keeps the vector; a move's search result shows the new path before it applies; pending rows clear after a save; never applied while `is_indexing`; tombstone at either path gets `path_rewritten = 1`; `embedded_model` evidence for a later delete at the new path still holds.
- [ ] **Step 2: Run** `pytest tests/test_faiss_renames.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** plus `pytest tests -k "tombstone or faiss" -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(library): rename FAISS rows without re-embedding`.

### Task 16: Move detection and the import pipeline

**Files:**
- Create: `metascan/core/move_detector.py`
- Modify: `backend/services/background/import_process.py`, `frontend/src/App.vue` (debounced media reload on the `library` event), `backend/main.py`
- Test: `tests/test_move_detector.py`, `tests/test_import_pipeline.py`

**Interfaces:**
- Produces: `Candidate(path, size, mtime, phash)`; `match_moves(new: Sequence[Candidate], vanished: Sequence[Candidate]) -> list[tuple[str, str]]` (old, new), rules per spec 11.1, `db.candidate_for(path) -> Candidate`, reading either `modified_at` shape. `ImportProcess` gains the polling loop: pause, resume and stop live; `can_pause` true except in a Full clean; one ingest thread; `library` / `media_added {count}` at most every 2 s; then `EmbeddingProcess.kick(allow_vlm_tagging=True)` debounced.

- [ ] **Step 1: Write failing tests:**

```python
def test_same_size_and_mtime_single_candidate_is_a_move(): ...
def test_two_candidates_is_not_a_move(): ...                     # Review Focus 4
def test_phash_mismatch_is_not_a_move(): ...
def test_different_mtime_needs_phash_and_one_candidate(): ...
def test_mtime_within_2s_counts(): ...
def test_epoch_and_iso_modified_at_both_read(): ...
def test_no_phash_matches_by_rule_one_only(): ...
def test_case_only_rename_keeps_the_item_on_windows_and_macos(tmp_path): ...  # Review Focus 3; updates the stored spelling
def test_dropped_file_ingests_and_broadcasts(tmp_path): ...
def test_folder_rename_moves_every_file_in_one_batch(tmp_path): ...
def test_vanished_row_is_not_cleaned_by_import(tmp_path): ...
def test_paused_import_catches_up_on_resume(tmp_path): ...
def test_restart_catches_up(tmp_path): ...
def test_one_bad_file_counts_a_warning_and_import_stays_running(tmp_path): ...
def test_unavailable_folder_is_waiting_and_touches_nothing(tmp_path): ...
def test_watch_directories_false_stays_idle(tmp_path): ...
```

- [ ] **Step 2: Run** both files. Expected: FAIL.
- [ ] **Step 3: Implement.** Vanished rows go to `pending_vanished` with a timestamp; new files wait one poll beside it so both sides of a move are seen. Rows left after `MOVE_GRACE_S` stay for hygiene to clean.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(import): always-on import with move detection`.

### Task 17: Retire the Import button

**Files:**
- Modify: `frontend/src/components/layout/ContentSearchBar.vue`, `frontend/src/views/LibraryView.vue`, the Config dialog (new Maintenance section with Full clean & rescan opening `ScanDialog`), `CLAUDE.md`, `docs/` pages describing Import
- Delete: `metascan/core/watcher.py`

- [ ] **Step 1:** Grep `docs/`, `tests/` and `frontend/src` for `watcher`, `Import` and Ctrl+S references and list them.
- [ ] **Step 2: Implement.** Remove the button, `@scan`, `openScan` hotkey; ScanDialog's mode is Full clean only. `watchdog` stays in requirements only if `prompt_store` still needs it.
- [ ] **Step 3: Run** `make quality test`, `npm run build`, and the Playwright smoke: drop a file into a temp scan folder, it appears in the grid with Import panel activity.
- [ ] **Step 4: Commit** `feat(import): remove explicit import, move Full clean to Config`.

# Stage 3: library hygiene

### Task 18: Checks framework and idle rule

**Files:**
- Modify: `backend/services/background/hygiene.py`, `backend/api/scan.py` (extract completion helpers), `backend/main.py` (activity middleware)
- Create: `backend/services/background/idle.py`
- Test: `tests/test_hygiene_framework.py`

**Interfaces:**
- Produces: `HygieneCheck` Protocol (`name: str`, `async run(ctx: HygieneContext) -> CheckResult`); `IdleTracker(clock).note_request()`, `idle_for() -> float`; middleware counts non-GET only; `scan.finish_ordinary_pass(report) -> None` extracted from `_scan`, used by both, so the marker removal stays in one place. `HygieneProcess` gains `can_start=True`, `pause`, `stop`, the idle timer.

- [ ] **Step 1: Write failing tests:** a pass starts only when no process is `running`, 120 s after the last non-GET, and 1800 s after the last pass; GETs do not reset it; play runs now, ignoring both; pause lands between checks; Import polling does not block it; a process starting holds it; a stop stays stopped.
- [ ] **Step 2: Run** `pytest tests/test_hygiene_framework.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** plus `pytest tests -k scan -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(hygiene): checks framework and idle rule`.

### Task 19: The checks and the mass-removal guard

**Files:**
- Create: `backend/services/background/hygiene_checks.py`
- Test: `tests/test_hygiene_checks.py`

**Interfaces:**
- Consumes: `_stale_paths_to_clean`, `_unavailable_folders`, `_reingest_generated_files`, `apply_pending_taste_snapshot`, `db.delete_media_batch`, `db.enqueue_tags`, `db.images_without_vlm_tags`, `finish_ordinary_pass`.
- Produces: `MissingFiles`, `NewFiles`, `ChangedFiles`, `MissingDerived`, `Finish`, in that order; `MassRemovalBlocked(folder, count)`; `HygieneProcess` state `failed` with "1,240 files missing from D:\Media: press play to confirm" and play applying that folder only.

- [ ] **Step 1: Write failing tests:**

```python
def test_missing_file_is_tombstoned_with_its_grade(tmp_path): ...
def test_vanished_within_move_grace_is_not_cleaned(tmp_path): ...
def test_nothing_under_an_unavailable_folder_is_cleaned(tmp_path): ...
def test_guard_needs_both_25_percent_and_100_rows(tmp_path): ...      # 101 of 400 blocks; 100 of 100 does not; 101 of 410 does not
def test_guard_blocks_one_folder_and_cleans_the_others(tmp_path): ...
def test_play_confirms_that_folder_only(tmp_path): ...
def test_incomplete_full_clean_adds_but_cleans_nothing(tmp_path): ...
def test_clean_pass_removes_the_marker_like_an_ordinary_scan(tmp_path): ...
def test_cancelled_pass_keeps_the_marker(tmp_path): ...
def test_changed_file_reingests_and_keeps_ratings(tmp_path): ...
def test_missing_thumbnail_and_phash_regenerate(tmp_path): ...
def test_unembedded_kicks_embedding_and_untagged_enqueues(tmp_path): ...
def test_resumes_nothing_while_generated_output_unreachable(tmp_path): ...
def test_a_new_check_is_one_class(tmp_path): ...
```

- [ ] **Step 2: Run** `pytest tests/test_hygiene_checks.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Page through the JSON blob's size and mtime, off the DB lock between pages.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(hygiene): sync checks and mass-removal guard`.

### Task 20: Final documents and verification

**Files:**
- Modify: `CLAUDE.md`, `.claude/rules/background.md`, `docs/background-activity.md`, `README.md`

- [ ] **Step 1: Update** documents for the `library` channel, polling, moves and hygiene; state the Windows and macOS testing limit.
- [ ] **Step 2: Run** `make quality test`, `npm run build`, and the full-stack smoke end to end: drop, appear, embed, tag against fakes; pause; render hold; move; delete from disk.
- [ ] **Step 3: Commit** `docs: always-on import and hygiene`.
