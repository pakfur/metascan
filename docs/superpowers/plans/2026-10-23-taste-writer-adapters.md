# Taste Learning — Project 4: Prompt-Writer Fine-Tunes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner fine-tune the 8B prompt writer on their own ratings, overnight on the 5090, producing named LoRA adapters (per category or "all") with blind scorecards, selectable in the T2I/I2V writer menus, deletable, and measured in the Writers dashboard against the `base` / `qwen3vl-8b` baseline.

**Architecture:** A dataset builder turns project 1–3 data into three example kinds (variant preference pairs, edit pairs, hindsight briefs). A one-shot training subprocess (optional heavy deps, imported only there) runs bf16 LoRA SFT then DPO on the HF-format base, checkpoints for pause/resume, then converts the adapter with llama.cpp's `convert_lora_to_gguf.py`. A training manager owns the single job, the GPU lock and progress (WS `training` channel). The writer-role llama-server loads every ready adapter with `--lora … --lora-init-without-apply` and each request picks one via the per-request `lora` field. A scorecard runs fixed developer-written briefs through base and adapter and hides the writer until rated.

**Tech Stack:** Python 3.11; training-only optional deps in `requirements-train.txt` (CUDA torch, transformers with Qwen3-VL support, peft, trl, accelerate, safetensors); llama.cpp (existing build script + its `convert_lora_to_gguf.py`); FastAPI, SQLite; Vue 3 / Pinia / TS.

**Spec:** `docs/superpowers/specs/2026-10-22-taste-learning-design.md` — project 4 (training window, category scope, model roles, scorecard, hyperparameters), "Success measure".

## Global Constraints

- Python 3.11 (`venv-311`); `make VENV_DIR=venv-311 quality test`; `cd frontend && npm run build`. Known pre-existing failure: `tests/test_t2i_api.py::TestAppWiring::test_create_app_registers_the_t2i_routes`.
- **Training deps are optional.** `requirements-train.txt` lists them; the FastAPI process never imports torch/transformers/peft/trl. Only `metascan/workers/writer_train_worker.py` (and its backend module) import them, lazily. Without them the feature is gated off with a clear reason.
- **Feature gate `writer_training`** (in `metascan/core/hardware.py` `feature_gates`): available only when a CUDA GPU with ≥ 24 GB VRAM is present, the training deps import in a subprocess probe, the HF base weights are downloaded, and the llama.cpp converter script is present. Each missing piece is reported separately. This Mac fails the gate — that is expected.
- **Base model:** Qwen3-VL-8B, the writer role's model only (never the 27B). HF training weights repo: `huihui-ai/Huihui-Qwen3-VL-8B-Instruct-abliterated` (the source of the installed `noctrex/...-GGUF`); store it on the `qwen3vl-8b` `VlmModelSpec` as `hf_train_repo`, overridable by `config.json` `models.writer_train_repo`. The implementer must confirm the repo id from the GGUF repo's model card before hard-coding it.
- **Method:** LoRA in bf16 (no 4-bit, no bitsandbytes), text model only — target `q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj` of the language model; vision tower frozen and untouched. Stage 1 SFT on hindsight + edit examples; stage 2 DPO on preference pairs (β = 0.1). Effort presets (shown resolved, read-only; overridable under Advanced within clamps):
  - Quick: rank 8, lr 1e-4, 1 epoch · Standard: rank 16, lr 1e-4, 2 epochs · Thorough: rank 32, lr 5e-5, 3 epochs.
  - Clamps: rank 4–64, lr 1e-6–5e-4, epochs 1–5. DPO uses its own learning rate (default 1e-5, same clamps), with the SFT adapter as the DPO reference. Max sequence length 2048 tokens (system + user + completion; the system prompt is never truncated — parity with inference).
