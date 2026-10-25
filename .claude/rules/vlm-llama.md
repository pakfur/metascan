---
paths:
  - "metascan/core/vlm_*.py"
  - "metascan/utils/llama_server.py"
  - "backend/api/vlm.py"
  - "backend/api/chat.py"
  - "backend/services/vlm_tag_pump.py"
  - "backend/services/scan_dispatch.py"
  - "frontend/src/api/vlm.ts"
  - "scripts/build_llama_server.sh"
  - "backend/services/vlm_roles.py"
  - "metascan/core/writer_adapters.py"
  - "tests/test_writer_adapter_serving.py"
  - "setup_models.py"
  - "tests/_fake_llama_server.py"
  - "tests/test_vlm_*.py"
  - "tests/test_llama_*.py"
  - "tests/test_setup_models_*.py"
---

# The Qwen-VL tagging pipeline and llama-server

Moved out of the root CLAUDE.md so it loads only when you work with the files above;
the root CLAUDE.md still applies.

- **Qwen3-VL VLM tagging.** A long-running `VlmClient`
  (`metascan/core/vlm_client.py`) supervises a `llama-server` subprocess for
  generative tagging on hardware tiers where it's viable. CLIP tagging
  remains the fallback for `cpu_only` and `cuda_entry`. The DB layer
  arbitrates merging via `_update_indices` and `add_tag_indices` —
  VLM-source tag rows survive CLIP rescans (the demote-on-rescan logic in
  `database_sqlite._update_indices` preserves them). Engine choice rationale
  is in `docs/superpowers/specs/2026-05-02-qwen3vl-tagging-design.md` §11.
  `metascan/core/vlm_models.REGISTRY` now spans two families — four
  Qwen3-VL Abliterated sizes (`qwen3vl-2b/4b/8b/30b-a3b`) plus
  `qwen38-27b` (Qwen3.8 27B Abliterated, dense hybrid Gated DeltaNet) —
  and `qwen38-27b` is the `cuda_workstation` ≥22 GB VRAM recommendation
  (`qwen3vl-8b` remains the pick below that floor).
