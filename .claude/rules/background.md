---
paths:
  - "backend/services/background/**"
  - "backend/api/background.py"
  - "tests/test_background_*"
  - "frontend/src/components/background/**"
  - "frontend/src/types/background.ts"
  - "frontend/src/stores/background.ts"
---

# Background processes

Every long-running job (import, embedding, tagging, T2I, I2V, upscaling, storyboard, taste, writer training, hygiene) is a `BackgroundProcess` registered with the `ProcessRegistry` (`backend/services/background/{protocol,registry}.py`, `get_registry()` in `backend/dependencies.py`). The registry owns state evaluation, holds and the owner's intent; adapters (`backend/services/background/<name>.py`) only report and obey. Spec: `docs/superpowers/specs/2026-10-30-background-activity-design.md`.

## States

`State` is `idle | running | waiting | held | paused | stopped | failed`. A process reports one of `idle`, `running`, `waiting` (queued work it cannot do yet, e.g. the VLM is not ready) or `failed`; the registry derives `held`, `paused` and `stopped` and the effective `failed`:

- `intent == stopped` -> `stopped`; `intent == paused` -> `paused`; else any live holder -> `held`; else a sticky error with the process idle, waiting or failed -> `failed` (cleared when it next reports `running`); else the reported state.
- **Intent** is the owner's: `none | paused | stopped`, set only by `registry.command`, persisted in the state file (`{"intents": ...}`; only `paused` and `stopped` are saved) and re-applied on register (`persisted_intent`). A persisted pause is applied through the adapter's `pause()` once it registers. `registry.intent(pid)` reads it without evaluating reports, so `capabilities()` may call it.
- **Holds**: `holds` on an adapter lists the process ids it suspends while it is `RUNNING` (Upscaling and Training hold Tagging and Embedding; T2I, I2V and Storyboard likewise; Embedding holds Tagging). `registry.hold(targets, by)` / `hold_for(targets, by, grace_s)` add temporary holds for code that is not a process (chat, playground and writer prompting hold Tagging `INTERACTIVE_GRACE_S` after a request). `held_by` carries the holders' labels. A held process is told `pause()` and later `resume()` by `_reconcile`; holds never write intent. `_check_cycle` refuses a holds cycle at register.
- **`winding_down`** (on `ProcessReport`): told to pause, held or stopped but still saving. While it is true and the state is `running` the registry shows `running` (with the adapter's activity, e.g. "Saving, then pausing") and keeps the process's holds and `held_by` until the adapter reports otherwise. Use it whenever the work cannot stop at once (Embedding's worker, Training's step boundary, Import's task, a generation batch with a job in ComfyUI).

## Commands

`registry.command(pid, action)` (`POST /api/background/{id}/{start|pause|resume|stop}`, refusals are 409 `{code}`; 404 `unknown_process`): `pause` needs `can_pause` and refuses on `stopped`; `stop` needs `can_stop`; `resume` only from `paused` (`not_paused`); `start` clears a pause or a stop and calls `start()` unless it was only paused (then it resumes). The process calls a command implies are queued under the lock and run in order on the event loop (`settle`); a failure lands in `cmd_error`, never as an exception to the caller. `Capabilities.confirm_stop` makes the UI ask first (Training: stop deletes checkpoints).

## Rules for adapters

- **Never call `registry.notify` while holding your own lock.** The registry calls `report()` and `capabilities()` outside its lock and they take yours; notify from inside yours is a lock-order inversion. Update state under your lock, release it, then notify. `notify` is safe from any thread and re-evaluates every process (a hold change moves others); events are coalesced to `NOTIFY_MIN_INTERVAL_S` per process, so notify freely.
- `report()` and `capabilities()` must be cheap, side-effect free and safe from any thread (cache file reads, e.g. Upscaling's `REPORT_TTL_S`); an exception in either marks the process failed / unusable, never breaks the rest. Everything else runs on the server loop.
- Activity lines carry counts and stage words only: never a prompt, brief, example or path (a bare file name at most); a failure shows the exception type.
- Pause is the process's own gate, not a registry cancel; Stop is the process's own cancel path (the route's logic, so the same checkpoint and tombstone rules apply).
- **`by_owner`.** An action that originates from the owner (a click on the panel, a route the owner calls such as `POST /api/similarity/build`, retag, the T2I/I2V start routes) may clear a `stopped` intent: starting is an explicit wish. A background one never does: the post-scan embedding follow-up passes `by_owner=False` (`similarity.build_index(rebuild=False, by_owner=False)`), and every automatic kick (auto-index, drains after a build, taste auto-retrain, startup) must check `registry.blocked(pid)` / `registry.intent(pid)` and do nothing while the process is held, paused or stopped. A paused or held process the work arrives for queues it; a stopped one drops the automatic trigger and keeps the durable queue.

## Pause limits per process

- **ComfyUI-backed (T2I, I2V, Storyboard)**: pause is a gate. `ComfyClient.set_source_paused` keeps that source's queued jobs queued and the runner blocks between steps; a job already in ComfyUI is not interrupted. The panel says "Pausing after the current job" (`winding_down`) until no job of the source is in ComfyUI and no prompt step is mid-call to the writer. A paused Random batch keeps its writer hold and so the writer stays resident. Stop cancels every active batch through `cancel_batch`.
- **Embedding**: pause and hold use the cancel path: the worker saves and exits and `has_embedding` keeps the remainder durable. The panel says "Saving, then pausing" until the worker process has exited (`is_indexing` false). Resume starts an incremental build, which costs a CLIP reload; a rebuild cut short resumes as an incremental one.
- **Upscaling**: pause is the queue's pause, which kills the running worker and resets that file to 0 ("Paused; the current file restarts"). `queue.gated` keeps the poller from claiming anything while paused; a task the owner adds meanwhile is paused on arrival.
- Import: pause and stop halt the watcher (no poll, no ingest, no move; a file being ingested finishes first, shown as winding down) and Play re-seeds it from the database so nothing dropped meanwhile is missed; pause is not offered during a Full clean, and Stop there is the `/cancel` path (a stopped Full clean is a cancelled one, so the Stop asks first); Tagging pauses and stops between images (both let the images in flight finish; Stop reports `running` + `winding_down`, "Stopping after the current images", until they have, and keeps the rows queued: `can_stop` is false only when idle with an empty queue). A persisted `stopped` survives a restart; the startup kick, the retry timer and an embedding run's kick all check `registry.blocked` and do nothing, while rows keep accumulating. Play (`start`) clears it; an owner retag (`kick(by_owner=True)`) clears a Stop too, a pause stays; Training pauses at a step boundary; Taste finishes the run in flight; Hygiene stops between rows or checks (pause and stop alike, a hold arrives as a pause) and offers Play, Pause and Stop.

## Import and Library hygiene (`import_process.py`, `hygiene.py`, `hygiene_checks.py`, `idle.py`)

The full rules are in CLAUDE.md (the "Import is always on" and "Library hygiene" bullets); the ones to keep in mind when touching these files:

- **Import polls** (`FolderPoller`, `os.scandir`, interval `max(5 s, 20 x the last walk)`): file notifications are not reliable on 9p, network or exFAT drives. A new file is ingested after two polls with the same size and mtime, non-empty and openable; partial downloads and named OS junk are ignored (no blanket dotfile rule). `watch_directories` false turns it off. It re-seeds from the database on start, resume, after a scan and when the folder list changes. A folder `_probe_folder` cannot see is `unavailable`: nothing under it vanishes. Windows and macOS are covered by simulated spellings only.
- **Import never deletes a row.** A vanished file gets a timestamp (`vanished_since`); only hygiene removes the row, `MOVE_GRACE_S` = 60 s later. A moved file is matched (`match_moves`: ambiguity is "new file") and re-keyed with everything attached in one transaction (`move_media_batch_applied`, every table in `MOVE_PATH_COLUMNS`; `tests/test_move_media.py` fails on a path column on neither list). The same transaction queues `faiss_pending_renames`; the FAISS rows are renamed without re-embedding (`faiss_renames.apply_pending_renames`, by the embedding worker or by the server while none is alive, never both).
- **`scan.move_gate`**: take it with `acquire(blocking=False)` in a worker thread (or poll it in a coroutine, as `start_scan` does, with `MOVE_GATE_WAIT_S`) around "no scan running? then move / delete / finish". A coroutine must never hand a blocking `acquire` to a thread it cannot cancel.
- **Hygiene is in nobody's `holds`.** Its idle rule (no other process running, no non-GET for 120 s, 30 min since the last pass; Play ignores the timers, never the other processes) decides when it starts; it yields between rows through `HygieneContext.stopped()`, which latches. A new check is an object with `name` and `async run(ctx) -> CheckResult` added to `default_checks()`; set `always = True` only for a check that must see an interrupted pass (`Finish`). Checks do blocking work with `asyncio.to_thread`, report with `ctx.report(activity, done, total)` (counts and bare names; `_safe_activity` strips paths) and never delete without `delete_media_batch` under `move_gate`.
- **Mass-removal guard**: more than 25 % of a folder's rows and more than 100 missing blocks that folder (the pass ends `failed`, red, "N files missing from <folder>: press play to confirm"); Play confirms the shown count. Rows under no configured folder are one group. Changing `MissingFiles` must keep the order: move matching, folder/mount checks (`_stale_paths_to_clean`), generated-root probe, grace, guard, then delete.
- `vlm_tag_exhausted` keeps hygiene from re-queueing images tagging gave up on or that returned no tags (cleared by a forced retag); `images_without_vlm_tags(for_hygiene=True)` also leaves out T2I / I2V output. `library` WS events: `media_added {count}` (Import, at most one per 2 s) and `media_removed {count}` (hygiene).

## Adding a process

1. Write `backend/services/background/<name>.py`: a class with `id`, `label`, `order`, `holds`, and `capabilities()`, `report()`, `start/pause/resume/stop` (the protocol is in `protocol.py`). Report `winding_down` if its work ends late; keep `report()` cheap.
2. Construct it in `backend/main.py`'s `lifespan` (or the module that owns the work) and `registry.register(...)` it, after the thing it wraps exists. Persisted intent is applied at register.
3. Make the work's own triggers honour the registry: automatic starts check `registry.blocked(id)`, owner starts go through the owner path, and the work calls `registry.notify(id)` (outside its own locks) on every change of state, progress or activity. Anything non-process that must stop the new process uses `registry.hold(...)`.
4. List what it holds (`holds`) and what holds it: add its id to the `holds` of the processes that must suspend it, check the cycle test.
5. Test it in `tests/test_background_<name>.py` (states, holds, persisted intent, pause/resume/stop, no lock held across notify); the frontend needs nothing unless it adds a new capability, since the grid renders whatever `GET /api/background` returns and `process_updated` updates.
