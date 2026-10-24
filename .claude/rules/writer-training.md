---
paths:
  - "backend/services/training_manager.py"
  - "backend/services/writer_training_assets.py"
  - "backend/services/scorecard.py"
  - "backend/services/hindsight_briefs.py"
  - "backend/services/gpu_lock.py"
  - "backend/api/writers.py"
  - "metascan/core/gpu_guard.py"
  - "metascan/core/writer_dataset.py"
  - "metascan/core/writer_adapters.py"
  - "metascan/core/scorecard_blind.py"
  - "metascan/workers/writer_train_*.py"
  - "requirements-train.txt"
  - "data/taste/scorecard_briefs.txt"
  - "frontend/src/stores/training.ts"
  - "frontend/src/api/training.ts"
  - "frontend/src/types/training.ts"
  - "frontend/src/components/dialogs/WriterTrainingDialog.vue"
  - "tests/test_training_manager.py"
  - "tests/test_writer_*.py"
  - "tests/test_scorecard.py"
  - "tests/test_hindsight_briefs.py"
---

# Prompt-writer LoRA training

Moved out of the root CLAUDE.md so it loads only when you work with the files above;
the root CLAUDE.md still applies. LoRA serving is in `vlm-llama.md`; user guide is
`docs/writer-training.md`.

- **Worker contract.** `metascan/workers/writer_train_worker.py` is stdlib-only
  (importable by the server; a test proves no torch/transformers/peft/trl/datasets
  load). Heavy imports live in `writer_train_backend.py` (`RealBackend`, lazy
  `import_deps()`); `writer_train_fake.py` is the deterministic `FakeBackend` all server
  tests use (`backend="fake"`). Task file keys: `backend`, `base_dir`, `sft_path`,
  `pref_path`, `holdout_path`, `out_dir`, `checkpoint_dir`, `control_dir`, `data_root`,
  `resume`, `hyperparams`, `converter {python, script}`, `init_adapter_dir`, `meta`.
  `out_dir` / `checkpoint_dir` must be strictly inside `data_root`; every delete goes
  through `safe_rmtree`. Hyperparams outside `CLAMPS` are an error in the worker (the
  manager clamps first); an unknown key is an error. The worker writes `progress.json`,
  `result.json` (all keys always present) and `meta.json` atomically (tmp + `os.replace`);
  `step`/`total_steps` are overall (SFT + DPO). Drain BOTH stdout and stderr of the
  subprocess. Empty `control_dir` before every spawn. Resume needs the same JSONL files
  and hyperparams (sha256 fingerprint) or it errors.