- **Two VLM roles: `writer` and `general`.** `backend/services/vlm_roles.py`
  `VlmRoles` maps the `writer` role (T2I / I2V prompt writing — runners get
  `get_writer_vlm`) and the `general` role (tagging, retag, chat, prompt
  playground, storyboard — `get_vlm_client()` with no argument) to clients.
  Config `models.writer_vlm` / `models.general_vlm`; unset writer defaults to
  `WRITER_DEFAULT_MODEL` (`qwen3vl-8b`) when installed, else the general
  model; unset general is the first VLM in `preload_at_startup`, else
  today's hardware pick. Equal ids share ONE
  client (one llama-server); `set_model` rebuilds and shuts down a client
  no role uses. A spec with `writer_allowed=False` (`qwen38-27b`) is never
  the writer (422 `writer_model_not_allowed`). Unloading for ComfyUI shuts
  down every distinct client once (`get_all_vlm_clients`), sparing only
  the writer while another T2I Random batch is still prompting.
  `pick_vlm_model` prefers the loaded model, then the client's
  `preferred_model_id` (stamped by `VlmRoles`). `POST /api/vlm/active`
  without `role` still targets general; it 409s on a missing model and
  persists the role only after the swap succeeds (`VlmRoles.activate`).
  A client no role uses is **retired** (`VlmClient.retired`): `start` /
  `ensure_started` raise `VlmError("client retired")` instead of
  respawning an untracked llama-server. Two distinct role models stay
  co-resident only when their `approx_vram_gb` sum fits the detected VRAM
  (CUDA VRAM, MPS unified RAM; unknown = no) minus 2 GB; otherwise they
  **take turns** via `VlmClient.before_spawn` (`VlmRoles._make_room`).
  The mode is per request, from the `background_vlm_work()` contextvar
  (set by `VlmTagPump.drain_once` and `_run_retag_job`; tasks inherit it).
  An **interactive** spawn (writer prompting, chat, playground,
  storyboard) waits at most `IDLE_TIMEOUT_S` (30 s) for the other client's
  in-flight requests (`VlmClient.wait_idle`), then evicts it — at once
  when all it is doing is background work (`background_only()`:
  background requests in flight, no interactive request / waiter, no
  hold; those requests are retried after the eviction). A **background**
  spawn never force-evicts: it waits with no cap until the other client
  is not `busy_for_background(BACKGROUND_GRACE_S)` (nothing in flight or
  waiting for residency, no `hold()`, not loading, idle for 30 s); an
  interactive caller that starts waiting for the same client meanwhile
  bounds that wait from then on. A T2I Random batch holds the writer
  (`VlmClient.hold()` in `T2iRunner._produce`) for its whole prompting
  phase, so the tag drain never evicts the writer between two prompts:
  a writer run overlapping a tag batch costs at most 2 swaps
  (`test_a_writer_run_overlapping_a_tag_batch_swaps_at_most_twice`). An unset writer whose
  default is not installed and whose general model is not writer-eligible
  stays `qwen3vl-8b` (missing): T2I falls back with
  `writer_missing_hint`, I2V raises `I2vUnavailableError` with it.
  `refresh()` (GET /roles, after a VLM download) re-applies the
  unset-writer rule without a restart.
  A client swapped out for the other role is `evict()`ed
  (`swapped_out=True`): its next `generate_tags` / `generate_text` /
  `chat_stream` re-ensures residency first (`_resident_request`), and a
  request whose client was force-evicted mid-call is retried, so a tag
  batch survives the writer taking over. A client is busy for swapping
  while requests are in flight, while it is spawning/loading, and for
  `SWAP_GRACE_S` after its last request (no per-item swaps). An explicit
  `shutdown()` (ComfyUI unload, /unload) clears `swapped_out` and bumps
  `_explicit_stops`: nothing auto-restarts it, and a background re-ensure
  that began before it aborts (`VlmError("VLM was unloaded…")`) instead of
  spawning. Chat / playground accept a swapped-out client as ready.
  `evict()` keeps the client non-resident for its whole shutdown
  (`swapped_out` set first, `_quiesced` cleared; `_is_resident()` also
  checks `_stopping`), so retries and new requests wait and re-ensure
  rather than hit the dying server. A client that is spawning/loading is
  never evicted: `_make_room` waits for `wait_loaded` (bounded by the
  600 s load timeout) and the swap grace restarts at READY so the waiting
  requests run first. Internal model changes inside `start()` use `_stop()`,
  not `shutdown()`. `_make_room` re-checks the other client after its
  waits: stopped meanwhile (an explicit unload landed) → nothing to
  evict; explicitly reloaded meanwhile (`_explicit_stops` changed) → left
  alone. `evict()` itself is a no-op on a non-resident client, so it can
  never mark a stopped one `swapped_out`. `_resident_request` captures
  `_explicit_stops` when the request begins and its re-ensure honours it
  (`_ensure_resident(since=…)`). A start that times out tears the
  process down and lands in ERROR (no zombie LOADING); `_is_loading()`
  treats a LOADING state whose `_health_task` is done as abandoned, so
  `wait_loaded` / `wait_idle` do not wait out the load timeout on it. A
  failed background reload backs off (`_RESPAWN_BACKOFF_SECONDS`, reset at
  READY): requests inside the window raise `VlmError("… retry in Ns")`
  fast instead of evicting the other role again for every attempt. A
  load that reached READY (`_ready_seen`) and was evicted before its
  loader's `_wait_ready` resumed counts as loaded (`ensure_started`
  returns; the next request re-ensures) — only a client evicted before
  READY raises "ended in state stopped". `_resident_request` checks
  `_is_resident()` after its own `_ensure_resident` returns and re-ensures
  again (bounded by `_EVICTION_RETRIES`) instead of running the request
  against the stopped server.
  The post-scan tag drain (`similarity._schedule_vlm_drain_after_complete`,
  run on the captured server loop, coalesced) is a background re-ensure
  too: `_start_index_build` snapshots the general client's
  `_explicit_stops`, and `VlmTagPump(unless_stopped_since=…)` passes it to
  `VlmClient.ensure_started(…, unless_stopped_since=…)`, which refuses to
  spawn after an explicit unload since the scan began; the batch stays
  queued in `vlm_pending.jsonl` for the next drain. The drain also waits
  while ComfyUI generation is in flight (`similarity.
  set_generation_busy_probe`, installed from the lifespan with
  `auto_index.generation_busy`; a failing probe counts as busy), polling
  every `_VLM_DRAIN_DEFER_POLL_S` and running on its own once the work
  settles — before each pass, including coalesced reruns. A drain that
  had to wait re-snapshots `_explicit_stops` after the wait: the render's
  own VLM unload is then behind it and must not strand the batch.