- **Examples** (all built locally; never logged; preview only in the owner's UI):
  - *Preference pairs*: within a T2I `variant_group`, a variant whose images include a favorite vs a variant whose images were all seen-or-passed with none favorited → (system = the target model's `META_*` prompt as at inference, user = the caption turn, chosen, rejected). Pairs whose two prompts share `writer_id`+`writer_model` are fine; pairs involving `manual` are excluded.
  - *Edit pairs*: rendered prompt with `edited = 1` → chosen = edited text, rejected = draft (DPO) and chosen as an SFT target.
  - *Hindsight examples*: favorited generated images with a known prompt → user turn = a short brief written by the general VLM from the image (cached in `hindsight_briefs`), target = the prompt that produced the image; only target-model families known (reuse `taste_examples.matches_model_family`).
  - Category scope: `all` (each example's category names added to its user turn as `CATEGORIES: …`) or one category id (only examples whose image carries it). Holdout 10% of preference pairs (seeded) for the **holdout agreement** metric = fraction where the model's log-prob of chosen > rejected.
- **Readiness:** a scope is trainable with ≥ 100 preference pairs + edit pairs combined; the window says "Enough new data ✓" when ≥ 200 new pairs exist since the newest ready adapter of that scope.
- **GPU lock:** training starts only when no ComfyUI job is pending/running and no T2I/I2V/storyboard batch is active; it shuts down all VLM clients and the CLIP inference worker first. While training: T2I/I2V generate, Generate Prompt, storyboard synthesis, VLM tagging/retag, chat and similarity search return 409 `gpu_busy_training` (UI shows "GPU busy: training"); browsing and rating still work. The lock releases on completion, failure, pause or cancel.
- **Adapters:** files under `data/models/vlm/adapters/<id>/` (`adapter.gguf`, `adapter_config.json`, `meta.json`). Registry statuses `training`, `paused`, `untested`, `ready`, `failed`. Names `taste-<scope-slug>-v<N>` (scope slug `all` or the category name slugified), renameable. Delete removes files and row; generations keep `writer_id = adapter:<id>` and the remembered name.
- **Inference:** writer ids `adapter:<id>`; `writer_model = qwen3vl-8b`. The writer llama-server starts with `--lora <file>` for every `ready`/`untested` adapter plus `--lora-init-without-apply`; requests send `lora: [{"id": <position>, "scale": 1.0}]` for an adapter and nothing (unchanged request shape) for base. A registry change restarts the writer server only when it is idle (deferred otherwise).
- **Scorecard:** 40 developer-written briefs in `data/taste/scorecard_briefs.txt` (neutral, adult-safe scenes, no people's names, pass the T2I caption screen). A scorecard run renders every brief with base and with the adapter (same seeds), tagged `scorecard_id`; the viewer and T2I tiles hide writer/variant labels for scorecard images until each has a verdict. Result = favorite rate per writer over judged scorecard images + mean taste score as a proxy.
- Pause/resume via the trainer's checkpoints (every 50 steps); cancel deletes the run's checkpoints. Only one run trains at a time. Server restart during a run → run marked `paused` (resumable) at startup.
- No `PRAGMA user_version` bump (new tables/columns are additive). Never log prompts, briefs or examples at INFO+. Tests never need CUDA: the worker has a `fake` backend; the real backend has one test skipped without CUDA.

## Review Focus

1. **Lineage with adapters:** a T2I/I2V output written with adapter N records `writer_id = adapter:N`, `writer_model = qwen3vl-8b`; base requests send no `lora` field — tests in Task 2.
2. **GPU lock:** no generation, VLM or CLIP work can start while training holds the GPU, and training cannot start while ComfyUI is busy — tests in Task 6.
3. **Deleting an adapter in use** (selected in a dialog, or loaded in the writer server) → server restarts without it when idle; requests naming it get a clear 409/404; history rows keep their name — tests in Tasks 1–2.
4. **Pause / server restart / resume** continue from the last checkpoint and never double-register an adapter — tests in Tasks 5–6.
5. **Dataset correctness:** no `manual` writers in preference pairs, no scorecard images in training data, holdout pairs never trained on, category scope filters exactly — tests in Task 3.

---

### Task 1: Adapter registry, lineage ids and management API

**Files:** Create `metascan/core/writer_adapters.py`; modify `metascan/core/database_sqlite.py`, `metascan/core/judgments.py` (accept `adapter:<id>` writer ids for ready adapters), `backend/api/writers.py` (new), `backend/main.py`; tests `tests/test_writer_adapters.py`.

**Interfaces:**
- Tables: `writer_adapters(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE, scope TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('t2i','i2v')), base_model TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, data_cutoff TEXT, hyperparams TEXT NOT NULL, counts TEXT NOT NULL, holdout_agreement REAL, base_agreement REAL, file_path TEXT, error TEXT, deleted_at TEXT)`; `training_runs(id INTEGER PRIMARY KEY AUTOINCREMENT, adapter_id INTEGER NOT NULL, status TEXT NOT NULL, step INTEGER NOT NULL DEFAULT 0, total_steps INTEGER, checkpoint_dir TEXT, started_at TEXT, updated_at TEXT, log_path TEXT, error TEXT)`.
- `WRITER_ADAPTER_PREFIX = "adapter:"`, `adapter_writer_id(adapter_id: int) -> str`, `parse_adapter_writer_id(writer_id: str) -> Optional[int]`; `judgments.check_writer_model` / draft writer validation accept `adapter:<id>` only for adapters with status `ready`/`untested`, with `writer_model` = the adapter's `base_model`.
- DB: `create_writer_adapter(...) -> int`, `get_writer_adapter(id)`, `list_writer_adapters(include_deleted=False)`, `rename_writer_adapter(id, name)`, `set_writer_adapter_status(id, status, **fields)`, `soft_delete_writer_adapter(id)` (keeps the row for history names, sets `deleted_at`, status `deleted`), `next_adapter_name(scope_slug) -> str`.
- Routes: `GET /api/writers/adapters`, `PATCH /api/writers/adapters/{id}` `{name}`, `DELETE /api/writers/adapters/{id}` → `{status:"deleted"}` (removes files; 409 `adapter_training` while training). WS `training` event `adapters_changed {}`.
- Tests: registry CRUD + name uniqueness + `next_adapter_name` sequencing; writer-id parsing; a deleted adapter's id is rejected for new drafts but history rows keep the name in writer stats.

### Task 2: Serve adapters from the writer llama-server; writer menus

**Files:** modify `metascan/core/vlm_client.py` (spawn args + optional `lora` on `generate_text`), `backend/services/vlm_roles.py` (writer client built with the registry's adapter list; deferred restart on change), `metascan/core/t2i_runner.py`, `metascan/core/i2v_runner.py` (writer selection → lora id; lineage), `backend/api/t2i.py`, `backend/api/i2v.py` (`writer` accepts `adapter:<id>`), frontend `T2IDialog.vue`, `I2VDialog.vue`, `stores/t2i.ts`, `stores/i2v.ts`, `api/writers.ts` (new), `types/writers.ts` (new); tests `tests/test_writer_adapter_serving.py`.

**Interfaces:**
- `VlmClient(..., lora_paths: Sequence[Path] = ())` → command gains `--lora <p>` per path + `--lora-init-without-apply` when non-empty; `generate_text(..., lora_id: Optional[int] = None)` adds `"lora": [{"id": lora_id, "scale": 1.0}]` only when set (base requests byte-identical to today).
- `VlmRoles.writer_adapters() -> List[AdapterSlot(adapter_id, position, path)]`; `refresh_adapters()` rebuilds the writer client when idle, else marks `restart_pending` and applies after the writer's next idle period.
- Menus: writer select lists `Base`, `Base + taste` (T2I), and each ready/untested adapter of matching kind whose scope is `all` or equals one of the dialog's selected categories, labelled `name · date · fav %` (from writer stats) with an `untested` badge; an adapter whose per-target-model example count for the selected T2I model is < 50 is shown disabled with a tooltip. Selecting an adapter that disappears falls back to Base with a toast.
- Tests (fake llama-server records request bodies): base request has no `lora` key; adapter request has the right position; spawn command lists adapters; deleting an adapter while a request is in flight defers the restart; writer lineage `adapter:<id>` recorded for T2I Random/Manual and I2V.

### Task 3: Training dataset builder

**Files:** create `metascan/core/writer_dataset.py`; modify `metascan/core/database_sqlite.py` (read helpers); tests `tests/test_writer_dataset.py`.

**Interfaces:**
- `@dataclass DatasetCounts(pref_pairs, edit_pairs, hindsight, by_target_model: Dict[str, int], new_since_last: int, date_span: Tuple[str, str])`.
- `build_examples(db, scope: str, store: PromptStore, now) -> Tuple[List[SftExample], List[PrefExample], DatasetCounts]` — the exact rules in Global Constraints; excludes `manual` writers, scorecard images (`scorecard_id` not null), videos; uses the same `compose_*` system prompts as inference.
- `split_holdout(prefs, fraction=0.1, seed=0)`; `write_jsonl(examples, path)`; `preview(examples, n=10) -> List[dict]` (for the window; never logged).
- `readiness(counts, last_adapter_cutoff) -> {"trainable": bool, "enough_new": bool, "missing": int}`.
- Tests with synthetic DB rows: a variant group with one favorited variant and one seen-not-favorited variant yields one pair in the right order; manual-writer and scorecard rows excluded; category scope filters; edit pairs; holdout disjoint; counts per target model.

### Task 4: Hindsight briefs job

**Files:** create `backend/services/hindsight_briefs.py`; modify `metascan/core/database_sqlite.py` (`hindsight_briefs(file_path TEXT PRIMARY KEY REFERENCES media(file_path) ON DELETE CASCADE, brief TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL)`), `data/meta_prompt.yml` (`HINDSIGHT_BRIEF_SYSTEM` — "describe what this image shows in one short sentence a person might type as a request; no style words"), `backend/api/writers.py` (`POST /api/writers/briefs/run`, status); tests `tests/test_hindsight_briefs.py`.

- Background job using the **general** role in background mode (never evicts the writer, waits while ComfyUI generates, respects explicit unloads), one image per request, resumable (skips cached), cancellable, progress on WS `training` (`briefs_progress {done,total}`); only favorited generated images with a known prompt and target family. Briefs ≤ 200 chars. Tests with a fake VLM.

### Task 5: Training worker (subprocess)

**Files:** create `metascan/workers/writer_train_worker.py`, `metascan/workers/writer_train_backend.py` (real TRL/PEFT backend, lazy imports), `metascan/workers/writer_train_fake.py`, `requirements-train.txt`; tests `tests/test_writer_train_worker.py` (+ one `@pytest.mark.skipif(no CUDA)` real smoke).

**Interfaces:**
- CLI `python -m metascan.workers.writer_train_worker <task.json>`; task `{backend: "real"|"fake", base_dir, sft_path, pref_path, holdout_path, out_dir, checkpoint_dir, resume: bool, hyperparams, converter: {python, script}, control_dir}`; progress `<out_dir>/progress.json` `{stage: "sft"|"dpo"|"eval"|"convert", step, total_steps, loss, holdout_agreement, base_agreement, eta_s}` written atomically every few steps; control files `pause.signal` / `cancel.signal` checked between steps (pause → save checkpoint, exit code 3; cancel → exit code 4); result `<out_dir>/result.json` `{status, adapter_dir, gguf_path, holdout_agreement, base_agreement, error}`.
- Real backend: load `Qwen3VLForConditionalGeneration` (or the text model via AutoModelForCausalLM if supported) in bf16 on CUDA; freeze vision; PEFT LoRA on the listed modules; SFT then DPO with TRL; checkpoint every 50 steps; resume from the latest; eval agreement on holdout (adapter vs base via `disable_adapter()`); save PEFT dir; run `convert_lora_to_gguf.py --base <base_dir> --outtype f16 --outfile <gguf> <peft_dir>`.
- Fake backend: deterministic steps, honours pause/cancel/resume and writes a dummy `.gguf`.
- Tests: fake run end-to-end; pause → resume continues from the saved step; cancel cleans checkpoints; malformed task → error result, exit 1; stderr drained by the manager (Task 6).

### Task 6: Training manager, API, GPU lock

**Files:** create `backend/services/training_manager.py`; modify `backend/api/writers.py`, `backend/main.py` (lifespan: construct, mark interrupted runs `paused`, shutdown cancels the subprocess cleanly), every GPU consumer route/runner for the 409 (`t2i`, `i2v`, `storyboard`, `vlm`, `chat`, `similarity`, tag pump), `metascan/core/hardware.py` (`writer_training` gate); tests `tests/test_training_manager.py`.

**Interfaces:**
- `TrainingManager.start(scope, effort, overrides, start_from: "fresh"|adapter_id, render_scorecard: bool) -> run_id` (409 `gpu_busy` if ComfyUI/batches active, 409 `already_training`, 409 `not_ready` with readiness, 409 `gate_unavailable` with reasons); `pause()`, `resume(run_id)`, `cancel()`; `status()`; `gpu_locked() -> bool` consulted by consumers.
- Routes: `GET /api/writers/training` (status + dataset counts + readiness + gate + resolved hyperparams per preset), `POST /api/writers/training/start|pause|resume|cancel`, `GET /api/writers/dataset/preview?scope=`. WS `training`: `run_started`, `run_progress`, `run_paused`, `run_completed {adapter_id}`, `run_failed {error}`, `adapters_changed`, `briefs_progress`.
- On completion: register adapter `untested`, `refresh_adapters()`, optionally start the scorecard (Task 9).
- Tests (fake backend): lock blocks each consumer with 409 `gpu_busy_training` and releases after; start refused while a fake ComfyUI job is pending; restart marks running → paused and resume works; stderr drained; exactly one adapter registered per completed run.

### Task 7: Base weights and converter in the Models tab

**Files:** modify `backend/api/models.py` (status rows: "Writer training base (HF, ~16 GB)" download via the existing HF download flow into `data/models/vlm/train/<repo>/`; "llama.cpp LoRA converter" present/missing with the build-script hint), `metascan/core/vlm_models.py` (`hf_train_repo`), frontend `ConfigModelsTab.vue`; tests extend `tests/test_models_vlm_rows.py`.

### Task 8: Training window UI

**Files:** create `frontend/src/components/dialogs/WriterTrainingDialog.vue`, `frontend/src/stores/training.ts`, `frontend/src/api/training.ts`; modify `ConfigModelsTab.vue` ("Train prompt writer" button + adapter list), the app header (training chip), `App.vue` (always-on `training` WS bridge).
- Implements the approved mockup (`.superpowers/brainstorm/6418-1792679425/content/training-window.html`): closable window; dataset panel (counts per kind with "+N new", by target model, date span, holdout %, readiness, Preview 10 examples); run panel (name, scope = All or a category, Effort preset with resolved values read-only + Advanced overrides with clamps and a warning, Start from Fresh / Continue <adapter>, "render scorecard after training" checkbox, progress bar, stage, step, holdout agreement vs base, loss sparkline, time since checkpoint); controls Start / Pause / Resume / Cancel (Pause and Cancel confirm); gate reasons when unavailable; adapter list with status, scope, hyperparams, scorecard fav %, rename, delete, resume. Header chip `◐ Training <name> · <pct>% · ~<eta>` reopens the window; consumers show "GPU busy: training".
- Verify with `npm run build` + an isolated-worktree live check driving the fake backend.

### Task 9: Scorecard

**Files:** create `data/taste/scorecard_briefs.txt` (40 briefs), `backend/services/scorecard.py`; modify `metascan/core/t2i_runner.py` (a `scorecard_id` on batches → stored on `t2i_images.scorecard_id`), `metascan/core/database_sqlite.py`, `backend/api/writers.py` (`POST /api/writers/adapters/{id}/scorecard`, results), `MediaViewer.vue` / `ThumbTile.vue` (hide writer/variant labels for scorecard images until judged), Writers dashboard (scorecard rows); tests `tests/test_scorecard.py`.
- Run: for each brief, one Manual-style T2I step with the base writer and one with the adapter, same seeds, `variants = 1`, krea2 target by default (selectable); `scorecard_id` + `writer_id` recorded; result `{adapter_id, n_judged, fav_rate_base, fav_rate_adapter, mean_score_base, mean_score_adapter}`; adapter status `untested → ready` once ≥ 30 pairs are judged.
- Briefs file loads through the T2I caption screen; a test asserts every brief passes it.

### Task 10: Docs

**Files:** `docs/writer-training.md` (new, linked from README's Documentation list): Windows native setup (CUDA torch wheel, `pip install -r requirements-train.txt`), WSL2 alternative, downloading the base weights, building llama.cpp with the converter, expected run times, what the scorecard means; CLAUDE.md bullets (training worker contract, GPU lock, adapter serving, never log examples); `.claude/rules/vlm-llama.md` (LoRA serving rules).