- **Training recipe (do not change casually).** SFT with `completion_only_loss`, then
  DPO whose reference is the SFT adapter loaded a second time (`ref_adapter_name`), not
  the bare base; `dpo_lr` default 1e-5 (SFT `lr` is the preset's). Presets: quick
  r8 / 1e-4 / 1 epoch, standard r16 / 1e-4 / 2, thorough r32 / 5e-5 / 3; clamps rank
  4-64, lr 1e-6..5e-4, epochs 1-5. `MAX_SEQ_LEN = 2048`, system + user + completion,
  and over-long examples are DROPPED, never truncated (counted in `dropped_too_long`).
  LoRA targets only `language_model` projections (regex); a vision-tower key in the
  adapter or GGUF fails the run. bf16 only, no 4-bit. Checkpoints every 50 steps.
  `requirements-train.txt` pins `trl>=0.25.0,<0.26`; before widening, check
  `DPOConfig` still has `model_adapter_name` / `ref_adapter_name`
  (`writer_train_probe.probe_trl_reference_support` runs the same check for the gate).
- **Data rules (`writer_dataset.py`).** Three example kinds (variant preference pairs,
  edit pairs -> DPO + SFT, hindsight SFT). Images only. Scorecard images
  (`t2i_images.scorecard_id` not NULL), `manual` and (as a target) `fallback` writers
  are excluded; deleted generations are read from `media_tombstones`. Trainable at
  `MIN_TRAINABLE_PAIRS = 100`, "enough new" at `ENOUGH_NEW_PAIRS = 200` counted since
  the newest `ready` OR `untested` adapter of the scope. The holdout is a stable
  sha256 rank of `{seed}:{group}` (about 10% of groups; a group never changes sides as
  data grows; with 2+ groups at least one is held and never all) and is removed from
  SFT too. The user turn must stay identical to inference (parity
  test). Examples and briefs are NEVER logged (counts and exception types only; for a non-`TrainWorkerError` the worker's `result.json`
  `error` holds the type name only); the
  Preview endpoint (`GET /api/writers/dataset/preview`) is the only egress.
- **Run lifecycle.** `start` checks, in order: `already_training`, hyperparams
  (422), `gate_unavailable` / `gate_checking` (409), `unknown_scope`, `start_from`,
  `not_ready`, then takes the GPU lock and the second `gpu_busy` check. The adapter
  row (`training`) and run row are created at start; on success a single compare-and-set
  (`db.complete_training_run`) marks the run `completed` and the adapter `untested`
  with `file_path`, agreements and `data_cutoff`, then the manager calls
  `_refresh_writer()` (`refresh_writer_adapters()`) BEFORE the GPU lock is dropped, then
  emits `adapters_changed`, then `run_completed`, then the `on_completed` hook
  (scorecard; skipped while the manager is closing). "Continue from" copies the source
  PEFT dir into `<run_dir>/init_adapter` (`INIT_ADAPTER_DIRNAME`; `init_adapter_dir`
  points at the copy), so deleting the source never breaks resume. A start or resume
  that fails after the spawn stops the worker (`_stop_spawned`: terminate, wait, kill,
  reap) BEFORE releasing the lock. Exit 3 -> run+adapter `paused`; 4 -> `cancelled`
  (adapter soft-deleted, files removed); anything else -> `failed`, checkpoints kept,
  resumable. `recover()` at startup turns `running` runs into `paused`. The pid file
  `worker.pid` (`pid`, psutil `create_time`, module) identifies an orphan: it is
  stopped only if PID alive, start time within 1 s and cmdline contains
  `writer_train_worker`; an alive-but-unreadable one is `worker_unverifiable` (resume
  409, never killed) and a verified live one is `worker_alive`. Deleting a paused or
  failed adapter discards its run under `manager.op_lock` (delete/resume are serialized
  and `resume` re-reads after taking the GPU: 409 `run_discarded`). Adapter names are
  `taste-<scope>-v<N>`; numbers come from the max over `writer_adapters.generated_name`
  (deleted rows included) and are never reused, a user rename never moves the sequence.
- **GPU lock.** `gpu_guard.set_gpu_reservation(probe)` is pointed at
  `TrainingManager.gpu_locked` by the lifespan; `gpu_reserved()` fails CLOSED if the
  probe raises. Take it FIRST (so new requests already 409), check `gpu_busy()`
  (`comfy_jobs`, `t2i_batch`, `storyboard`, `index_build`, `upscale_active`,
  `scorecard`), run `release_gpu()` (cancel retag jobs and the briefs job, unload every
  VLM, shut down CLIP), check busy again; any failure resets the lock. Release only
  through `_drop_lock()`, which restarts an interrupted briefs job (not while closing).
  Every GPU consumer route has `Depends(require_gpu_free)` (`GPU_FREE`), and the core
  client entry points (`VlmClient.start`, `InferenceClient.start`, `ComfyClient.submit`
  / `submit_now` and its pump, `_start_index_build`, `ProcessUpscaleQueue.start_processing`)
  re-check under the lock. A submit that lands exactly as the lock is taken fails with
  `GpuBusyError` (-> 409) rather than being held. `tests/test_training_manager.py`
  enumerates the consumer routes; add a new GPU route to its list. Not locked: browsing,
  rating, judgments, the taste scorer (CPU), `/api/vlm/unload`. Shutdown closes the
  training manager first (a completion refreshes the writer), then the scorecard.
  The lifespan installs training (`recover()`) right after the T2I runner, before any
  CLIP / FAISS / VLM preload.
- **The gate** (`hardware.writer_training_reasons`) lists one reason per missing
  piece: CUDA, a 32 GB card (`WRITER_TRAINING_MIN_VRAM_GB`, at max_seq 2048 with DPO's
  three passes; compared against `WRITER_TRAINING_VRAM_FLOOR_GB` = 30 because torch
  reports GiB and a 5090 shows 31.8), training deps, the TRL reference probe (skipped when the deps are
  missing), base weights (every shard listed by the index), converter script. The
  deps/TRL probe runs in a single-flight background subprocess per interpreter
  (`gate.checking`); definitive answers are cached for the process, probe failures for
  60 s; `GET /api/writers/training?recheck=1` clears the cache. `/api/models/status`
  uses `probe=False` ("not checked yet") and never spawns. `GET .../training?dataset=0`
  is the light status (no dataset build, no `next_name`) the app uses on every WS
  (re)connect; the dataset summary is single-flight per scope with a 30 s cache.
- **Assets.** `writer_training_assets.asset_paths(config, data_dir)` is the one path
  resolver (base `data/models/vlm/train/<org>--<name>/`, converter
  `<models.llama_cpp_dir or data/llama.cpp>/convert_lora_to_gguf.py`, interpreter
  `models.writer_train_python` or `sys.executable`); see `hardware.md` for the rows.
- **Scorecard (`scorecard.py`, `scorecard_blind.py`).** 40 briefs from
  `data/taste/scorecard_briefs.txt`, each screened by `caption_reject_reason` (adult-only
  invariant; do not add a line that fails it). Per brief, base and `adapter:<id>` write
  one prompt each with the SAME seed (`brief_seed(scorecard_id, i)`; order from
  `adapter_first`), no angle, no taste examples; then one Manual batch per brief with
  two supplied `prompts[]`. All prompts are written, the VLM is unloaded, then batches
  render one at a time. **Judged = an explicit favorite or pass on both images** (seen
  alone is NOT judged and does not lift the blind); 30 judged pairs promote `untested`
  -> `ready` through a single CAS (`db.promote_writer_adapter`), after which the adapter
  stays ready. Blinding is server-side: while an image is blind, `GET /api/media/{path}`,
  `/api/t2i/images`, the PATCH answer and the scorecard `batch_step` / batches payloads
  drop prompt, tags, writer, variant and lineage (`scorecard_blind.py` helpers; tests
  assert no writer or prompt text appears anywhere in the JSON). `scorecard_batches` and
  `scorecard_jobs` keep the scorecard id, writer and variant for jobs that ingest after a
  restart. Scorecard batches are never adopted by the T2I dialog or "Cancel all";
  `BatchRequest.scorecard_id` is internal. Scorecard images are normal taste labels but
  never writer training data nor writer-stats rows (`generation_outcomes` skips them,
  tombstones included). A pair is one per `(scorecard, brief)`: the latest complete
  batch. A resume skips briefs whose job is still queued or running (409
  `scorecard_pending` when nothing else is left); a start answers 503 `shutting_down`
  while the app is closing. A scorecard is not auto-resumed after a restart
  (`interrupted`), and it needs a `t2i.model_workflows` preset for the target model
  (default `krea2`; else 409 `no_workflow`).
- **Hindsight briefs.** `HindsightBriefsJob` (general VLM role, one image per request,
  resumable, cancellable, one at a time) writes the one-sentence briefs. Its selection
  reuses `writer_dataset._hindsight_target` so it cannot drift from the builder.
  `job.blocked` is set to `TrainingManager.gpu_locked`: it pauses between images and
  refuses a start with 409 `gpu_busy_training`. Never refuse training because the job
  says "running"; the lock cancels it and restarts it afterwards.
- **Frontend.** The training store keeps client-side state only for the loss
  sparkline; everything else refetches. Map every refusal code through
  `trainingErrorText`, and GPU-busy 409s through `utils/gpuBusy.ts` (`GPU_BUSY_TEXT`).
  The window is a non-modal floating panel (z-index above the Config overlay); closing
  it never stops a run. It steps aside (`v-show`) while any media viewer is mounted
  (`composables/useViewerPresence.ts`: MediaViewer, MobileMediaViewer and
  SlideshowViewer call `useRegisterViewer()`) — the T2I / I2V viewers sit inside a
  z-index 900 overlay, below the window — and its Escape handler acts only with no
  viewer open and focus inside the window (or on body), so Esc and the rating keys
  belong to the viewer. Writer menu rates (T2I / I2V dialogs) are the blind scorecard
  rates from `GET /api/writers/scorecards` (`training.scorecards`), never writer-stats:
  `types/writers.ts::favText` / `judgedPairs` format them for both the menu and the
  window. After a WS reconnect the app does one light status fetch
  (`onWsConnect`).