- **Prompt-writer adapters (LoRA) ride the writer's llama-server.**
  `VlmRoles(adapter_source=…)` (wired in the lifespan to
  `writer_adapters.servable_adapter_files`: `ready`/`untested` rows whose
  `data/models/vlm/adapters/<id>/adapter.gguf` exists) gives the writer
  client `lora_paths` = the adapters whose `base_model` is the writer model
  (a general-only client gets none); `_build_command` appends `--lora <p>`
  per path plus `--lora-init-without-apply`, and `_loaded_lora_paths` is
  what the running server spawned with. `generate_text(lora_id=…)` /
  `(lora_path=…)` adds `"lora": [{"id": <position>, "scale": 1.0}]` — a
  base request has NO `lora` key (byte-identical to before; tested).
  `lora_path` is resolved against the running server right before the POST
  (with the HTTP client snapshot, after any image encoding). An adapter
  added after the spawn restarts the server (`_ensure_lora` /
  `prepare_lora`) only once it is not busy — nothing in flight or waiting,
  NO `hold()`, not loading — never evicting work or breaking a T2I Random
  batch's prompting hold; still busy after `lora_wait_s` (60 s) →
  `AdapterLoadingError` (409 `adapter_loading`, "try again when the current
  batch finishes") and the pending restart runs at the next idle. The
  caller's OWN hold never counts (`_OWN_HOLDS` contextvar, set by `hold()`),
  the wait returns as soon as another caller's restart loaded the adapter,
  and the restart is single-flight (`_lora_restart_lock`). POST
  /api/t2i/batches calls `prepare_lora(wait_s=15)` before registering a
  Random batch and refuses it with 409 `adapter_loading` when the writer
  stays busy; a step whose adapter is not loaded falls back at once (no
  retry). Adapter 409s carry `writer_id` so the dialog falls back the Writer
  pick only when it is the refused adapter, else drops the held draft. One withdrawn and no
  longer loaded raises `AdapterNotLoadedError` (409 `adapter_unavailable`).
  `refresh_adapters()` restarts a stale client only when idle
  (`busy_for_background(swap_grace)` false: nothing in flight or waiting,
  no `hold()`, not loading) — via `restart_for_loras()` = `evict()`, so it
  comes back on its next request — else `restart_pending` until it is; a
  stopped client just spawns with the new list. `withdraw_adapter(id)`
  (DELETE `/api/writers/adapters/{id}` calls it BEFORE removing files, for
  Windows' mapped-file lock) answers 409 `adapter_files_busy` at once when it
  returns False (a running or loading server still maps the GGUF) instead of
  relying on the removal failing; a refused withdraw puts the id back, so the adapter
  stays served and no restart is left pending; a withdrawn id is forgotten once the
  registry no longer lists it. A registry change leaves an idle writer
  `stopped` until its next request (user-visible: no model shown loaded). Tests: `tests/test_writer_adapter_serving.py` (the fake server's
  `--record` file logs argv and request bodies).
- **VLM image-only guard.** `VlmClient.generate_tags` short-circuits with
  `[]` for any path whose suffix isn't in `_SUPPORTED_IMAGE_EXTS`. Both
  the scan-time enqueuer (`embedding_worker.py`) and the retag job
  (`backend/api/vlm.py:_run_retag_job`) filter videos upfront so progress
  totals are honest. Use `VlmClient.is_image_path(path)` from new call
  sites instead of duplicating the extension list.
- **VLM image resize.** `_encode_image_b64` decodes via Pillow and
  resizes to `_IMAGE_MAX_EDGE = 1024` before JPEG-encoding. Skipping the
  resize previously blew past the 8K context budget on 2K SDXL renders
  (`HTTP 400 the request exceeds the available context size`). Tagging
  doesn't need fine-print readability — bumping the cap only makes sense
  alongside a `--ctx-size` bump in `_build_command`.
- **GBNF grammar gotcha.** `\-` is not a valid GBNF escape; bad
  grammars crash `llama-server` with SIGSEGV inside
  `llama_grammar_init_impl`, triggering an infinite respawn loop on every
  tag request. Hyphens must be literal (place at the start or end of a
  character class). The tagging grammar lives in
  `metascan/core/vlm_prompts.py:TAGGING_GRAMMAR`.
- **`llama-server` local override.** `binary_path()` returns
  `data/bin/local/<name>` when present, else the bundled
  `data/bin/<name>`. `scripts/build_llama_server.sh` populates the
  override (Linux + NVIDIA CUDA is the most common reason — upstream
  ships no Linux CUDA prebuilt). The override naturally suppresses the
  bundled-asset download because both `_vlm_status_rows` and the
  downloader check `binary_path().exists()`. See
  `docs/build-llama-server.md`.
- **llama.cpp release archive extraction must flatten `bin/`.** The
  release archives ship the binary plus its sister shared libraries
  (`libllama.so`, `libmtmd.so`, `libggml*.so`, …) under `build/bin/`.
  `llama-server`'s `RUNPATH` is `$ORIGIN`, so every `.so` must land in
  the same directory as the binary or it dies at startup with `error
  while loading shared libraries`. From `LLAMA_CPP_RELEASE = "b10456"`
  onward, Linux/macOS assets are `.tar.gz` (Windows stays `.zip`) —
  `setup_models.py` picks `_extract_flat_bin_targz` or
  `_extract_flat_bin_zip` off the URL's suffix in `_ensure_target`.
  Archives also include symlinked SONAME chains (`libllama.so` →
  `libllama.so.0` → `libllama.so.0.0.<build>`) — the zip path preserves
  them via `os.symlink` (zip stores the link target as the file content
  with `S_IFLNK` in `external_attr`), the tar path via
  `TarInfo.issym()`/`.linkname` (tar stores real symlink entries
  natively). Both extractors defer symlink creation until after their
  targets are written.
- **Local llama.cpp builds need explicit RPATH + flat output.**
  `cmake` by default places shared libs alongside their target's
  source dir (`build/tools/mtmd/libmtmd.so`, `build/src/libllama.so`,
  …) and does not bake `$ORIGIN`-relative `RUNPATH` into the build-tree
  binary. Either of those alone is enough to leave the binary unable
  to find its libs after we copy it. The build script forces output
  consolidation (`-DCMAKE_RUNTIME_OUTPUT_DIRECTORY=build/bin
  -DCMAKE_LIBRARY_OUTPUT_DIRECTORY=build/bin`) for both platforms, then
  applies a per-OS rpath strategy. **Linux / ELF:**
  `-DCMAKE_BUILD_RPATH_USE_ORIGIN=ON -DCMAKE_INSTALL_RPATH='$ORIGIN'`.
  **macOS / Mach-O:** `-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON
  -DCMAKE_INSTALL_RPATH='@loader_path'` plus a post-copy
  `install_name_tool -delete_rpath` / `-add_rpath '@loader_path'` /
  `codesign --force --sign -` pass on the binary and every dylib —
  cmake's ELF-only `BUILD_RPATH_USE_ORIGIN` flag is ignored on Mach-O,
  and `CMAKE_INSTALL_RPATH` only fires on `cmake --install` (we just
  `cp`), so without the post-process pass the binary keeps cmake's
  default absolute build-tree rpath and dies the moment the temp dir
  goes away. The verify step (`--version`) now runs **after**
  `rm -rf "${WORK_DIR}"` so any reliance on the build-tree rpath
  surfaces immediately rather than passing verify and failing on first
  user activation.
- **Qwen3.8 reasoning must stay disabled.** `qwen38-27b`'s `extra_args`
  disable thinking at server startup because llama.cpp grammar enforcement
  is inactive while thinking is enabled (ggml-org/llama.cpp#20345) — and
  every metascan VLM call is GBNF-constrained. Never remove those flags
  without moving all call sites off grammars. The model also requires
  llama.cpp >= ~b10450: older CUDA builds load it fine and silently emit
  corrupted tokens (Gated DeltaNet kernel bug). `LLAMA_CPP_RELEASE` is
  pinned accordingly; a stale `data/bin/local/llama-server` built from an
  older tag reproduces the garbage-output failure even with a correct pin.
- **Chat streaming is its own path.** `VlmClient.chat_stream` (used only
  by `backend/api/chat.py`, the Prompt Playground chat window) sends
  `stream: true`, takes a full multi-turn history, attaches the image to
  the first user message only, and sends no grammar. Keep
  `generate_text`'s request shape unchanged for the prompt / T2I / I2V /
  storyboard pipelines rather than folding chat into it.
- **Per-model llama-server flags live in `VlmModelSpec.extra_args`**, and
  the context budget in `VlmModelSpec.ctx_size` — never re-introduce
  model-id string matching in `vlm_client._build_command` or
  `startswith("qwen3vl-")` filters in selection code; use `mid in REGISTRY`.
- **Qwen3-VL pipeline DB writes must run in a worker thread.**
  Both `_run_retag_job` (`backend/api/vlm.py`) and `VlmTagPump.drain_once`
  (`backend/services/vlm_tag_pump.py`) wrap `db.add_tag_indices` with
  `asyncio.to_thread`. WSL2 `/mnt/<drive>` mounts and slow disks
  produce SQLite fsyncs of 50–100 ms+; running that on the event loop
  serializes concurrent tagging tasks and triggers the `heartbeat:
  event loop stalled` warnings.
- **Per-request VLM concurrency must match `parallel_slots`.** Both
  pipelines use a `Semaphore(spec.parallel_slots)` keyed off the
  registry entry for the active model id (`REGISTRY[mid].parallel_slots`).
  Going below it leaves GPU slots idle in `llama-server`'s
  `--parallel`; going above it forces the server to queue requests and
  removes the overlap benefit.
- **LoRA adapter serving (prompt-writer adapters).** `VlmClient(lora_paths=...)`
  adds one `--lora <adapter.gguf>` per adapter followed by a single
  `--lora-init-without-apply` (only when the list is non-empty, so a
  base-only command line is unchanged). The adapters are loaded but not
  applied; each request selects one with `lora: [{id: <position>, scale: 1}]`
  and the `lora` key is absent from base requests. The position is the index
  in the list the RUNNING server was spawned with (`_loaded_lora_paths`,
  snapshotted at `start()`), resolved together with the HTTP client right
  before the POST. Never compute it from the registry's current list.
  `VlmRoles` feeds the writer client the `ready`/`untested` adapters whose
  `adapters/<id>/adapter.gguf` exists and whose `base_model` equals the
  writer's model; a general-only client gets none. Adapter ids are
  `adapter:<id>` writer ids (`writer_adapters.adapter_writer_id`); a
  T2I adapter is only valid on T2I, an I2V one on I2V.
- **Restart only when idle.** A registry change (new, deleted, withdrawn or
  failed adapter) does not interrupt work: an idle stale client is evicted
  and respawns lazily on its next request (so the writer reads "stopped"
  until then); a busy one sets `restart_pending` and a background task
  restarts it when `busy_for_background` is false (nothing in flight or
  waiting, no `hold()`, not loading). `_ensure_lora` for an adapter the
  server does not yet hold waits (bounded by `lora_wait_s`, 60 s) for that
  idle moment and never preempts a Random batch's hold: past the wait it
  raises `AdapterLoadingError` -> 409 `adapter_loading` (retry later). A T2I
  Random batch calls `prepare_lora` BEFORE taking its hold. A withdrawn
  adapter the running server still holds keeps serving until the pending
  restart; one it no longer holds raises `AdapterNotLoadedError`.
  `WriterAdapterUnavailable` (gone, failed, still training, other kind, not
  served, other base model) -> 409 `adapter_unavailable`; the frontend falls
  back to Base. `DELETE /api/writers/adapters/{id}` withdraws first and
  answers 409 `adapter_files_busy` if a running server still maps the file.
  After any registry change call `refresh_writer_adapters()` BEFORE
  broadcasting `adapters_changed`.
- **Adapter prompts match the training prompts.** Adapter requests send no
  taste examples and no variant angle, and add the `CATEGORIES:` line only
  for a scope-`all` adapter (`t2i_prompt.add_categories_line`);
  `tests/test_writer_dataset_parity.py` guards that train and inference
  render the same user turn. Never log the prompts or examples.
- **The adapter GGUF comes from `convert_lora_to_gguf.py`** run by the
  training worker with `--base <HF dir>`; the worker verifies the GGUF (LoRA
  tensor count, no vision tensors) and fails the run otherwise. The
  converter is pinned to `LLAMA_CPP_RELEASE`; a `llama-server` built from a
  much different llama.cpp may not load the adapter (the converter row in
  Config → Training shows `release_mismatch`).

- **VLM status row requires the binary too.** `_vlm_status_rows` in
  `backend/api/models.py` only flips a row to `available` when GGUF +
  mmproj + `binary_path()` are all present. A partial download (weights
  on disk, binary missing) keeps the row at `missing` so the Download
  button stays enabled — `_ensure_target` short-circuits on existing
  files, so retrying only fetches the missing pieces. `size_bytes` is
  reported from whichever weight files exist regardless of binary
  status, so the user still sees partial-download progress.
- **VLM download stage label.** `_download_vlm` broadcasts
  `download_progress` with `stage="downloading (n/3)"` and
  `percent=0.0`. Don't put the GGUF filename in the stage — it's
  several characters longer than the chip can render. The frontend
  `statusLabel` only appends `${pct}%` when `percent > 0`, so keeping
  the percent at 0 avoids the misleading "33%" / "66%" suffix that
  reflects step count, not byte progress.
- **`VlmClient` stderr drainer logs at DEBUG, errors at WARNING.**
  llama-server stderr includes the entire chat-template dump on each
  load (~150 lines) plus per-request slot chatter. Routine lines go to
  DEBUG; lines containing `error`/`failed`/`fatal`/`abort` are
  promoted to WARNING. The 200-line ring buffer (`_stderr_ring`) is
  attached to crash reports by `_wait_exit` so debugging info still
  reaches the user on a real failure.

## Common tasks

### Adding a new VLM caption style
1. Add the style key to `CAPTION_STYLE_PROMPTS` in `metascan/core/vlm_prompts.py`.
2. The style picker in the (future) UI reads keys directly; backend doesn't need a registry change.
3. The style template should be deterministic, single-image, and produce parseable output if the consumer requires structured fields.
4. Wire it into `VlmClient.generate_caption(image_path, style)` once the future captioning feature lands.
