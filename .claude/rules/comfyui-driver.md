---
paths:
  - "metascan/core/comfy_*.py"
  - "metascan/core/workflow_validation.py"
  - "backend/api/comfy.py"
  - "backend/services/comfy_service.py"
  - "frontend/src/api/comfy.ts"
  - "tests/_fake_comfy_server.py"
  - "tests/test_comfy_*.py"
  - "tests/test_fake_comfy_server.py"
  - "tests/test_workflow_validation.py"
  - "tests/test_t2i_workflow_validation.py"
---

# The ComfyUI driver, its protocol edges and workflow presets

Moved out of the root CLAUDE.md so it loads only when you work with the files above;
the root CLAUDE.md still applies.

- **ComfyUI is driven, not just parsed.** `metascan/core/comfy_client.py`
  submits jobs to a ComfyUI server (the extractors in
  `metascan/extractors/comfyui*.py` remain read-only metadata parsers, a
  separate concern). A workflow is registered as an API-format graph whose
  nodes are titled with the `MS_*` convention (`MS_POSITIVE`, `MS_NEGATIVE`,
  `MS_SEED`, `MS_LATENT`, `MS_SAVE`, optional `MS_LORA` / `MS_LORA_STACK` /
  `MS_REF_IMAGE` / `MS_RESOLUTION`);
  `comfy_bindings.resolve_bindings` maps titles to node ids at registration
  time and **fails loudly** on a missing required title. Titles are used
  rather than node ids because ComfyUI renumbers nodes on re-save.
- **Metascan owns the ComfyUI job queue.** `ComfyClient` holds at most
  `comfy.in_flight` jobs inside ComfyUI at a time so a user-requested reroll
  can jump the queue and cancellation stays responsive. One persistent
  WebSocket per app (not per job) consumes ComfyUI's event stream; the
  `execution_error` node type and message go verbatim into
  `generation_jobs.error`.
- **Generated images are fetched over HTTP, never read from disk.**
  `collect_outputs` pulls each image via `/view` and writes it under
  `comfy.output_root`, so a remote or containerized ComfyUI works unchanged
  and there is no watcher race. Ingest goes through the public
  `Scanner.ingest_file`, wrapped in `asyncio.to_thread` — it does SQLite
  writes and Pillow work, and running it on the event loop stalls the
  WebSocket reader.
