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
- Import offers Stop only (the `/cancel` path: a stopped Full clean is a cancelled one); Tagging pauses and stops between images (both let the images in flight finish; Stop reports `running` + `winding_down`, "Stopping after the current images", until they have, and keeps the rows queued: `can_stop` is false only when idle with an empty queue). A persisted `stopped` survives a restart; the startup kick, the retry timer and an embedding run's kick all check `registry.blocked` and do nothing, while rows keep accumulating. Play (`start`) clears it; an owner retag (`kick(by_owner=True)`) clears a Stop too, a pause stays; Training pauses at a step boundary; Taste finishes the run in flight; Hygiene is a placeholder.

## Adding a process

1. Write `backend/services/background/<name>.py`: a class with `id`, `label`, `order`, `holds`, and `capabilities()`, `report()`, `start/pause/resume/stop` (the protocol is in `protocol.py`). Report `winding_down` if its work ends late; keep `report()` cheap.
2. Construct it in `backend/main.py`'s `lifespan` (or the module that owns the work) and `registry.register(...)` it, after the thing it wraps exists. Persisted intent is applied at register.
3. Make the work's own triggers honour the registry: automatic starts check `registry.blocked(id)`, owner starts go through the owner path, and the work calls `registry.notify(id)` (outside its own locks) on every change of state, progress or activity. Anything non-process that must stop the new process uses `registry.hold(...)`.
4. List what it holds (`holds`) and what holds it: add its id to the `holds` of the processes that must suspend it, check the cycle test.
5. Test it in `tests/test_background_<name>.py` (states, holds, persisted intent, pause/resume/stop, no lock held across notify); the frontend needs nothing unless it adds a new capability, since the grid renders whatever `GET /api/background` returns and `process_updated` updates.