- **ComfyUI's protocol has five sharp edges; `tests/_fake_comfy_server.py`
  models all five and must keep doing so.** Verified against ComfyUI's
  `server.py` / `execution.py` / `main.py`.
  1. **`/interrupt` must carry `{"prompt_id": ...}`.** A bodyless POST is
     an explicit *global* interrupt that kills whatever prompt is
     currently executing — and since ComfyUI runs one prompt at a time
     while metascan keeps `in_flight` (default 2) queued there, the job a
     user cancels is routinely the *pending* one and the bystander is a
     real generation. Go through `ComfyClient._stop_prompt`.
  2. **An interrupted prompt reports `execution_interrupted`, not
     `execution_error`** (`handle_execution_error` branches on
     `InterruptProcessingException`). It must be handled as terminal or
     the job never leaves `running` and permanently burns an `in_flight`
     slot.
  3. **`/history` is written only at end of prompt**, by
     `PromptQueue.task_done()` — after every `executed` frame and after
     `execution_success` (which is emitted from *inside* `execute()`).
     Collection therefore triggers on `execution_success` /
     `executing {node: null}`, never on `executed`, and
     `_await_history` retries briefly before treating an absent entry as
     a job failure. Reading history on the first `executed` silently
     lost every image whenever `MS_SAVE` wasn't the last node to run.
  4. **`executing` is overloaded**: `{node: <id>}` is a per-node ping,
     `{node: null}` is end-of-prompt. Only the latter is actionable.
  5. **A `clientId` must never be reused across websocket connections.**
     ComfyUI keeps ONE socket per `clientId`, routes a prompt's frames
     only to the socket registered under the id that submitted it, and
     its `/ws` teardown does `self.sockets.pop(sid, None)`
     *unconditionally* — it never checks the registered socket is still
     its own. Reconnecting under the same id races the old connection's
     teardown; when ComfyUI gets to it late (event loop busy loading a
     model, FIN delayed by the WSL localhost relay) the pop deletes the
     LIVE registration. The socket stays open and answers every ping but
     is deaf: no progress, no `execution_success`, nothing collected, the
     row sits in `running` forever. Observed for real — four hours of
     pongs and zero events while two jobs finished. `_reader_loop`
     therefore replaces `client_id` via `_fresh_client_id()` on every
     drop (before the backoff sleep, so submits made while down carry
     the next connection's id).
- **The ComfyUI websocket is the fast path, not a reliable one —
  `/history` is the record.** A frame sent during a reconnect gap, or
  addressed to a previous `clientId`, is gone; ComfyUI never replays.
  `ComfyClient._reconcile_loop` polls `/history/{prompt_id}` for every
  entry in `_prompt_to_job` (≤ `in_flight` GETs per
  `_RECONCILE_INTERVAL_SECONDS`, plus one pass on every (re)connect),
  waits `_RECONCILE_GRACE_SECONDS` so a healthy socket's trailing frame
  wins, then completes the job through the normal `_on_prompt_end` /
  `_finish_job` paths — `_collecting` and the terminal-state guard make
  a race with the websocket a no-op. `_history_outcome` tells an
  interrupt from a node failure by `status.messages`; both have
  `status_str == "error"`. A reconcile that fires logs a WARNING: it
  means the event stream lost something. The socket drop itself is also
  a WARNING now (it was DEBUG, which is why a dead stream went unnoticed
  for hours), and `GET /api/comfy/status` reports `connected`.
- **ComfyUI job rows are reconciled at startup, not just at reconnect.**
  `ComfyClient.start()` calls `_rehydrate_jobs` once: `queued` rows are
  re-enqueued into `_queue` (that's what makes "state survives a restart"
  true), and `running` rows left by a dead process are marked `failed`
  with an "interrupted by a restart" message rather than re-adopted —
  no event will ever arrive for them and re-adopting would
  over-subscribe `in_flight`. `_rehydrate_prompt_map` is the
  *reconnect*-time path and deliberately does neither. `_rehydrate_jobs`
  assumes one process per database — correct for `run_server.py`'s
  single uvicorn worker, but running with `workers > 1` would have each
  worker's startup mark the *other* workers' still-running jobs `failed`.
- **`generation_jobs.preset_id` has no `ON DELETE` clause, on purpose.**
  Deleting a used preset raises `sqlite3.IntegrityError`;
  `ComfyService.delete_preset` translates it into `PresetInUseError` and
  the route answers **409** naming the job count. Do not add `ON DELETE
  CASCADE` (it would destroy job history) and do not make the column
  nullable (it would orphan it).
- **`generation_jobs.panel_id` has no `REFERENCES` clause.** The `panels`
  table arrives in Phase B of the storyboard feature; with
  `PRAGMA foreign_keys = ON`, an INSERT naming a foreign key to a missing
  table fails at runtime, and SQLite cannot add a foreign key to an existing
  table without rebuilding it.
- **Workflow presets carry an optional dialect tag and are validated per
  (target, mode).** `workflow_presets.video_target`/`video_mode` (nullable
  TEXT, idempotent adds that must stay AFTER the kind-CHECK rebuild block
  in `_init_database` — the rebuild recreates the table from an explicit
  column list and would silently drop columns added before it).
  `metascan/core/workflow_validation.py` is the pure validation layer over
  `comfy_bindings`: `validate_workflow(workflow, kind, target, mode)`
  returns a `ValidationReport` of every error/warning at once (duplicate
  titles, unknown `MS_*` titles, missing required titles, missing widgets)
  plus `TitleFix` suggestions — the one auto-fix computable without
  knowing a target's node classes is renaming a misspelled `MS_*` title
  via fuzzy match (`apply_fixes`). Per-(target, mode) validators live in
  its `_VALIDATORS` registry; only `("minimax", "ref2va")` is implemented
  (warnings for missing `MS_REF_IMAGE`/`MS_AUDIO`/`MS_DURATION`, unused
  keyframe slots) — an unregistered pair gets a single `no_validator`
  warning, never an error, so future targets don't hard-fail. `POST
  /api/comfy/presets` blocks on validation errors with a structured 400
  (`{code:"validation_failed", findings, fixes, fixed_workflow?}`) and
  returns `warnings` on success; `POST /api/comfy/presets/validate` is
  the pure dry-run the registration dialog's Validate/Apply-fixes flow
  uses. `StoryboardRunner.generate_video` rejects a preset whose tag
  mismatches the storyboard's target/mode (untagged legacy presets pass),
  which is what makes the association binding. `VIDEO_TARGETS`/
  `VIDEO_MODES` in `workflow_validation.py` are the canonical axes to
  extend when a new dialect (ltx, wan, …) lands.
- **Presets are updated in place, workflow only.** `PUT
  /api/comfy/presets/{id}` (`{workflow}`) replaces `workflow_json` and the
  bindings snapshot via `DatabaseManager.update_workflow_preset_workflow`
  and bumps `updated_at`; name, kind and the dialect tag are NOT
  updatable (extra body keys are ignored), and validation runs against
  the preset's STORED kind/target/mode. The id is preserved on purpose —
  `i2v.fast_preset_id`/`quality_preset_id`, storyboards and
  `generation_jobs.preset_id` all reference it, and `DELETE` 409s once
  jobs exist, so delete + re-register is not an option for a used preset.
  It goes through `ComfyService`, not `ComfyClient` (no ComfyUI
  connection needed). `GET /api/comfy/presets/{id}` returns the parsed
  `workflow` (the list route still omits the graph). In
  `PresetRegistrationDialog.vue`, clicking a row in "Existing presets"
  enters update mode (`editingId`): the row stays highlighted, the form
  is populated, name/model/mode are locked, Validate uses the preset's
  own `editingKind` (legacy `t2i`/`ref` presets are updatable too), and
  the button reads **Update** instead of **Save**; clicking the selected
  row again calls `resetForm()`. After a successful update the selection
  and form are kept so the user can keep iterating. `selectSeq` drops
  out-of-order preset loads.
